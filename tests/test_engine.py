import json
import math
import subprocess
import unittest

from scripts.build_simulator import build


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = build()

    def setUp(self):
        self.process = subprocess.Popen([str(self.binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        self.clock = 0

    def tearDown(self):
        self.process.stdin.close()
        self.process.wait(timeout=3)
        self.process.stdout.close()

    def exchange(self, line):
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        return json.loads(self.process.stdout.readline())

    def sample(self, mass, *, valid=True):
        self.clock += 100
        raw = math.floor(mass * 4095 / 5000 + 0.5)
        return self.exchange(f"sample {self.clock} {raw} {int(valid)}")

    def settle(self, mass):
        for _ in range(12):
            state = self.sample(mass)
        self.assertTrue(state["stable"])
        return state

    def normal(self):
        self.settle(100)
        self.assertTrue(self.exchange("tare tare-1")["ok"])
        self.settle(100)
        return self.settle(1100)

    def test_waits_for_stability_and_separates_gross_tare_net(self):
        self.assertIsNone(self.sample(100)["event"])
        state = self.normal()
        self.assertEqual(state["quantity"], 200)
        self.assertFalse(state["alert"])
        self.assertAlmostEqual(state["gross_g"] - state["tare_g"], state["net_g"], places=3)
        self.assertAlmostEqual(state["net_g"], 1000, delta=2)

    def test_threshold_and_restock_clear_alert(self):
        self.normal()
        state = self.settle(350)
        self.assertEqual(state["quantity"], 50)
        self.assertTrue(state["alert"])
        state = self.settle(275)
        self.assertEqual(state["event"]["delta"], -15)
        self.assertEqual(state["event"]["kind"], "withdrawal")
        state = self.settle(1200)
        self.assertEqual(state["quantity"], 220)
        self.assertEqual(state["event"]["delta"], 185)
        self.assertFalse(state["alert"])

    def test_manipulation_only_records_final_stable_stock(self):
        previous = self.normal()["event"]["sequence"]
        for mass in (900, 700, 1000, 600):
            for _ in range(4):
                state = self.sample(mass)
                self.assertEqual(state["event"]["sequence"], previous)
        state = self.settle(950)
        self.assertEqual(state["event"]["sequence"], previous + 1)
        self.assertEqual(state["event"]["delta"], -30)
        self.assertEqual(self.settle(950)["event"]["sequence"], previous + 1)

    def test_hysteresis_prevents_rounding_boundary_chatter(self):
        self.normal()
        state = self.settle(1104)
        self.assertEqual(state["quantity"], 201)
        sequence = state["event"]["sequence"]
        for mass in (1102, 1104, 1102, 1104):
            state = self.settle(mass)
            self.assertEqual(state["quantity"], 201)
            self.assertEqual(state["event"]["sequence"], sequence)

    def test_removed_container_preserves_baseline(self):
        previous = self.normal()["event"]["sequence"]
        for _ in range(15):
            state = self.sample(0)
        self.assertEqual(state["status"], "container_removed")
        self.assertIsNone(state["quantity"])
        self.assertEqual(state["event"]["sequence"], previous)
        state = self.settle(950)
        self.assertEqual(state["event"]["delta"], -30)
        self.assertEqual(state["event"]["sequence"], previous + 1)

    def test_invalid_sensor_does_not_create_movements(self):
        previous = self.normal()["event"]["sequence"]
        for _ in range(20):
            state = self.sample(0, valid=False)
        self.assertFalse(state["valid"])
        self.assertIsNone(state["gross_g"])
        self.assertIsNone(state["quantity"])
        self.assertEqual(self.settle(1100)["event"]["sequence"], previous)

    def test_parameter_changes_are_adjustments(self):
        self.normal()
        self.assertTrue(self.exchange("unit unit-1 2.5")["ok"])
        state = self.settle(1100)
        self.assertEqual(state["quantity"], 400)
        self.assertEqual(state["event"]["kind"], "adjustment")
        self.assertEqual(state["event"]["delta"], 0)
        self.assertTrue(self.exchange("minimum min-1 400")["ok"])
        self.assertTrue(self.exchange("snapshot")["alert"])

    def test_invalid_parameters_and_unstable_tare_are_rejected(self):
        self.assertFalse(self.exchange("tare early")["ok"])
        for line in ("unit a 0", "unit b -1", "unit c nan", "minimum d -1", "minimum e 2.5", "calibrate f 0", 'tare invalid"id', "reset_tare extra-value 200"):
            self.assertFalse(self.exchange(line)["ok"], line)

    def test_reset_tare_recovers_from_excessive_tare_and_allows_retaring(self):
        self.settle(2000)
        self.assertTrue(self.exchange("tare mistaken-tare")["ok"])
        self.settle(2000)
        for _ in range(12):
            state = self.sample(200)
        self.assertEqual(state["status"], "container_removed")
        self.assertFalse(self.exchange("tare blocked-tare")["ok"])
        self.assertTrue(self.exchange("reset_tare recover-tare")["ok"])
        state = self.exchange("snapshot")
        self.assertEqual(state["tare_g"], 0)
        self.assertFalse(state["settings"]["tare_set"])
        self.assertIsNone(state["quantity"])
        state = self.settle(200)
        self.assertEqual(state["quantity"], 40)
        self.assertEqual(state["event"]["kind"], "adjustment")
        self.assertEqual(state["event"]["delta"], 0)
        self.assertTrue(self.exchange("tare corrected-tare")["ok"])
        self.assertEqual(self.settle(200)["quantity"], 0)
        self.assertAlmostEqual(self.exchange("snapshot")["tare_g"], 200, delta=1)

    def test_reset_tare_preserves_calibration_and_duplicate_does_not_undo_new_tare(self):
        self.normal()
        self.settle(600)
        self.assertTrue(self.exchange("calibrate calibration-1 500")["ok"])
        before = self.settle(600)["settings"]
        first = self.exchange("reset_tare reset-once")
        self.assertTrue(first["ok"])
        after = self.settle(200)["settings"]
        for key in ("grams_per_raw", "calibrated", "unit_g", "minimum"):
            self.assertEqual(after[key], before[key])
        self.assertTrue(self.exchange("tare new-tare")["ok"])
        self.settle(200)
        self.assertEqual(self.exchange("reset_tare reset-once"), first)
        self.assertTrue(self.exchange("snapshot")["settings"]["tare_set"])

    def test_reset_when_tare_already_clear_preserves_stable_stock(self):
        before = self.settle(200)
        self.assertTrue(self.exchange("reset_tare already-clear")["ok"])
        after = self.exchange("snapshot")
        self.assertTrue(after["stable"])
        self.assertEqual(after["quantity"], before["quantity"])
        self.assertEqual(after["event"], before["event"])

    def test_reference_calibration_and_unit_from_sample(self):
        self.settle(100)
        self.assertFalse(self.exchange("calibrate before-tare 500")["ok"])
        self.exchange("tare empty")
        self.settle(100)
        self.assertFalse(self.exchange("calibrate no-reference 500")["ok"])
        self.settle(600)
        self.assertTrue(self.exchange("calibrate known-500 500")["ok"])
        state = self.settle(600)
        self.assertAlmostEqual(state["net_g"], 500, places=3)
        self.assertTrue(state["settings"]["calibrated"])
        self.assertTrue(self.exchange("sample_unit sample-100 100")["ok"])
        self.assertAlmostEqual(self.exchange("snapshot")["settings"]["unit_g"], 5, places=3)

    def test_duplicate_command_returns_original_ack_without_reapplying(self):
        self.settle(100)
        original = self.exchange("tare unique-tare")
        self.settle(1100)
        self.assertEqual(self.exchange("tare unique-tare"), original)
        state = self.settle(1100)
        self.assertEqual(state["quantity"], 200)
        self.assertFalse(self.exchange("unit unique-tare 10")["ok"])

    def test_small_calibration_factor_remains_positive_in_telemetry(self):
        self.settle(100)
        self.exchange("tare small-reference-zero")
        self.settle(5000)
        self.assertTrue(self.exchange("calibrate small-reference 0.01")["ok"])
        factor = self.exchange("snapshot")["settings"]["grams_per_raw"]
        self.assertGreater(factor, 0)
        self.assertLess(factor, 0.00001)

    def test_explicit_preparation_previews_without_stock_events_or_led(self):
        self.assertTrue(self.exchange("prepare sample-run")["ok"])
        self.settle(100)
        self.exchange("tare empty-run")
        self.settle(150)
        acknowledgement = self.exchange("sample_unit learn-ten 10")
        self.assertTrue(acknowledgement["ok"])
        self.assertAlmostEqual(acknowledgement["unit_g"], 5, delta=0.02)
        for mass in (100, 150, 500):
            state = self.settle(mass)
            self.assertFalse(state["monitoring"])
            self.assertFalse(state["alert"])
            self.assertIsNone(state["event"])
        self.assertEqual(state["quantity"], 80)
        self.assertTrue(self.exchange("start_monitoring counted-lot")["ok"])
        state = self.exchange("snapshot")
        self.assertTrue(state["monitoring"])
        self.assertEqual(state["measurement_id"], "counted-lot")
        self.assertEqual(state["event"]["kind"], "initial")
        self.assertEqual(state["event"]["quantity"], 80)
        self.assertEqual(self.settle(450)["event"]["delta"], -10)

    def test_start_monitoring_requires_a_tared_and_stable_container(self):
        self.exchange("prepare no-tare")
        self.assertFalse(self.exchange("start_monitoring no-reading")["ok"])
        self.settle(100)
        self.assertFalse(self.exchange("start_monitoring needs-tare")["ok"])
        self.exchange("tare tare-container")
        self.assertFalse(self.exchange("start_monitoring settling")["ok"])
        self.settle(500)
        self.assertTrue(self.exchange("start_monitoring correct-start")["ok"])
        self.assertFalse(self.exchange("start_monitoring already-active")["ok"])

    def test_new_preparation_stops_alert_and_changes_context_without_a_withdrawal(self):
        self.exchange("prepare first-preparation")
        self.settle(100)
        self.exchange("tare first-container")
        self.settle(150)
        self.exchange("start_monitoring first-lot")
        self.assertTrue(self.exchange("snapshot")["alert"])
        self.exchange("prepare another-material")
        state = self.settle(0)
        self.assertEqual(state["measurement_id"], "another-material")
        self.assertFalse(state["settings"]["tare_set"])
        self.assertFalse(state["alert"])
        self.assertIsNone(state["event"])


if __name__ == "__main__":
    unittest.main()
