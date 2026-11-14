"""Preparation, material ownership and migration through the real native core."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.server import Application


class MaterialWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="stockweight-materials-")
        self.database = Path(self.directory.name) / "stock.sqlite3"
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

    def learn(self, *, empty=100, sample_mass=150, count=10, lot=500):
        self.settle(empty)
        self.assertTrue(self.application.execute({"command": "tare"})["ok"])
        self.settle(sample_mass)
        self.assertTrue(self.application.execute({"command": "sample_unit", "value": count})["ok"])
        return self.settle(lot)

    def test_ten_piece_sample_and_eighty_piece_preview_do_not_create_movements(self):
        flow = self.application.state()["workflow"]
        self.assertEqual(flow["phase"], "preparing")
        self.assertFalse(flow["can_start"])
        self.assertIsNone(flow["material"]["unit_g"])
        state = self.learn()
        self.assertEqual(state["telemetry"]["quantity"], 80)
        self.assertFalse(state["telemetry"]["alert"])
        self.assertIsNone(state["telemetry"]["event"])
        self.assertEqual(state["events"], [])
        self.assertEqual(state["readings"], [])
        self.assertTrue(state["workflow"]["can_start"])
        self.assertTrue(self.application.execute({"command": "start_monitoring"})["ok"])
        state = self.application.state()
        self.assertEqual([(event["kind"], event["quantity"]) for event in state["events"]], [("initial", 80)])
        self.assertEqual(state["workflow"]["phase"], "monitoring")
        state = self.settle(450)
        self.assertEqual(state["events"][0]["kind"], "withdrawal")
        self.assertEqual(state["events"][0]["delta"], -10)
        self.assertEqual(state["autonomy"]["consumed_pieces"], 10)

    def test_start_requires_tare_known_unit_and_a_stable_lot(self):
        with self.assertRaises(ValueError):
            self.application.execute({"command": "start_monitoring"})
        self.settle(100)
        self.application.execute({"command": "tare"})
        self.settle(150)
        with self.assertRaises(ValueError):
            self.application.execute({"command": "start_monitoring"})
        self.application.execute({"command": "sample_unit", "value": 10})
        with self.assertRaises(ValueError):
            self.application.execute({"command": "start_monitoring"})
        self.settle(500)
        self.assertTrue(self.application.execute({"command": "start_monitoring"})["ok"])

    def test_material_switch_separates_history_and_reuses_saved_unit_and_minimum(self):
        screw = self.application.state()["workflow"]["material"]["id"]
        self.learn()
        self.application.execute({"command": "minimum", "value": 12})
        self.application.execute({"command": "start_monitoring"})
        self.settle(450)
        screws = self.application.state()["events"]
        nail = self.application.create_material({"name": "Prego"})["material"]
        self.assertTrue(self.application.execute({"command": "prepare", "material_id": nail["id"]})["ok"])
        self.assertEqual(self.application.state()["events"], [])
        state = self.learn(sample_mass=200, lot=900)
        self.assertEqual(state["telemetry"]["quantity"], 80)
        self.application.execute({"command": "start_monitoring"})
        self.settle(800)
        nails = self.application.state()["events"]
        self.assertTrue(all(event["material_id"] == nail["id"] and event["material_name"] == "Prego" for event in nails))
        self.assertEqual(self.application.state(history_material=screw)["events"], screws)
        self.assertEqual(len(self.application.state(history_material="all")["events"]), len(screws) + len(nails))
        self.application.prepare_material({"material_id": screw})
        state = self.settle(100)
        self.assertAlmostEqual(state["telemetry"]["settings"]["unit_g"], 5, delta=0.02)
        self.assertEqual(state["telemetry"]["settings"]["minimum"], 12)
        self.assertFalse(state["telemetry"]["settings"]["tare_set"])
        self.assertEqual(state["events"], screws)
        self.assertEqual(state["autonomy"]["consumed_pieces"], 0)

    def test_preparing_again_does_not_record_removing_the_previous_lot(self):
        self.learn()
        self.application.execute({"command": "start_monitoring"})
        before = self.application.state()["events"]
        self.application.execute({"command": "prepare"})
        for mass in (0, 100, 150, 1200, 200):
            state = self.settle(mass)
            self.assertEqual(state["events"], before)
            self.assertFalse(state["telemetry"]["alert"])

    def test_restart_preserves_profiles_and_history_but_local_device_requires_preparation(self):
        self.learn()
        self.application.execute({"command": "start_monitoring"})
        old = self.application.state()
        self.application.create_material({"name": "Prego"})
        self.application.close()
        self.application = Application(self.database, background=False)
        state = self.application.state()
        self.assertEqual(state["events"], old["events"])
        self.assertEqual([m["name"] for m in state["materials"]], ["Parafusos", "Prego"])
        self.assertEqual(state["workflow"]["phase"], "preparing")
        self.assertFalse(state["telemetry"]["settings"]["tare_set"])
        self.assertEqual(state["workflow"]["material"]["unit_g"], old["workflow"]["material"]["unit_g"])

    def test_duplicate_start_does_not_create_another_initial_event(self):
        self.learn()
        body = {"command": "start_monitoring", "command_id": "begin-once"}
        first = self.application.execute(body)
        events = self.application.state()["events"]
        self.assertEqual(self.application.execute(body), first)
        self.assertEqual(self.application.state()["events"], events)

    def test_reset_tare_during_monitoring_returns_to_preparation_without_stock_movements(self):
        self.learn()
        self.application.execute({"command": "start_monitoring"})
        before = self.application.state()["events"]
        settings = self.application.state()["telemetry"]["settings"]
        response = self.application.execute({"command": "reset_tare"})
        self.assertTrue(response["ok"])
        state = self.settle(200)
        self.assertEqual(state["workflow"]["phase"], "preparing")
        self.assertEqual(state["events"], before)
        self.assertFalse(state["telemetry"]["settings"]["tare_set"])
        for key in ("unit_g", "minimum", "grams_per_raw", "calibrated"):
            self.assertEqual(state["telemetry"]["settings"][key], settings[key])
        self.assertTrue(self.application.execute({"command": "tare"})["ok"])
        self.assertEqual(self.settle(200)["telemetry"]["quantity"], 0)

    def test_profile_validation_and_scope_prevent_cross_material_reassignment(self):
        self.application.create_material({"name": "Prego"})
        for name in ("", " ", "pREGO", "bad\nname", "x" * 81, 10):
            with self.assertRaises(ValueError):
                self.application.create_material({"name": name})
        with self.assertRaises(ValueError):
            self.application.prepare_material({"material_id": "unknown"})
        with self.assertRaises(ValueError):
            self.application.state(history_material="unknown")
        context = self.application.state()["workflow"]["context_id"]
        nail = self.application.state()["materials"][1]
        with self.assertRaises(ValueError):
            self.application.prepare_material({"material_id": nail["id"], "command_id": context})

    def test_external_measurement_change_stops_monitoring_instead_of_creating_stock_events(self):
        self.learn()
        self.application.execute({"command": "start_monitoring"})
        before = self.application.state()["events"]
        self.application._exchange("unit external-change 10")
        state = self.settle(1000)
        self.assertEqual(state["workflow"]["phase"], "preparing")
        self.assertEqual(state["events"], before)
        self.assertFalse(state["workflow"]["can_start"])

    def test_legacy_database_is_migrated_without_erasing_or_duplicating_history(self):
        self.application.close()
        self.database = Path(self.directory.name) / "legacy.sqlite3"
        db = sqlite3.connect(self.database)
        db.executescript("""
            CREATE TABLE events (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                received_at TEXT NOT NULL, kind TEXT NOT NULL, delta INTEGER NOT NULL, quantity INTEGER NOT NULL,
                UNIQUE(session_id, sequence));
            INSERT INTO events VALUES (1, 'local-old', 1, '2026-11-13T12:00:00Z', 'initial', 0, 80);
            INSERT INTO events VALUES (2, 'local-old', 2, '2026-11-13T12:01:00Z', 'withdrawal', -10, 70);
        """)
        db.close()
        self.application = Application(self.database, background=False)
        events = self.application.state()["events"]
        self.assertEqual([row["id"] for row in events], [2, 1])
        self.assertTrue(all(row["material_name"] == "Parafusos" for row in events))
        self.application.close()
        self.application = Application(self.database, background=False)
        self.assertEqual(self.application.state()["events"], events)


if __name__ == "__main__":
    unittest.main()
