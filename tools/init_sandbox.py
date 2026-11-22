#!/usr/bin/env python3
"""Initialize a local self-simulated sandbox after T2 calibrate.

Creates work/lock_receipt.json, work/sandbox/service_config.json, and 4 role tokens.
Do NOT include work/sandbox/ (tokens or SQLite DBs) in your final submission.
"""
import argparse
import hashlib
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from common import (
    decision,
    digest,
    events,
    file_sha,
    load_impl,
    read_json,
    validate_policy,
    verify_inputs,
    write_json,
)


def main():
    parser = argparse.ArgumentParser(
        description="Prepare local Action Server sandbox from your frozen policy."
    )
    parser.add_argument("--work", default="work")
    parser.add_argument("--impl", default="templates/decision_impl.py")
    parser.add_argument("--trusted-manifest", default="manifests/inputs_convenience.json")
    parser.add_argument("--sandbox-dir", default="work/sandbox")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear existing local sandbox state/databases before creating.",
    )
    args = parser.parse_args()

    root = Path.cwd()
    work_dir = Path(args.work)
    sandbox_dir = Path(args.sandbox_dir)

    verify_inputs(root, args.trusted_manifest)
    baseline = read_json(work_dir / "baseline.json")
    handoff = read_json(work_dir / "handoff.json")
    contract = read_json(root / "schemas/decision_contract.json")
    policy = read_json(work_dir / "candidate_policy.json")
    validate_policy(policy, baseline, handoff, args.impl, contract)

    if sandbox_dir.exists():
        if not args.reset:
            raise SystemExit(
                f"Sandbox '{sandbox_dir}' already exists. Pass --reset if you want a fresh sandbox."
            )
        shutil.rmtree(sandbox_dir)

    module = load_impl(args.impl)
    expected = {}
    for ev in events(root / "data/events/test"):
        item = decision(module, ev, baseline, policy, contract)
        expected[item["decision_id"]] = item

    tokens_dir = sandbox_dir / "tokens"
    tokens_dir.mkdir(parents=True, exist_ok=True)

    roles = {
        "producer": ("member-A", ["producer", "reviewer", "reader"]),
        "reviewer": ("member-D", ["reviewer", "reader"]),
        "analyst": ("member-E", ["reader"]),
        "operator": ("sim-operator", ["owner", "dispatcher", "reader"]),
    }
    identities = {}
    for role_name, (actor_id, role_list) in roles.items():
        token = secrets.token_urlsafe(24)
        (tokens_dir / f"{role_name}.token").write_text(token + "\n", encoding="utf-8")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        identities[token_hash] = {"id": actor_id, "roles": role_list}

    write_json(
        sandbox_dir / "service_config.json",
        {
            "policy": policy,
            "identities": identities,
            "expected": expected,
        },
    )

    receipt = {
        "policy_id": policy["policy_id"],
        "locked_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "student-self-simulation",
        "decision_code_sha256": file_sha(args.impl),
        "trusted_input_manifest_sha256": file_sha(args.trusted_manifest),
        "handoff_sha256": digest(handoff),
    }
    write_json(work_dir / "lock_receipt.json", receipt)

    print("Local sandbox initialized:")
    print(f"  Lock receipt : {work_dir / 'lock_receipt.json'}")
    print(f"  Tokens dir   : {tokens_dir}  (DO NOT commit or submit tokens/DBs!)")
    print("Start your group Action Server in a separate terminal:")
    print(
        f"  python src/action_server.py "
        f"--config {sandbox_dir / 'service_config.json'} "
        f"--state {sandbox_dir / 'databases'} --port 8765"
    )


if __name__ == "__main__":
    main()
