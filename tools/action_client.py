#!/usr/bin/env python3
"""Authenticated HTTP client for Action Server operations (T4 & T5)."""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from common import canonical, request, write_json


def main():
    parser = argparse.ArgumentParser(description="Interact with the Lab 4 Action Server.")
    parser.add_argument(
        "command",
        choices=["snapshot", "anchor", "review", "pause", "resume", "dispatch"],
    )
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--decision-id")
    parser.add_argument("--approve", choices=["yes", "no"])
    parser.add_argument("--reason")
    parser.add_argument("--fail-after-effect", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args()

    path = "/" + args.command
    method = "GET"
    body = None

    if args.command == "review":
        method = "POST"
        body = {
            "decision_id": args.decision_id,
            "approve": {"yes": True, "no": False}.get(args.approve),
            "reason": args.reason,
        }
    elif args.command in ("pause", "resume"):
        path = "/control"
        method = "POST"
        body = {"paused": args.command == "pause"}
    elif args.command == "dispatch":
        method = "POST"
        body = {"fail_after_effect": args.fail_after_effect}

    status, result = request(args.url + path, os.environ["LAB4_TOKEN"], method, body)
    summary = (
        f"{len(result.get('decisions', []))} decisions"
        if args.command == "snapshot"
        else canonical(result)
    )
    print(status, summary)

    if args.out:
        write_json(args.out, {"http_status": status, "result": result})

    if status >= 400:
        sys.exit(2)


if __name__ == "__main__":
    main()
