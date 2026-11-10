"""Calendar-time forecasts and observation quality, without real-time waiting."""
from copy import deepcopy
import sqlite3
import unittest

from app.autonomy import AutonomyTracker, DAY, calculate, example_history

NOW = 1794312000.0


def movement(at, delta, kind="withdrawal"):
    return {"at": at, "delta": delta, "kind": kind}


class CalculationTests(unittest.TestCase):
    def test_forecast_uses_withdrawals_not_net_stock_change(self):
        result = calculate([(NOW - 3 * DAY, NOW)], [
            movement(NOW - 2 * DAY, -20), movement(NOW - DAY, -40),
            movement(NOW - DAY / 2, 100, "restock"),
            movement(NOW - DAY / 3, -500, "adjustment"),
        ], 200, NOW)
        self.assertEqual(result["status"], "forecast")
        self.assertEqual(result["consumed_pieces"], 60)
        self.assertEqual(result["daily_consumption"], 20)
        self.assertEqual(result["days"], 10)

    def test_exactly_24_observed_hours_are_required(self):
        events = [movement(NOW, -20)]
        before = calculate([(NOW - DAY + 1, NOW)], events, 100, NOW)
        self.assertEqual(before["status"], "insufficient_data")
        self.assertIsNone(before["days"])
        after = calculate([(NOW - DAY, NOW)], events, 100, NOW)
        self.assertEqual(after["status"], "forecast")
        self.assertEqual(after["days"], 5)

    def test_rolling_seven_calendar_days_include_days_without_consumption(self):
        cutoff = NOW - 7 * DAY
        result = calculate([(NOW - 10 * DAY, NOW)], [
            movement(cutoff - 1, -1000), movement(cutoff, -500),
            movement(cutoff + 1, -70), movement(NOW + 1, -900),
        ], 100, NOW)
        self.assertEqual(result["observed_days"], 7)
        self.assertEqual(result["consumed_pieces"], 70)
        self.assertEqual(result["daily_consumption"], 10)
        self.assertEqual(result["days"], 10)

    def test_gaps_are_not_zero_consumption_and_recovery_event_is_excluded(self):
        result = calculate([(NOW - 3 * DAY, NOW - 2 * DAY), (NOW - DAY, NOW)], [
            movement(NOW - 2.5 * DAY, -20), movement(NOW - 1.5 * DAY, -500),
            movement(NOW - DAY, -500), movement(NOW - DAY / 2, -20),
        ], 200, NOW)
        self.assertEqual(result["observed_days"], 2)
        self.assertEqual(result["consumed_pieces"], 40)
        self.assertEqual(result["days"], 10)

    def test_no_consumption_needs_history_and_empty_stock_can_have_zero_days(self):
        self.assertEqual(calculate([], [], 100, NOW)["status"], "insufficient_data")
        result = calculate([(NOW - DAY, NOW)], [], 100, NOW)
        self.assertEqual(result["status"], "no_consumption")
        self.assertEqual(result["daily_consumption"], 0)
        self.assertIsNone(result["days"])
        result = calculate([(NOW - DAY, NOW)], [movement(NOW, -20)], 0, NOW)
        self.assertEqual(result["status"], "forecast")
        self.assertEqual(result["days"], 0)

    def test_unavailable_or_invalid_quantity_never_produces_forecast(self):
        for quantity in (None, -1, True, 2.5):
            self.assertEqual(calculate([(NOW - DAY, NOW)], [], quantity, NOW)["status"], "unavailable")
        result = calculate([(NOW - DAY, NOW)], [movement(NOW, -20)], 100, NOW, available=False)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["days"])

    def test_overlapping_observation_intervals_do_not_double_count_time(self):
        result = calculate([(NOW - DAY, NOW), (NOW - DAY / 2, NOW + 100)], [movement(NOW, -20)], 100, NOW)
        self.assertEqual(result["observed_days"], 1)
        self.assertEqual(result["consumed_pieces"], 20)

    def test_pitch_example_uses_the_same_calculation(self):
        result = example_history(NOW)
        self.assertEqual(result["source"], "example")
        self.assertEqual(result["autonomy"]["source"], "example")
        self.assertEqual(result["quantity"], 200)
        self.assertEqual(result["autonomy"]["observed_days"], 3)
        self.assertEqual(result["autonomy"]["consumed_pieces"], 60)
        self.assertEqual(result["autonomy"]["daily_consumption"], 20)
        self.assertEqual(result["autonomy"]["days"], 10)
        self.assertEqual([item["quantity"] for item in result["events"]], [160, 140, 110, 210, 200])


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.now = NOW
        self.tracker = AutonomyTracker(self.db, "mqtt:team/device", clock=lambda: self.now)
        self.message = {
            "session_id": "boot-1", "valid": True, "stable": True, "container_present": True,
            "settings": {"unit_g": 5, "grams_per_raw": 1.22, "tare_raw": 80,
                         "tare_set": True, "calibrated": False, "minimum": 50},
            "event": {"sequence": 1, "kind": "initial", "delta": 0},
        }

    def tearDown(self):
        self.db.close()

    def observe(self, seconds=0, *, event=None, **fields):
        self.now += seconds
        self.message.update(fields)
        if event:
            self.message["event"] = event
        self.tracker.observe(self.message)
        return self.tracker.summary(200, available=True)

    def withdraw(self, seconds=1, sequence=2, pieces=20):
        return self.observe(seconds, event={"sequence": sequence, "kind": "withdrawal", "delta": -pieces})

    def test_repeated_telemetry_counts_one_withdrawal_and_minimum_preserves_history(self):
        self.observe()
        self.withdraw()
        self.observe(1)
        self.message["settings"]["minimum"] = 200
        result = self.observe(1)
        self.assertEqual(result["consumed_pieces"], 20)
        self.assertEqual(result["observed_seconds"], 3)
        self.assertEqual(result["reference_reason"], "started")

    def test_long_telemetry_gap_does_not_count_missing_time_or_recovery_withdrawal(self):
        self.observe()
        self.withdraw()
        self.withdraw(100, sequence=3, pieces=100)
        result = self.withdraw(1, sequence=4, pieces=10)
        self.assertEqual(result["consumed_pieces"], 30)
        self.assertEqual(result["observed_seconds"], 2)

    def test_invalid_sensor_breaks_coverage_until_a_stable_recovery(self):
        self.observe()
        self.withdraw()
        self.observe(1, valid=False, stable=False)
        self.observe(1, valid=True, stable=False)
        self.observe(1, stable=True, event={"sequence": 3, "kind": "withdrawal", "delta": -100})
        result = self.withdraw(1, sequence=4, pieces=10)
        self.assertEqual(result["consumed_pieces"], 30)
        self.assertEqual(result["observed_seconds"], 2)

    def test_container_handling_preserves_net_withdrawal_reference(self):
        self.observe()
        self.withdraw()
        self.observe(1, stable=False, container_present=False)
        self.observe(1, stable=False, container_present=True)
        result = self.observe(1, stable=True, event={"sequence": 3, "kind": "withdrawal", "delta": -10})
        self.assertEqual(result["consumed_pieces"], 30)
        self.assertEqual(result["observed_seconds"], 4)

    def test_measurement_change_restart_adjustment_and_lost_events_reset_reference(self):
        cases = [("measurement_changed", "unit"), ("measurement_changed", "tare"),
                 ("measurement_changed", "calibration"), ("device_restarted", "boot"),
                 ("measurement_adjustment", "adjustment"), ("history_gap", "gap"),
                 ("clock_changed", "clock")]
        original = deepcopy(self.message)
        for reason, change in cases:
            with self.subTest(change=change):
                self.message = deepcopy(original)
                self.tracker = AutonomyTracker(self.db, change, clock=lambda: self.now)
                self.observe()
                self.withdraw()
                if change == "unit":
                    self.message["settings"]["unit_g"] = 10
                elif change == "tare":
                    self.message["settings"]["tare_raw"] = 90
                elif change == "calibration":
                    self.message["settings"]["grams_per_raw"] = 1.5
                elif change == "boot":
                    self.message["session_id"] = "boot-2"
                    self.message["event"] = original["event"]
                elif change == "adjustment":
                    self.message["event"] = {"sequence": 3, "kind": "adjustment", "delta": 0}
                elif change == "gap":
                    self.message["event"] = {"sequence": 5, "kind": "withdrawal", "delta": -500}
                result = self.observe(-2 if change == "clock" else 1)
                self.assertEqual(result["reference_reason"], reason)
                self.assertEqual(result["consumed_pieces"], 0)
                self.assertEqual(result["observed_seconds"], 0)

    def test_persistence_preserves_known_time_and_excludes_backend_downtime(self):
        self.observe()
        self.withdraw()
        self.db.commit()
        self.tracker = AutonomyTracker(self.db, "mqtt:team/device", clock=lambda: self.now)
        self.withdraw(100, sequence=3, pieces=100)
        result = self.withdraw(1, sequence=4, pieces=10)
        self.assertEqual(result["consumed_pieces"], 30)
        self.assertEqual(result["observed_seconds"], 2)

    def test_other_topic_cannot_reuse_the_devices_observed_time(self):
        self.observe()
        self.withdraw()
        other = AutonomyTracker(self.db, "mqtt:another/device", clock=lambda: self.now)
        result = other.summary(200, available=True)
        self.assertEqual(result["consumed_pieces"], 0)
        self.assertEqual(result["observed_seconds"], 0)

    def test_forecast_after_a_full_day_and_expiration_after_seven_days(self):
        # One observation every hour, with a matching allowed gap for this test.
        # Production retains its short gap threshold; time advances without sleep.
        self.tracker.max_gap = 3600
        self.observe()
        for hour in range(1, 25):
            result = self.withdraw(3600) if hour == 12 else self.observe(3600)
        self.assertEqual(result["status"], "forecast")
        self.assertEqual(result["daily_consumption"], 20)
        self.assertEqual(result["days"], 10)
        self.now += 8 * DAY
        result = self.tracker.summary(200, available=False)
        self.assertEqual(result["observed_seconds"], 0)
        self.assertEqual(result["consumed_pieces"], 0)


if __name__ == "__main__":
    unittest.main()
