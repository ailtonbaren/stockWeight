from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from app.server import Application, Handler


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="stockweight-test-")
        self.database = Path(self.directory.name) / "history.sqlite3"
        self.application = Application(self.database, background=False)
        self.clock = 0

    def tearDown(self):
        self.application.close()
        self.directory.cleanup()

    def settle(self, mass):
        self.application.change_simulation({"mass_g": mass})
        for _ in range(12):
            self.clock += 100
            self.application.tick(milliseconds=self.clock)
        return self.application.state()

    def normal(self):
        self.settle(100)
        self.application.execute({"command": "tare", "command_id": "empty"})
        return self.settle(1100)

    def request(self, method, path, body=None, *, origin="http://localhost:8765"):
        # Exercise the real HTTP request parser and handler using memory streams;
        # sockets are not available inside the restricted execution environment.
        payload = json.dumps(body).encode() if body is not None else b""
        header = (f"{method} {path} HTTP/1.1\r\nHost: localhost:8765\r\nOrigin: {origin}\r\n"
                  f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n").encode()
        handler = Handler.__new__(Handler)
        handler.rfile = BytesIO(header + payload)
        handler.wfile = BytesIO()
        handler.server = SimpleNamespace(application=self.application)
        handler.client_address = ("127.0.0.1", 0)
        handler.handle_one_request()
        headers, response = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        status = int(headers.split(b" ", 2)[1])
        return status, headers, response

    def test_persistent_history_and_settings_survive_restart(self):
        self.normal()
        self.settle(950)
        self.application.execute({"command": "minimum", "value": 35})
        events = self.application.state()["events"]
        self.application.close()
        self.application = Application(self.database, background=False)
        state = self.application.state()
        self.assertEqual(state["events"], events)
        self.assertEqual(state["telemetry"]["settings"]["minimum"], 35)
        self.assertTrue(state["telemetry"]["settings"]["tare_set"])
        self.assertEqual(state["simulation"]["mass_g"], 950)

    def test_event_deduplication_and_consumption_exclude_restocks(self):
        self.normal()
        self.settle(950)
        self.settle(1200)
        state = self.settle(1200)
        kinds = [event["kind"] for event in state["events"]]
        self.assertEqual(kinds.count("withdrawal"), 1)
        self.assertEqual(state["autonomy"]["consumed_pieces"], 30)
        self.assertIsNone(state["autonomy"]["days"])

    def test_duplicate_command_is_idempotent_and_payload_cannot_change(self):
        self.normal()
        body = {"command": "unit", "value": 10, "command_id": "one-command"}
        first = self.application.execute(body)
        self.settle(1100)
        count = len(self.application.state()["events"])
        self.assertEqual(self.application.execute(body), first)
        self.assertEqual(len(self.settle(1100)["events"]), count)
        with self.assertRaises(ValueError):
            self.application.execute({**body, "value": 20})

    def test_fault_and_disconnection_states(self):
        self.normal()
        self.application.change_simulation({"fault": True})
        state = self.application.state()
        self.assertTrue(state["connected"])
        self.assertFalse(state["telemetry"]["valid"])
        self.application.change_simulation({"connected": False})
        self.assertFalse(self.application.state()["connected"])
        with self.assertRaises(ValueError):
            self.application.execute({"command": "minimum", "value": 0})
        self.application.change_simulation({"fault": False, "connected": True})
        self.assertEqual(self.settle(1100)["telemetry"]["quantity"], 200)

    def test_http_serves_ui_and_reports_local_mode(self):
        status, headers, response = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Content-Security-Policy", headers)
        self.assertIn("Modo de teste local".encode(), response)
        status, _, response = self.request("GET", "/api/state")
        state = json.loads(response)
        self.assertEqual(status, 200)
        self.assertEqual(state["mode"], "local_simulator")
        self.assertFalse(state["wokwi_connected"])
        for path in ("/app.js", "/styles.css"):
            self.assertEqual(self.request("GET", path)[0], 200)

    def test_http_commands_validate_input_and_return_ack(self):
        status, _, response = self.request("POST", "/api/command", {"command": "tare"})
        self.assertEqual(status, 409)
        self.assertFalse(json.loads(response)["ok"])
        status, _, response = self.request("POST", "/api/command", {"command": "minimum", "value": 12})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response)["target"], "local_simulator")
        for body in ({"mass_g": -1}, {"mass_g": float("nan")}, {"fault": "yes"}, {"mass_g": True}):
            self.assertEqual(self.request("POST", "/api/simulation", body)[0], 400)
        self.assertEqual(self.request("POST", "/api/simulation", {"mass_g": 100}, origin="https://another.example")[0], 400)
        self.assertEqual(self.request("GET", "/../../docs/stockWeight.md")[0], 404)

    def test_pitch_example_does_not_change_live_state_or_database(self):
        self.normal()
        self.settle(950)
        # Include every SQLite table, not only the visible event history.
        before = list(self.application.db.iterdump())
        telemetry = self.application.state()["telemetry"]
        for _ in range(2):
            status, _, response = self.request("GET", "/api/autonomy/example")
            data = json.loads(response)
            self.assertEqual(status, 200)
            self.assertEqual(data["source"], "example")
            self.assertEqual(data["autonomy"]["days"], 10)
        self.assertEqual(list(self.application.db.iterdump()), before)
        self.assertEqual(self.application.state()["telemetry"], telemetry)
        self.assertEqual(self.application.state()["autonomy"]["source"], "device")

    def test_autonomy_suspends_on_fault_and_pause_and_resets_after_tare(self):
        self.normal()
        self.settle(950)
        self.assertEqual(self.application.state()["autonomy"]["consumed_pieces"], 30)
        self.application.change_simulation({"fault": True})
        self.assertEqual(self.application.state()["autonomy"]["status"], "unavailable")
        self.application.change_simulation({"fault": False})
        self.settle(950)
        self.application.change_simulation({"connected": False})
        self.assertEqual(self.application.state()["autonomy"]["status"], "unavailable")
        self.application.change_simulation({"connected": True})
        self.settle(950)
        self.application.execute({"command": "tare"})
        result = self.settle(950)["autonomy"]
        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(result["consumed_pieces"], 0)


if __name__ == "__main__":
    unittest.main()
