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
        for line in ("unit a 0", "unit b -1", "unit c nan", "minimum d -1", "minimum e 2.5", "calibrate f 0", 'tare invalid"id'):
            self.assertFalse(self.exchange(line)["ok"], line)

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


if __name__ == "__main__":
    unittest.main()
