"""Extra tests for verify_audit (Role E). Run from the starter root:
    python -m unittest discover -s tests -p "test_audit*.py" -v
"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from common import digest, load_impl


def build_chain(n=4):
    log, prev = [], "0" * 64
    for i in range(1, n + 1):
        e = {
            "seq": i,
            "time_utc": f"2026-02-12T00:00:0{i}+00:00",
            "actor": "member-A",
            "action": "PROPOSE",
            "object_id": f"d{i}",
            "details": {"reason": "tiếng Việt ✓", "n": i},
            "prev_hash": prev,
        }
        e["hash"] = digest(e)
        log.append(e)
        prev = e["hash"]
    return log, {"count": n, "last_hash": prev}


class VerifyAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v = staticmethod(load_impl("templates/audit_impl.py").verify_audit)

    def setUp(self):
        self.log, self.anchor = build_chain()

    # --- the 4 official challenge cases -----------------------------------
    def test_original(self):
        self.assertIs(self.v(self.log, self.anchor), True)

    def test_edited(self):
        self.log[0]["actor"] = "forged"
        self.assertIs(self.v(self.log, self.anchor), False)

    def test_truncated(self):
        self.assertIs(self.v(self.log[:-1], self.anchor), False)

    def test_rehash_with_old_anchor(self):
        forged = copy.deepcopy(self.log)
        forged[0]["actor"] = "forged"
        prev = "0" * 64
        for e in forged:
            e["prev_hash"] = prev
            e["hash"] = digest({k: v for k, v in e.items() if k != "hash"})
            prev = e["hash"]
        # chain is internally consistent ...
        self.assertIs(self.v(forged, {"count": len(forged), "last_hash": prev}), True)
        # ... but fails against the independent anchor
        self.assertIs(self.v(forged, self.anchor), False)

    # --- empty log ----------------------------------------------------------
    def test_empty_ok(self):
        self.assertIs(self.v([], {"count": 0, "last_hash": "0" * 64}), True)

    def test_empty_bad_anchor(self):
        self.assertIs(self.v([], {"count": 0, "last_hash": "1" * 64}), False)
        self.assertIs(self.v([], {"count": 1, "last_hash": "0" * 64}), False)
        self.assertIs(self.v([], self.anchor), False)

    # --- structural / malformed ---------------------------------------------
    def test_missing_field(self):
        del self.log[1]["actor"]
        self.assertIs(self.v(self.log, self.anchor), False)

    def test_extra_field(self):
        self.log[1]["extra"] = 1
        self.assertIs(self.v(self.log, self.anchor), False)

    def test_not_a_dict_entry(self):
        self.log[2] = "oops"
        self.assertIs(self.v(self.log, self.anchor), False)

    def test_none_inputs(self):
        self.assertIs(self.v(None, self.anchor), False)
        self.assertIs(self.v(self.log, None), False)
        self.assertIs(self.v(self.log, {}), False)

    def test_seq_gap_swap_and_bool(self):
        swapped = [self.log[1], self.log[0], *self.log[2:]]
        self.assertIs(self.v(swapped, self.anchor), False)
        self.log[0]["seq"] = True  # True == 1 in Python
        self.log[0]["hash"] = digest({k: v for k, v in self.log[0].items() if k != "hash"})
        self.assertIs(self.v(self.log, self.anchor), False)

    def test_nan_in_details_returns_false_not_raise(self):
        self.log[1]["details"] = {"x": float("nan")}
        self.assertIs(self.v(self.log, self.anchor), False)

    def test_anchor_count_wrong_type(self):
        self.assertIs(self.v(self.log, {**self.anchor, "count": "4"}), False)
        self.assertIs(self.v(self.log, {**self.anchor, "count": 4.0}), False)
        self.assertIs(self.v(self.log, {**self.anchor, "count": True}), False)

    def test_returns_real_bool(self):
        self.assertIsInstance(self.v(self.log, self.anchor), bool)


if __name__ == "__main__":
    unittest.main()
