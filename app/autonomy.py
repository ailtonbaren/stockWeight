"""Stock autonomy from observed withdrawals, with an isolated pitch example."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time

DAY = 24 * 60 * 60
WINDOW_DAYS = 7
MINIMUM_HOURS = 24


def iso_time(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="milliseconds")


def calculate(segments, movements, quantity, now, *, available=True):
    """Use the last seven rolling 24-hour days, counting only observed time.

    Intervals are (start, end] so a recovery snapshot cannot be mistaken for
    consumption measured during the gap. Unobserved hours are never zero demand.
    """
    cutoff = now - WINDOW_DAYS * DAY
    intervals = sorted((max(start, cutoff), min(end, now)) for start, end in segments
                       if end > cutoff and start < now and end > start)
    merged = []
    for start, end in intervals:
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    observed = sum(end - start for start, end in merged)
    consumed = sum(-event["delta"] for event in movements
                   if event["kind"] == "withdrawal" and event["delta"] < 0
                   and any(start < event["at"] <= end for start, end in merged))
    result = {
        "days": None, "daily_consumption": None, "consumed_pieces": consumed,
        "observed_seconds": observed, "observed_days": observed / DAY,
        "window_days": WINDOW_DAYS, "minimum_hours": MINIMUM_HOURS,
        "period_start": iso_time(merged[0][0]) if merged else None,
        "period_end": iso_time(merged[-1][1]) if merged else None,
        "status": "insufficient_data", "source": "device",
    }
    if not available or type(quantity) is not int or quantity < 0:
        result["status"] = "unavailable"
    elif observed < MINIMUM_HOURS * 3600:
        pass
    elif consumed == 0:
        result.update(status="no_consumption", daily_consumption=0)
    else:
        daily = consumed / (observed / DAY)
        result.update(status="forecast", daily_consumption=daily, days=quantity / daily)
    return result


class AutonomyTracker:
    """Persist observation intervals independently of the display's event limit."""
    def __init__(self, database, scope, *, max_gap=5, clock=None):
        self.db = database
        self.scope = scope
        self.max_gap = max_gap
        self.clock = clock or time.time
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS autonomy_epochs (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, session_id TEXT NOT NULL,
                signature TEXT NOT NULL, reason TEXT NOT NULL, last_seen REAL NOT NULL,
                last_event_sequence INTEGER NOT NULL, active_segment INTEGER
            );
            CREATE INDEX IF NOT EXISTS autonomy_scope ON autonomy_epochs(scope, id);
            CREATE TABLE IF NOT EXISTS autonomy_segments (
                id INTEGER PRIMARY KEY, epoch_id INTEGER NOT NULL,
                started_at REAL NOT NULL, ended_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS autonomy_segment_epoch ON autonomy_segments(epoch_id);
            CREATE TABLE IF NOT EXISTS autonomy_withdrawals (
                epoch_id INTEGER NOT NULL, event_sequence INTEGER NOT NULL,
                observed_at REAL NOT NULL, pieces INTEGER NOT NULL,
                PRIMARY KEY(epoch_id, event_sequence)
            );
        """)
        row = self.db.execute("SELECT * FROM autonomy_epochs WHERE scope = ? ORDER BY id DESC LIMIT 1", (scope,)).fetchone()
        self.epoch = dict(row) if row else None
        # Retain previously observed intervals, but never bridge backend downtime.
        self.pause()

    def pause(self):
        if self.epoch and self.epoch["active_segment"] is not None:
            self.epoch["active_segment"] = None
            self.db.execute("UPDATE autonomy_epochs SET active_segment = NULL WHERE id = ?", (self.epoch["id"],))

    def _new_reference(self, message, signature, event_sequence, now, reason):
        cursor = self.db.execute(
            "INSERT INTO autonomy_epochs (scope, session_id, signature, reason, last_seen, last_event_sequence) VALUES (?, ?, ?, ?, ?, ?)",
            (self.scope, message["session_id"], signature, reason, now, event_sequence),
        )
        self.epoch = {"id": cursor.lastrowid, "scope": self.scope, "session_id": message["session_id"],
                      "signature": signature, "reason": reason, "last_seen": now,
                      "last_event_sequence": event_sequence, "active_segment": None}

    def observe(self, message, now=None):
        now = self.clock() if now is None else now
        settings = message["settings"]
        signature = json.dumps([float(settings[key]) for key in ("unit_g", "grams_per_raw", "tare_raw")]
                               + [settings["tare_set"], settings["calibrated"]])
        event = message.get("event")
        sequence = event["sequence"] if event else 0
        previous_sequence = self.epoch["last_event_sequence"] if self.epoch else 0
        new_event = sequence > previous_sequence
        reason = None
        if not self.epoch:
            reason = "started"
        elif message["session_id"] != self.epoch["session_id"]:
            reason = "device_restarted"
        elif signature != self.epoch["signature"]:
            reason = "measurement_changed"
        elif now < self.epoch["last_seen"]:
            reason = "clock_changed"
        elif sequence > previous_sequence + 1:
            reason = "history_gap"
        elif new_event and event["kind"] == "adjustment":
            reason = "measurement_adjustment"
        if reason:
            self._new_reference(message, signature, sequence, now, reason)
            new_event = False
        if now - self.epoch["last_seen"] > self.max_gap:
            self.pause()
        had_observation = self.epoch["active_segment"] is not None
        if not message["valid"]:
            self.pause()
        elif not had_observation:
            if message["stable"] and message["container_present"]:
                cursor = self.db.execute("INSERT INTO autonomy_segments (epoch_id, started_at, ended_at) VALUES (?, ?, ?)",
                                         (self.epoch["id"], now, now))
                self.epoch["active_segment"] = cursor.lastrowid
        else:
            # Normal settling and container handling preserve the reference.
            # The forecast is hidden until the container returns and settles.
            self.db.execute("UPDATE autonomy_segments SET ended_at = ? WHERE id = ?", (now, self.epoch["active_segment"]))
            if new_event and event["kind"] == "withdrawal":
                self.db.execute("INSERT OR IGNORE INTO autonomy_withdrawals VALUES (?, ?, ?, ?)",
                                (self.epoch["id"], sequence, now, -event["delta"]))
        self.epoch["last_seen"] = now
        self.epoch["last_event_sequence"] = max(sequence, self.epoch["last_event_sequence"])
        self.db.execute("UPDATE autonomy_epochs SET last_seen = ?, last_event_sequence = ?, active_segment = ? WHERE id = ?",
                        (now, self.epoch["last_event_sequence"], self.epoch["active_segment"], self.epoch["id"]))

    def summary(self, quantity, *, available, now=None):
        now = self.clock() if now is None else now
        segments, events = [], []
        if self.epoch:
            cutoff = now - WINDOW_DAYS * DAY
            segments = [(row["started_at"], row["ended_at"]) for row in self.db.execute(
                "SELECT started_at, ended_at FROM autonomy_segments WHERE epoch_id = ? AND ended_at > ?", (self.epoch["id"], cutoff))]
            events = [{"kind": "withdrawal", "at": row["observed_at"], "delta": -row["pieces"]} for row in self.db.execute(
                "SELECT observed_at, pieces FROM autonomy_withdrawals WHERE epoch_id = ? AND observed_at > ?", (self.epoch["id"], cutoff))]
        result = calculate(segments, events, quantity, now, available=available)
        result["reference_reason"] = self.epoch["reason"] if self.epoch else "started"
        return result


def example_history(now=None):
    """Read-only example. No live configuration, broker or database is touched."""
    now = time.time() if now is None else now
    fixture = Path(__file__).resolve().parents[1] / "examples/autonomy-history.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    duration = data["observed_days"] * DAY
    start = now - duration
    quantity = data["initial_quantity"]
    movements = [{"kind": "initial", "delta": 0, "at": start, "elapsed_hours": 0, "quantity": quantity}]
    for item in data["movements"]:
        quantity += item["delta"]
        movements.append({**item, "at": start + item["elapsed_hours"] * 3600, "quantity": quantity})
    summary = calculate([(start, now)], movements, quantity, now)
    summary["source"] = "example"
    return {"source": "example", "label": data["label"], "quantity": quantity,
            "autonomy": summary,
            "events": [{key: value for key, value in event.items() if key != "at"} for event in movements]}
