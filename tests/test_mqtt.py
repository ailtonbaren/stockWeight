"""MQTT contract tests using the real C++ engine and an in-memory client.

No internet, sockets or installed Paho package are needed. These tests verify
our adapter callbacks and application behavior, not the public broker's uptime.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

from app.mqtt_config import MqttConfig, load_config
from app.mqtt_transport import MqttTransport, validate_telemetry
from app.server import Application
from scripts.build_simulator import build


class MemoryClient:
    def __init__(self):
        self.published = []
        self.publish_hook = None
        self.publish_result = 0
        self.subscriptions = []

    def reconnect_delay_set(self, **kwargs):
        self.reconnect = kwargs

    def connect_async(self, host, port, **kwargs):
        self.endpoint = (host, port)

    def subscribe(self, topics):
        self.subscriptions = topics
        return 0, 1

    def loop_start(self):
        self.on_connect(self, None, None, 0, None)
        self.on_subscribe(self, None, 1, [0, 0, 0], None)

    def publish(self, topic, payload, **kwargs):
        self.published.append((topic, payload, kwargs))
        if self.publish_hook and self.publish_result == 0:
            self.publish_hook(topic, payload)
        return SimpleNamespace(rc=self.publish_result)

    def deliver(self, topic, payload, *, retain=False):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, allow_nan=True).encode()
        self.on_message(self, None, SimpleNamespace(topic=topic, payload=data, retain=retain))

    def disconnect(self):
        self.on_disconnect(self, None, None, 0, None)

    def loop_stop(self):
        pass


class NativeDevice:
    def __init__(self, config):
        self.config = config
        self.session = "boot-test-1"
        self.process = subprocess.Popen([str(build()), self.session], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, text=True, encoding="utf-8")
        self.clock = 0

    def exchange(self, line):
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        result = json.loads(self.process.stdout.readline())
        result.update(device_id=self.config.device_id, session_id=self.session)
        return result

    def settle(self, mass):
        for _ in range(12):
            self.clock += 100
            result = self.exchange(f"sample {self.clock} {int(mass * 4095 / 5000 + 0.5)} 1")
        return result

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=3)
        self.process.stdout.close()


class MqttTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="stockweight-mqtt-")
        self.database = Path(self.directory.name) / "history.sqlite3"
        self.config = MqttConfig(prefix="stockweight/team_test")
        self.client = MemoryClient()
        self.device = NativeDevice(self.config)
        self.application = Application(self.database, mode="mqtt", mqtt_config=self.config,
                                       transport_factory=self.factory, command_timeout=0.04)

    def tearDown(self):
        self.application.close()
        self.device.close()
        self.directory.cleanup()

    def factory(self, config, receive, connection):
        return MqttTransport(config, receive, connection, client=self.client)

    def deliver(self, kind, payload, **kwargs):
        self.client.deliver(self.config.topic + "/" + kind, payload, **kwargs)

    def live(self, mass=100):
        message = self.device.settle(mass)
        self.deliver("telemetry", message)
        return message

    def device_receives(self, topic, frame):
        self.assertEqual(topic, self.config.topic + "/command")
        session, line = frame.split("\t", 1)
        self.assertEqual(session, self.device.session)
        self.deliver("ack", self.device.exchange(line))
        self.deliver("telemetry", self.device.exchange("snapshot"))

    def test_requires_live_device_and_never_falls_back_to_simulator(self):
        state = self.application.state()
        self.assertTrue(state["mqtt_connected"])
        self.assertFalse(state["connected"])
        self.assertIsNone(state["telemetry"])
        self.assertIsNone(self.application.process)
        with self.assertRaises(ValueError):
            self.application.execute({"command": "minimum", "value": 200})
        with self.assertRaises(ValueError):
            self.application.change_simulation({"mass_g": 100})
        self.live()
        self.assertTrue(self.application.state()["wokwi_connected"])

    def test_real_engine_receives_tare_and_minimum_and_confirms_execution(self):
        self.live()
        self.client.publish_hook = self.device_receives
        result = self.application.execute({"command": "tare", "command_id": "tare-remote"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["target"], "wokwi")
        self.assertEqual(result["status"], "confirmed")
        state = self.live(1100)
        self.assertEqual(state["quantity"], 200)
        result = self.application.execute({"command": "minimum", "command_id": "minimum-remote", "value": 200})
        self.assertTrue(result["ok"])
        self.assertTrue(self.application.state()["telemetry"]["alert"])
        self.assertEqual(self.application.state()["telemetry"]["settings"]["minimum"], 200)
        for _, _, options in self.client.published:
            self.assertEqual(options, {"qos": 0, "retain": False})

    def test_waits_without_holding_callback_lock(self):
        self.application.command_timeout = 1
        self.live()
        results = []
        worker = threading.Thread(target=lambda: results.append(self.application.execute(
            {"command": "minimum", "value": 200, "command_id": "async-command"})))
        worker.start()
        deadline = time.monotonic() + 1
        while not self.client.published and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(worker.is_alive())
        topic, frame, _ = self.client.published[-1]
        self.device_receives(topic, frame)
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertTrue(results[0]["ok"])

    def test_timeout_does_not_report_success_or_replay_command(self):
        self.live()
        body = {"command": "tare", "command_id": "uncertain-command"}
        result = self.application.execute(body)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "timeout")
        self.assertIn("pode ter sido executado", result["message"])
        self.assertEqual(self.application.execute(body), result)
        self.assertEqual(len(self.client.published), 1)

    def test_wrong_session_ack_is_ignored(self):
        self.live()
        def wrong_ack(topic, frame):
            self.deliver("ack", {"type": "ack", "device_id": self.config.device_id,
                                 "session_id": "old-boot", "command_id": "session-check",
                                 "ok": True, "message": "Wrong session"})
        self.client.publish_hook = wrong_ack
        result = self.application.execute({"command": "minimum", "value": 200, "command_id": "session-check"})
        self.assertEqual(result["status"], "timeout")

    def test_duplicate_command_and_rejected_parameter_preserve_state(self):
        self.live()
        self.client.publish_hook = self.device_receives
        body = {"command": "unit", "value": 10, "command_id": "idempotent"}
        result = self.application.execute(body)
        self.assertEqual(self.application.execute(body), result)
        self.assertEqual(len(self.client.published), 1)
        with self.assertRaises(ValueError):
            self.application.execute({**body, "value": 20})
        with self.assertRaises(ValueError):
            self.application.execute({"command": "unit", "value": 0})

    def test_publish_error_is_reported_without_retry(self):
        self.live()
        self.client.publish_result = 4
        result = self.application.execute({"command": "tare", "command_id": "no-publish"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "publish_failed")

    def test_retained_and_out_of_order_telemetry_cannot_become_live(self):
        message = self.device.settle(100)
        self.deliver("telemetry", message, retain=True)
        self.assertFalse(self.application.state()["connected"])
        self.deliver("telemetry", message)
        sequence = message["sequence"]
        old = self.device.settle(1100)
        old["sequence"] = sequence - 1
        self.deliver("telemetry", old)
        self.assertEqual(self.application.state()["telemetry"]["quantity"], 20)
        self.assertEqual(len(self.application.state()["events"]), 1)

    def test_duplicate_events_and_history_gaps(self):
        message = self.live()
        repeated = self.device.exchange("snapshot")
        self.deliver("telemetry", repeated)
        self.assertEqual(len(self.application.state()["events"]), 1)
        self.device.settle(1100)  # An event that was not received by the backend.
        message = self.live(950)
        self.assertEqual(message["event"]["sequence"], 3)
        state = self.application.state()
        self.assertEqual(len(state["events"]), 2)
        self.assertIsNotNone(state["history_warning"])

    def test_reconnect_needs_fresh_telemetry_and_old_boot_cannot_return(self):
        previous = self.live()
        self.client.disconnect()
        self.assertFalse(self.application.state()["connected"])
        self.client.loop_start()
        self.assertFalse(self.application.state()["connected"])
        fresh = self.live()
        self.assertTrue(self.application.state()["connected"])
        fresh.update(session_id="boot-new", sequence=1)
        self.deliver("telemetry", fresh)
        previous["sequence"] = 99999
        self.deliver("telemetry", previous)
        self.assertEqual(self.application.state()["telemetry"]["session_id"], "boot-new")

    def test_last_will_marks_device_offline_without_changing_stock(self):
        self.live(1100)
        self.deliver("availability", {"device_id": self.config.device_id,
                                      "session_id": self.device.session, "online": False}, retain=True)
        state = self.application.state()
        self.assertFalse(state["connected"])
        self.assertEqual(state["telemetry"]["quantity"], 220)
        self.assertEqual(len(state["events"]), 1)

    def test_malformed_messages_cannot_change_database(self):
        original = self.live()
        for field, bad in (("quantity", True), ("gross_g", float("nan")), ("device_id", "other-device"), ("stable", "yes")):
            message = deepcopy(original)
            message["sequence"] += 1
            message[field] = bad
            with self.assertLogs("app.mqtt_transport", level="WARNING"):
                self.deliver("telemetry", message)
        self.assertEqual(len(self.application.state()["events"]), 1)
        self.assertEqual(self.application.state()["telemetry"], original)
        with self.assertLogs("app.mqtt_transport", level="WARNING"):
            self.deliver("telemetry", b"[" * 1200 + b"0" + b"]" * 1200)
        self.live(1100)
        self.assertEqual(self.application.state()["telemetry"]["quantity"], 220)

    def test_history_persists_without_replaying_device_configuration(self):
        self.live()
        self.client.publish_hook = self.device_receives
        self.application.execute({"command": "minimum", "value": 200, "command_id": "persisted-minimum"})
        events = self.application.state()["events"]
        published = len(self.client.published)
        self.application.close()
        self.client = MemoryClient()
        self.application = Application(self.database, mode="mqtt", mqtt_config=self.config,
                                       transport_factory=self.factory, command_timeout=0.04)
        self.assertEqual(self.application.state()["events"], events)
        self.assertFalse(self.application.state()["connected"])
        self.assertIsNone(self.application.state()["telemetry"])
        self.assertEqual(len(self.client.published), 0)
        self.assertEqual(published, 1)

    def test_mqtt_and_local_histories_are_separate_even_in_same_database(self):
        self.application.close()
        local = Application(self.database, background=False)
        for i in range(12):
            local.tick(milliseconds=(i + 1) * 100)
        self.assertEqual(len(local.state()["events"]), 1)
        local.close()
        self.application = Application(self.database, mode="mqtt", mqtt_config=self.config,
                                       transport_factory=self.factory, command_timeout=0.04)
        self.assertEqual(self.application.state()["events"], [])
        self.live()
        self.assertEqual(len(self.application.state()["events"]), 1)


class ConfigTests(unittest.TestCase):
    def test_generated_namespace_is_persistent_and_can_be_shared(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mqtt.json"
            first = load_config(path)
            self.assertEqual(load_config(path), first)
            self.assertTrue(first.prefix.startswith("stockweight/"))
            self.assertNotEqual(first.prefix, "stockweight/example")

    def test_invalid_identifiers_and_endpoints_are_rejected(self):
        for value in ({"host": "http://broker.example"}, {"port": True}, {"prefix": "test/#"}, {"device_id": 'bad"id'}):
            with self.assertRaises(ValueError):
                MqttConfig(**value)


if __name__ == "__main__":
    unittest.main()
