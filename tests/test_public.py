"""Public toy unit tests for templates/decision_impl.py."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from common import load_impl, read_json


class PublicDecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        impl_path = os.environ.get("LAB4_IMPL", "templates/decision_impl.py")
        cls.module = load_impl(impl_path)
        cls.contract = read_json("schemas/decision_contract.json")
        cls.sample_event = {
            "record_id": "toy",
            "sensor_id": "S01",
            "site": "zone-1",
            "event_time_utc": "2026-02-10T08:00:00Z",
            "ingest_time_utc": "2026-02-10T08:01:00Z",
            "is_late": False,
            "temperature_c": "25.00",
            "source_object": "toy.csv",
            "source_row": 1,
            "source_sha256": "a" * 64,
        }
        cls.sample_baseline = {
            "S01": {
                "n": 40,
                "median_c": "20.00",
                "mad_c": "1.00",
                "scale_c": "2.000000",
            }
        }

    def test_threshold_equality(self):
        res = self.module.score_event(
            self.sample_event, self.sample_baseline, "2.50", self.contract
        )
        self.assertEqual(res["route"], "ALERT")

    def test_below(self):
        res = self.module.score_event(
            self.sample_event, self.sample_baseline, "3", self.contract
        )
        self.assertEqual(res["route"], "NORMAL")

    def test_late(self):
        late_event = {**self.sample_event, "ingest_time_utc": "2026-02-10T08:20:00Z"}
        res = self.module.score_event(
            late_event, self.sample_baseline, "2", self.contract
        )
        self.assertEqual((res["route"], res["score"]), ("HOLD", "2.500000"))

    def test_missing_baseline(self):
        res = self.module.score_event(self.sample_event, {}, "2", self.contract)
        self.assertEqual(res["reason"], "INSUFFICIENT_BASELINE")

    def test_key_repeat(self):
        key1 = self.module.make_action_key("g", self.sample_event, "p")
        key2 = self.module.make_action_key("g", dict(self.sample_event), "p")
        self.assertEqual(key1, key2)

    def test_key_policy(self):
        key1 = self.module.make_action_key("g", self.sample_event, "p1")
        key2 = self.module.make_action_key("g", self.sample_event, "p2")
        self.assertNotEqual(key1, key2)

    def test_denominators(self):
        predictions = [
            {"record_id": "a", "route": "ALERT"},
            {"record_id": "b", "route": "NORMAL"},
            {"record_id": "c", "route": "HOLD"},
        ]
        labels = {"a": 1, "b": 1, "c": 1}
        metrics = self.module.evaluate(predictions, labels, self.contract)
        self.assertEqual(
            (metrics["tp"], metrics["fn"], metrics["held_positive"]),
            (1, 1, 1),
        )
        self.assertEqual(metrics["end_to_end_recall"], 1 / 3)

    def test_label_mismatch(self):
        with self.assertRaises(ValueError):
            self.module.evaluate(
                [{"record_id": "a", "route": "NORMAL"}],
                {},
                self.contract,
            )


if __name__ == "__main__":
    unittest.main()
