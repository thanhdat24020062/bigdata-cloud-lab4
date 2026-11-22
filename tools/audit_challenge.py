#!/usr/bin/env python3
"""Audit tamper-detection challenge runner for T5 (G12)."""
import argparse
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from common import digest, load_impl, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description="Verify audit hash-chain tamper detection.")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--impl", default="templates/audit_impl.py")
    parser.add_argument("--out", default="work/audit_challenge.json")
    args = parser.parse_args()

    raw_snap = read_json(args.snapshot)
    snapshot = raw_snap.get("result", raw_snap)
    raw_anchor = read_json(args.anchor)
    anchor = raw_anchor.get("result", raw_anchor)

    log = snapshot["audit"]
    if not log:
        raise ValueError("complete the action workflow first")

    module = load_impl(args.impl)

    edited_log = copy.deepcopy(log)
    edited_log[0]["actor"] = "forged"

    rehashed_log = copy.deepcopy(edited_log)
    prev_hash = "0" * 64
    for entry in rehashed_log:
        entry["prev_hash"] = prev_hash
        entry["hash"] = digest({k: v for k, v in entry.items() if k != "hash"})
        prev_hash = entry["hash"]

    cases = [
        ("original", log, True),
        ("edited", edited_log, False),
        ("truncated", log[:-1], False),
        ("rehash_with_old_anchor", rehashed_log, False),
    ]

    results = []
    for case_name, candidate_log, expected in cases:
        observed = module.verify_audit(candidate_log, anchor)
        results.append(
            {
                "case": case_name,
                "expected": expected,
                "observed": observed,
                "pass": observed is expected,
            }
        )

    write_json(args.out, results)
    if not all(item["pass"] for item in results):
        raise SystemExit("audit challenge failed")
    print("Four audit cases passed.")


if __name__ == "__main__":
    main()
