"""Local dashboard with a native simulator or a real MQTT device connection."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid

from scripts.build_simulator import ROOT, build
from app.mqtt_config import DEFAULT_CONFIG, MqttConfig, load_config
from app.mqtt_transport import MqttTransport, validate_telemetry

STATIC = ROOT / "app/static"


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def number(value, low: float, high: float, *, integer: bool = False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high
            or (integer and int(value) != value)):
        raise ValueError(f"Informe {'um inteiro' if integer else 'um número'} entre {low} e {high}.")
    return int(value) if integer else float(value)


class Application:
    def __init__(self, database: Path, *, background: bool = True,
                 mode: str = "local_simulator", mqtt_config: MqttConfig | None = None,
                 transport_factory=MqttTransport, command_timeout: float = 8):
        if mode not in {"local_simulator", "mqtt"}:
            raise ValueError("Modo de execução inválido.")
        self.mode = mode
        self.mqtt_config = mqtt_config if mode == "mqtt" else None
        if mode == "mqtt" and mqtt_config is None:
            raise ValueError("Informe a configuração MQTT.")
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.process = None
        self.transport = None
        self.command_timeout = command_timeout
        self.pending = {}
        self.broker_connected = False
        self.device_available = True
        self.history_warning = None
        self.remote_sequence = 0
        self.remote_event_sequence = 0
        self.retired_sessions = set()
        self.error = None
        self.latest = None
        self.received_at = None
        self.received_monotonic = 0.0
        self.last_reading_saved = -math.inf
        self.started = time.monotonic()
        self.session = "local-" + uuid.uuid4().hex if mode == "local_simulator" else None
        database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                received_at TEXT NOT NULL, kind TEXT NOT NULL, delta INTEGER NOT NULL,
                quantity INTEGER NOT NULL, UNIQUE(session_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY, received_at TEXT NOT NULL, session_id TEXT NOT NULL,
                gross_g REAL, net_g REAL, quantity INTEGER, stable INTEGER NOT NULL,
                valid INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS commands (
                command_id TEXT PRIMARY KEY, received_at TEXT NOT NULL,
                fingerprint TEXT NOT NULL, acknowledgement TEXT NOT NULL
            );
        """)
        self.simulation = self._load("simulation", {"mass_g": 100.0, "fault": False, "connected": True})
        if mode == "mqtt":
            # An interrupted request must not be executed again on server restart.
            for row in self.db.execute("SELECT command_id, acknowledgement FROM commands").fetchall():
                ack = json.loads(row["acknowledgement"])
                if ack.get("status") == "pending":
                    ack.update(status="unknown", ok=False, message="Servidor reiniciado antes da confirmação. Verifique o estado do dispositivo.")
                    self.db.execute("UPDATE commands SET acknowledgement = ? WHERE command_id = ?", (json.dumps(ack), row["command_id"]))
            self.db.commit()
            try:
                self.transport = transport_factory(mqtt_config, self.receive_mqtt, self.mqtt_connection)
                if background:
                    self.transport.start()
            except Exception:
                self.db.close()
                raise
            return
        # A paused previous session must not make a fresh launch appear broken.
        self.simulation["connected"] = True
        self.process = subprocess.Popen(
            [str(build()), self.session], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        stored = self._load("settings", None)
        if stored:
            values = [stored["unit_g"], stored["minimum"], stored["grams_per_raw"],
                      stored["tare_raw"], int(stored["tare_set"]), int(stored["calibrated"])]
            restored = self._exchange("restore " + " ".join(map(str, values)))
            if not restored["ok"]:
                self.close()
                raise ValueError("Não foi possível restaurar a configuração persistida.")
        self.tick()
        if background:
            self.thread = threading.Thread(target=self._run, daemon=True, name="local-device")
            self.thread.start()

    def _load(self, key, default):
        row = self.db.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _save(self, key, value):
        self.db.execute("INSERT INTO metadata VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(value, allow_nan=False)))

    def _exchange(self, line):
        if self.process.poll() is not None:
            raise RuntimeError("O processo do simulador local foi encerrado.")
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        response = self.process.stdout.readline()
        if not response:
            raise RuntimeError("O simulador local parou de responder.")
        return json.loads(response)

    def _accept(self, telemetry):
        self.latest = telemetry
        self.received_at = timestamp()
        self.received_monotonic = time.monotonic()
        event = telemetry["event"]
        if event:
            self.db.execute("INSERT OR IGNORE INTO events (session_id, sequence, received_at, kind, delta, quantity) VALUES (?, ?, ?, ?, ?, ?)",
                            (self.storage_session(), event["sequence"], self.received_at,
                             event["kind"], event["delta"], event["quantity"]))
        if self.received_monotonic - self.last_reading_saved >= 1.0:
            self.db.execute("INSERT INTO readings (received_at, session_id, gross_g, net_g, quantity, stable, valid) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (self.received_at, self.storage_session(), telemetry["gross_g"],
                             telemetry["net_g"], telemetry["quantity"], telemetry["stable"], telemetry["valid"]))
            self.last_reading_saved = self.received_monotonic
        key = "settings" if self.mode == "local_simulator" else "settings:" + self.mqtt_config.topic
        if self._load(key, None) != telemetry["settings"]:
            self._save(key, telemetry["settings"])
        self.db.commit()

    def storage_session(self):
        if self.mode == "mqtt":
            return f"mqtt:{self.mqtt_config.topic}:{self.session}"
        return self.session

    def connected(self):
        transport_ok = self.broker_connected and self.device_available if self.mode == "mqtt" else self.simulation["connected"]
        return bool(transport_ok and self.latest and not self.error
                    and time.monotonic() - self.received_monotonic < (5 if self.mode == "mqtt" else 3))

    def mqtt_connection(self, connected, error):
        with self.lock:
            if self.stop.is_set():
                return
            self.broker_connected = connected
            self.error = error
            if not connected:
                # A new live reading is needed after reconnecting to the broker.
                self.received_monotonic = 0

    def receive_mqtt(self, kind, message):
        with self.lock:
            if self.stop.is_set():
                return
            if kind == "telemetry":
                validate_telemetry(message, self.mqtt_config.device_id)
                session = message["session_id"]
                if session in self.retired_sessions:
                    return
                if session != self.session:
                    if self.session:
                        self.retired_sessions.add(self.session)
                        for pending in self.pending.values():
                            pending["response"] = {"type": "ack", "ok": False, "status": "device_restarted",
                                                   "message": "O ESP32 reiniciou antes da confirmação. Verifique a medição."}
                            pending["event"].set()
                    self.session = session
                    self.remote_sequence = 0
                    self.remote_event_sequence = self.db.execute(
                        "SELECT COALESCE(MAX(sequence), 0) FROM events WHERE session_id = ?", (self.storage_session(),)
                    ).fetchone()[0]
                    self.started = time.monotonic()
                    self.last_reading_saved = -math.inf
                if message["sequence"] <= self.remote_sequence:
                    return
                self.remote_sequence = message["sequence"]
                event = message.get("event")
                if event:
                    if event["sequence"] > self.remote_event_sequence + 1:
                        self.history_warning = "Há movimentações anteriores sem registro nesta sessão. O histórico recebido pode estar incompleto."
                    self.remote_event_sequence = max(self.remote_event_sequence, event["sequence"])
                self.device_available = True
                self._accept(message)
            elif kind == "ack":
                if message.get("type") != "ack" or message.get("device_id") != self.mqtt_config.device_id or message.get("session_id") != self.session:
                    return
                command_id = message.get("command_id")
                if not isinstance(command_id, str):
                    return
                pending = self.pending.get(command_id)
                if pending and pending["session"] == self.session and type(message.get("ok")) is bool and isinstance(message.get("message"), str):
                    pending["response"] = {**message, "target": "wokwi", "status": "confirmed" if message["ok"] else "rejected"}
                    pending["event"].set()
            elif kind == "availability":
                if message.get("device_id") == self.mqtt_config.device_id and message.get("session_id") == self.session and message.get("online") is False:
                    self.device_available = False

    def tick(self, *, milliseconds=None):
        with self.lock:
            if self.mode != "local_simulator":
                return
            if not self.simulation["connected"]:
                return
            now_ms = int((time.monotonic() - self.started) * 1000) if milliseconds is None else milliseconds
            raw = math.floor(self.simulation["mass_g"] * 4095 / 5000 + 0.5)
            message = self._exchange(f"sample {now_ms % 2**32} {raw} {int(not self.simulation['fault'])}")
            self._accept(message)

    def _run(self):
        while not self.stop.wait(0.1):
            try:
                self.tick()
            except (OSError, ValueError, RuntimeError) as error:
                with self.lock:
                    self.error = str(error)
                return

    def change_simulation(self, body):
        with self.lock:
            if self.mode != "local_simulator":
                raise ValueError("No modo MQTT, altere a massa pelo potenciômetro no Wokwi.")
            if not body or set(body) - {"mass_g", "fault", "connected"}:
                raise ValueError("Informe massa, falha ou conexão do simulador.")
            updated = dict(self.simulation)
            if "mass_g" in body:
                updated["mass_g"] = number(body["mass_g"], 0, 5000)
            for field in ("fault", "connected"):
                if field in body:
                    if not isinstance(body[field], bool):
                        raise ValueError("Falha e conexão devem ser valores booleanos.")
                    updated[field] = body[field]
            self.simulation = updated
            self._save("simulation", updated)
            self.db.commit()
            self.tick()
            return updated

    def execute(self, body):
        if self.mode == "mqtt":
            return self.execute_remote(body)
        with self.lock:
            action = body.get("command")
            command_id = body.get("command_id", uuid.uuid4().hex)
            if not isinstance(command_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", command_id):
                raise ValueError("Identificador de comando inválido.")
            if action not in ("tare", "unit", "minimum", "calibrate", "sample_unit"):
                raise ValueError("Comando desconhecido.")
            value = body.get("value")
            if action in ("unit", "calibrate"):
                value = number(value, 0.01, 5000)
            elif action in ("minimum", "sample_unit"):
                value = number(value, 1 if action == "sample_unit" else 0, 1000000, integer=True)
            fingerprint = json.dumps([action, value], allow_nan=False)
            previous = self.db.execute("SELECT fingerprint, acknowledgement FROM commands WHERE command_id = ?", (command_id,)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ValueError("Esse identificador já foi usado para outro comando.")
                return json.loads(previous["acknowledgement"])
            if not self.simulation["connected"] or self.error:
                raise ValueError("O simulador está desconectado. Reconecte antes de enviar comandos.")
            line = f"{action} {command_id}" + ("" if action == "tare" else f" {value}")
            acknowledgement = self._exchange(line)
            acknowledgement["target"] = "local_simulator"
            self.db.execute("INSERT INTO commands VALUES (?, ?, ?, ?)",
                            (command_id, timestamp(), fingerprint, json.dumps(acknowledgement)))
            self._accept(self._exchange("snapshot"))
            return acknowledgement

    def execute_remote(self, body):
        action = body.get("command")
        command_id = body.get("command_id", uuid.uuid4().hex)
        if not isinstance(command_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", command_id):
            raise ValueError("Identificador de comando inválido.")
        if action not in ("tare", "unit", "minimum", "calibrate", "sample_unit"):
            raise ValueError("Comando desconhecido.")
        value = body.get("value")
        if action in ("unit", "calibrate"):
            value = number(value, 0.01, 5000)
        elif action in ("minimum", "sample_unit"):
            value = number(value, 1 if action == "sample_unit" else 0, 1000000, integer=True)
        fingerprint = json.dumps(["mqtt", self.mqtt_config.topic, action, value], allow_nan=False)
        with self.lock:
            previous = self.db.execute("SELECT fingerprint, acknowledgement FROM commands WHERE command_id = ?", (command_id,)).fetchone()
            if previous and previous["fingerprint"] != fingerprint:
                raise ValueError("Esse identificador já foi usado para outro comando.")
            pending = self.pending.get(command_id)
            if previous and not pending:
                return json.loads(previous["acknowledgement"])
            if not pending:
                if not self.connected():
                    raise ValueError("Aguarde o ESP32 conectar e enviar uma leitura recente antes de enviar comandos.")
                pending = {"event": threading.Event(), "response": None, "session": self.session}
                self.pending[command_id] = pending
                initial = {"type": "ack", "command_id": command_id, "ok": False, "target": "wokwi",
                           "status": "pending", "message": "Aguardando confirmação do ESP32."}
                self.db.execute("INSERT INTO commands VALUES (?, ?, ?, ?)",
                                (command_id, timestamp(), fingerprint, json.dumps(initial)))
                self.db.commit()
                # Format floating-point numbers without locale or scientific-notation surprises.
                line = f"{action} {command_id}" + ("" if action == "tare" else " " + format(value, ".12g"))
                try:
                    self.transport.publish_command(self.session, line)
                except (RuntimeError, OSError) as error:
                    pending["response"] = {**initial, "status": "publish_failed", "message": str(error)}
                    pending["event"].set()
        # MQTT callbacks need the lock to deliver the acknowledgement.
        confirmed = pending["event"].wait(self.command_timeout)
        with self.lock:
            response = pending["response"] if confirmed else None
            if not response:
                response = {"type": "ack", "command_id": command_id, "ok": False, "target": "wokwi", "status": "timeout",
                            "message": "Sem confirmação do ESP32. O comando pode ter sido executado; confira a leitura antes de tentar novamente."}
            response = {**response, "command_id": command_id, "target": "wokwi"}
            self.db.execute("UPDATE commands SET acknowledgement = ? WHERE command_id = ?", (json.dumps(response), command_id))
            self.db.commit()
            self.pending.pop(command_id, None)
            return response

    def state(self):
        with self.lock:
            connected = self.connected()
            if self.mode == "mqtt":
                prefix = f"mqtt:{self.mqtt_config.topic}:"
                rows = self.db.execute("SELECT * FROM events WHERE substr(session_id, 1, ?) = ? ORDER BY id DESC LIMIT 100", (len(prefix), prefix))
            else:
                rows = self.db.execute("SELECT * FROM events WHERE substr(session_id, 1, 5) != 'mqtt:' ORDER BY id DESC LIMIT 100")
            events = [dict(row) for row in rows]
            readings = [dict(row) for row in self.db.execute("SELECT received_at, quantity, stable FROM readings WHERE session_id = ? ORDER BY id DESC LIMIT 120", (self.storage_session(),))][::-1]
            return {
                "mode": self.mode, "connected": connected, "wokwi_connected": self.mode == "mqtt" and connected,
                "mqtt_connected": self.broker_connected, "error": self.error, "last_reading_at": self.received_at,
                "telemetry": self.latest, "simulation": dict(self.simulation), "events": events,
                "readings": readings, "autonomy": self._autonomy(), "history_warning": self.history_warning,
                "mqtt": {"host": self.mqtt_config.host, "port": self.mqtt_config.port, "topic": self.mqtt_config.topic} if self.mqtt_config else None,
            }

    def _autonomy(self):
        # Only this running session is observed continuously. Restarts and gaps
        # cannot be silently treated as zero consumption.
        elapsed = time.monotonic() - self.started
        last_adjustment = self.db.execute(
            "SELECT MAX(id) FROM events WHERE session_id = ? AND kind = 'adjustment'", (self.storage_session(),)
        ).fetchone()[0] or 0
        row = self.db.execute(
            "SELECT COALESCE(SUM(-delta), 0) AS consumed FROM events WHERE session_id = ? AND id > ? AND kind = 'withdrawal'",
            (self.storage_session(), last_adjustment),
        ).fetchone()
        consumed = row["consumed"]
        # Stage 1 exposes an honest empty state. A later observation-window
        # service must account for uptime, adjustments and disconnections.
        return {"days": None, "daily_consumption": None, "consumed_pieces": consumed,
                "observed_seconds": int(elapsed), "status": "insufficient_data" if consumed else "no_consumption"}

    def close(self):
        self.stop.set()
        if self.transport:
            self.transport.close()
        if self.thread:
            self.thread.join(timeout=2)
        with self.lock:
            for pending in self.pending.values():
                pending["event"].set()
            if self.process and self.process.poll() is None:
                self.process.stdin.close()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            if self.process:
                self.process.stdout.close()
            self.db.close()


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, body):
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/state":
            self.send_json(200, self.server.application.state())
            return
        files = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                 "/styles.css": ("styles.css", "text/css")}
        if path not in files:
            self.send_json(404, {"error": "Recurso não encontrado."})
            return
        name, content_type = files[path]
        data = (STATIC / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host", ""):
                raise ValueError("Origem da requisição não permitida.")
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("Envie um objeto JSON.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                raise ValueError("Tamanho de requisição inválido.")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Envie um objeto JSON.")
            if self.path == "/api/simulation":
                result = self.server.application.change_simulation(body)
                self.send_json(200, result)
            elif self.path == "/api/command":
                result = self.server.application.execute(body)
                self.send_json(200 if result["ok"] else 504 if result.get("status") == "timeout" else 409, result)
            else:
                self.send_json(404, {"error": "Recurso não encontrado."})
        except (ValueError, UnicodeDecodeError) as error:
            self.send_json(400, {"error": str(error)})
        except (RuntimeError, OSError) as error:
            self.send_json(503, {"error": str(error)})

    def log_message(self, format, *args):
        # Polling is routine; report only errors to keep the terminal useful.
        if args and str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            super().log_message(format, *args)


def main():
    parser = argparse.ArgumentParser(description="StockWeight — dashboard local e MQTT")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--mode", choices=("local_simulator", "mqtt"), default="local_simulator")
    parser.add_argument("--mqtt-config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    database = args.database or ROOT / ("data/stockweight-mqtt.sqlite3" if args.mode == "mqtt" else "data/stockweight.sqlite3")
    application = Application(database, mode=args.mode, mqtt_config=load_config(args.mqtt_config) if args.mode == "mqtt" else None)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        server.application = application
        print(f"StockWeight: http://localhost:{server.server_port}", flush=True)
        if args.mode == "mqtt":
            print(f"Modo MQTT: {application.mqtt_config.host}:{application.mqtt_config.port}", flush=True)
            print(f"Tópicos: {application.mqtt_config.topic}/… — aguardando o ESP32 no Wokwi.", flush=True)
        else:
            print("Modo: simulador local. Use --mode mqtt para conectar ao Wokwi.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        application.close()


if __name__ == "__main__":
    main()
