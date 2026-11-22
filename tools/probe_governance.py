#!/usr/bin/env python3
"""Governance negative-probe runner for T4 (G01-G06).
Run each probe using the corresponding role credential without pooling tokens.
"""
import argparse
import copy
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from common import request, write_json


PROBES = [
    "bad-token",
    "self-review",
    "viewer-review",
    "short-reason",
    "nonalert-review",
    "changed-payload",
]

EXPECTED_ERRORS = {
    "bad-token": "invalid credential",
    "self-review": "self approval prohibited",
    "viewer-review": "role required: reviewer",
    "short-reason": "boolean approve and reason >=8 characters required",
    "nonalert-review": "not pending; review is immutable",
    "changed-payload": "decision differs from locked rule/data",
}


def main():
    parser = argparse.ArgumentParser(description="Execute a single governance negative probe.")
    parser.add_argument("probe", choices=PROBES)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    token = os.environ.get("LAB4_TOKEN", "")

    if args.probe == "bad-token":
        status, resp = request(args.url + "/snapshot", "invalid-token")
        expected_status = 401
    else:
        snap_code, snapshot = request(args.url + "/snapshot", token)
        if snap_code != 200:
            raise ValueError("authenticated readable snapshot required")

        target = next(item for item in snapshot["decisions"] if item["status"] == "PENDING")

        if args.probe == "changed-payload":
            tampered = copy.deepcopy(target["payload"])
            tampered["score"] = "999.000000"
            status, resp = request(
                args.url + "/proposals",
                token,
                "POST",
                {"decisions": [tampered]},
            )
            expected_status = 409
        else:
            if args.probe == "nonalert-review":
                target = next(
                    item
                    for item in snapshot["decisions"]
                    if item["payload"]["route"] in ("NORMAL", "HOLD")
                )
            expected_status = {
                "self-review": 403,
                "viewer-review": 403,
                "short-reason": 400,
                "nonalert-review": 409,
            }[args.probe]
            reason = "ok" if args.probe == "short-reason" else "Controlled negative test"
            status, resp = request(
                args.url + "/review",
                token,
                "POST",
                {
                    "decision_id": target["id"],
                    "approve": True,
                    "reason": reason,
                },
            )

    expected_error = EXPECTED_ERRORS[args.probe]
    passed = status == expected_status and resp.get("error") == expected_error

    write_json(
        args.out,
        {
            "probe": args.probe,
            "expected": expected_status,
            "observed": status,
            "expected_error": expected_error,
            "passed": passed,
            "response": resp,
        },
    )
    print(args.probe, status)
    if not passed:
        sys.exit(2)


if __name__ == "__main__":
    main()
