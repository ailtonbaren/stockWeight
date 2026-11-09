"""MQTT adapter. Domain logic and SQLite do not run inside this module."""
from __future__ import annotations

import json
import logging
import math
import uuid

from app.mqtt_config import IDENTIFIER, MqttConfig

LOG = logging.getLogger(__name__)


def decode_payload(payload: bytes):
    if len(payload) > 4096:
        raise ValueError("Mensagem MQTT excede 4096 bytes.")
    value = json.loads(payload.decode("utf-8"), parse_constant=lambda token: (_ for _ in ()).throw(ValueError("Número inválido.")))
    if not isinstance(value, dict):
        raise ValueError("Mensagem MQTT precisa ser um objeto JSON.")
    return value


def validate_telemetry(message, device_id):
    """Reject malformed messages before they can alter history or configuration."""
    if message.get("type") != "telemetry" or message.get("device_id") != device_id:
        raise ValueError("Telemetria de outro dispositivo ou tipo inválido.")
    session = message.get("session_id")
    if not isinstance(session, str) or not IDENTIFIER.fullmatch(session):
        raise ValueError("Sessão inválida.")

    def integer(value, low=0, high=2**32 - 1):
        if type(value) is not int or not low <= value <= high:
            raise ValueError("Campo inteiro inválido na telemetria.")

    def numeric(value, *, nullable=False, low=-1e8, high=1e8):
        if nullable and value is None:
            return
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError("Campo numérico inválido na telemetria.")

    integer(message.get("sequence"), 1)
    integer(message.get("uptime_ms"))
    for field in ("valid", "stable", "container_present"):
        if type(message.get(field)) is not bool:
            raise ValueError("Estado de leitura inválido.")
    expected_status = "invalid" if not message["valid"] else "container_removed" if not message["container_present"] else "ready" if message["stable"] else "settling"
    if message.get("status") != expected_status:
        raise ValueError("Status incoerente com a leitura.")
    for field in ("raw", "gross_g", "net_g"):
        numeric(message.get(field), nullable=not message["valid"])
    numeric(message.get("tare_g"))
    quantity = message.get("quantity")
    if quantity is not None:
        integer(quantity, 0, 1000000)
    alert = message.get("alert")
    if alert is not None and type(alert) is not bool:
        raise ValueError("Estado de alerta inválido.")
    settings = message.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("Configuração ausente.")
    numeric(settings.get("unit_g"), low=0.01, high=5000)
    numeric(settings.get("grams_per_raw"), low=0.000001, high=10000)
    numeric(settings.get("tare_raw"), low=0, high=4095)
    integer(settings.get("minimum"), 0, 1000000)
    if any(type(settings.get(key)) is not bool for key in ("tare_set", "calibrated")):
        raise ValueError("Estado de calibração inválido.")
    if message["stable"] and (not message["valid"] or not message["container_present"] or quantity is None):
        raise ValueError("Quantidade estável indisponível.")
    if (not message["valid"] or not message["container_present"]) and (quantity is not None or alert is not None):
        raise ValueError("Leitura indisponível com quantidade ativa.")
    if quantity is not None and alert != (quantity <= settings["minimum"]):
        raise ValueError("Alerta incoerente com o estoque.")
    event = message.get("event")
    if event is not None:
        if not isinstance(event, dict) or event.get("kind") not in {"initial", "adjustment", "withdrawal", "restock"}:
            raise ValueError("Evento inválido.")
        integer(event.get("sequence"), 1)
        integer(event.get("quantity"), 0, 1000000)
        integer(event.get("delta"), -1000000, 1000000)
        if ((event["kind"] == "withdrawal" and event["delta"] >= 0)
                or (event["kind"] == "restock" and event["delta"] <= 0)
                or (event["kind"] in {"initial", "adjustment"} and event["delta"] != 0)):
            raise ValueError("Variação incompatível com o evento.")
    return message


class MqttTransport:
    def __init__(self, config: MqttConfig, on_message, on_connection, *, client=None):
        self.config = config
        self.receive = on_message
        self.connection = on_connection
        self.connected = False
        self.subscription = None
        if client is None:
            try:
                import paho.mqtt.client as mqtt
            except ImportError as error:
                raise RuntimeError("Instale o suporte MQTT: .venv/bin/python -m pip install -r requirements-mqtt.txt") from error
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                 client_id="sw-dashboard-" + uuid.uuid4().hex[:12],
                                 clean_session=True, protocol=mqtt.MQTTv311)
        self.client = client
        client.on_connect = self._on_connect
        client.on_connect_fail = self._on_connect_fail
        client.on_subscribe = self._on_subscribe
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=15)

    def start(self):
        self.client.connect_async(self.config.host, self.config.port, keepalive=15)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        self.connected = False
        if reason_code != 0:
            self.connection(False, f"Broker recusou a conexão: {reason_code}.")
            return
        result, self.subscription = client.subscribe([
            (self.config.topic + "/telemetry", 0),
            (self.config.topic + "/ack", 0),
            (self.config.topic + "/availability", 0),
        ])
        if result != 0:
            self.connection(False, "Não foi possível assinar os tópicos MQTT.")

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties):
        if mid != self.subscription:
            return
        self.connected = bool(reason_codes) and all(code == 0 for code in reason_codes)
        self.connection(self.connected, None if self.connected else "O broker recusou a assinatura MQTT.")

    def _on_connect_fail(self, client, userdata):
        self.connected = False
        self.connection(False, "Não foi possível conectar ao broker. Verifique a rede e o endereço; tentando novamente.")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected = False
        self.connection(False, "Conexão com o broker interrompida; aguardando reconexão.")

    def _on_message(self, client, userdata, packet):
        try:
            # Old retained telemetry/acks must never look like live readings.
            if packet.retain and packet.topic != self.config.topic + "/availability":
                return
            suffix = packet.topic.removeprefix(self.config.topic + "/")
            if packet.topic != self.config.topic + "/" + suffix or suffix not in {"telemetry", "ack", "availability"}:
                return
            self.receive(suffix, decode_payload(packet.payload))
        except (ValueError, UnicodeDecodeError, KeyError, TypeError, RecursionError) as error:
            LOG.warning("Mensagem MQTT ignorada: %s", error)

    def publish_command(self, session, line):
        if not self.connected:
            raise RuntimeError("Broker MQTT desconectado.")
        # No retained command or durable delivery request. The application does
        # not retry automatically: a lost confirmation leaves an uncertain result.
        result = self.client.publish(self.config.topic + "/command", session + "\t" + line,
                                     qos=0, retain=False)
        if result.rc != 0:
            raise RuntimeError("Não foi possível publicar o comando MQTT.")

    def close(self):
        self.connected = False
        self.client.disconnect()
        self.client.loop_stop()
