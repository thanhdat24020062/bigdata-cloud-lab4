#!/usr/bin/env python3
"""Self-check and package your Lab 4 submission for Portal upload.

Validates required files, policy/code hash lock, and excludes tokens/SQLite/checkpoints.
"""
import argparse
import csv
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from common import file_sha, read_json


REQUIRED_TEMPLATES = [
    "templates/decision_impl.py",
    "templates/audit_impl.py",
    "templates/contributions.csv",
    "templates/governance_tests.csv",
    "templates/report.md",
]

REQUIRED_WORK = [
    "work/input_check.json",
    "work/handoff.json",
    "work/gold_context.json",
    "work/baseline.json",
    "work/validation_report.json",
    "work/candidate_policy.json",
    "work/lock_receipt.json",
    "work/stream_run_first.json",
    "work/stream_run_replay.json",
    "work/G01.json",
    "work/G02.json",
    "work/G03.json",
    "work/G04.json",
    "work/G05.json",
    "work/G06.json",
    "work/before_approval.json",
    "work/pre_review.json",
    "work/review_01.json",
    "work/review_02.json",
    "work/review_03.json",
    "work/review_04.json",
    "work/review_05.json",
    "work/paused.json",
    "work/blocked.json",
    "work/resumed.json",
    "work/failure.json",
    "work/recovered.json",
    "work/repeated.json",
    "work/final_snapshot.json",
    "work/ta_anchor.json",
    "work/audit_challenge.json",
    "work/dashboard.html",
    "work/dashboard_summary.json",
    "work/lineage_dashboard.jsonl",
]


def main():
    parser = argparse.ArgumentParser(
        description="Validate and package Lab 4 submission ZIP for Portal upload."
    )
    parser.add_argument(
        "--team",
        default="team-01",
        help="Team identifier used in output ZIP filename.",
    )
    parser.add_argument(
        "--out",
        help="Output ZIP path (default: Lab4_Submission_<team>.zip)",
    )
    args = parser.parse_args()

    root = Path.cwd()
    missing = [rel for rel in REQUIRED_TEMPLATES + REQUIRED_WORK if not (root / rel).exists()]
    if missing:
        print("MISSING REQUIRED FILES:")
        for rel_path in missing:
            print(f"  - {rel_path}")
        raise SystemExit("Complete all tasks (T1-T5) before packaging your submission.")

    warnings = []
    if not (root / "work/stream_run_restart.json").exists():
        warnings.append(
            "work/stream_run_restart.json not found (did you run the 'restart' Spark control?)."
        )
    for label in ("first", "restart", "replay"):
        if not (root / f"work/stream_progress_{label}.json").exists():
            warnings.append(f"work/stream_progress_{label}.json not found (requires --mode spark).")

    code_sha = file_sha(root / "templates/decision_impl.py")
    policy = read_json(root / "work/candidate_policy.json")
    lock_receipt = read_json(root / "work/lock_receipt.json")
    if code_sha != policy.get("decision_code_sha256") or code_sha != lock_receipt.get(
        "decision_code_sha256"
    ):
        raise SystemExit(
            "HASH MISMATCH: templates/decision_impl.py was modified after calibrate / init_sandbox.py!\n"
            "Re-run calibrate and init_sandbox.py --reset (or restore the exact locked file)."
        )

    raw_snap = read_json(root / "work/final_snapshot.json")
    snapshot = raw_snap.get("result", raw_snap)
    if len(snapshot.get("decisions", [])) != 600:
        raise SystemExit(
            f"Expected 600 decisions in final_snapshot.json, got {len(snapshot.get('decisions', []))}."
        )
    if len(snapshot.get("tickets", [])) != 3:
        raise SystemExit(
            f"Expected 3 tickets in final_snapshot.json, got {len(snapshot.get('tickets', []))}."
        )
    rejected_count = sum(
        1 for item in snapshot.get("decisions", []) if item.get("status") == "REJECTED"
    )
    if rejected_count != 2:
        raise SystemExit(
            f"Expected 2 REJECTED decisions in final_snapshot.json, got {rejected_count}."
        )

    audit_cases = read_json(root / "work/audit_challenge.json")
    if not (
        isinstance(audit_cases, list)
        and len(audit_cases) == 4
        and all(item.get("pass") for item in audit_cases)
    ):
        raise SystemExit("work/audit_challenge.json did not pass all 4 audit mutation cases.")

    with (root / "templates/governance_tests.csv").open(encoding="utf-8", newline="") as f:
        gov_rows = list(csv.DictReader(f))
    unfilled_gov = [
        row["id"]
        for row in gov_rows
        if not (row.get("observed", "").strip() and row.get("evidence_path", "").strip())
    ]
    if unfilled_gov:
        warnings.append(
            f"templates/governance_tests.csv still has empty observed/evidence_path for: {', '.join(unfilled_gov)}"
        )

    with (root / "templates/contributions.csv").open(encoding="utf-8", newline="") as f:
        contrib_rows = list(csv.DictReader(f))
    unfilled_contrib = [
        row["member"] for row in contrib_rows if not row.get("evidence_path", "").strip()
    ]
    if unfilled_contrib:
        warnings.append(
            f"templates/contributions.csv still has empty fields for members: {', '.join(unfilled_contrib)}"
        )

    out_zip = Path(args.out) if args.out else (root / f"Lab4_Submission_{args.team}.zip")
    included = []
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in REQUIRED_TEMPLATES:
            zf.write(root / rel, rel)
            included.append(rel)
        for work_file in sorted((root / "work").rglob("*")):
            if not work_file.is_file():
                continue
            rel_parts = work_file.relative_to(root).parts
            if (
                "sandbox" in rel_parts
                or any(part.startswith("checkpoint") for part in rel_parts)
                or "__pycache__" in rel_parts
            ):
                continue
            if work_file.suffix in (".token", ".sqlite", ".db", ".pyc"):
                continue
            arcname = work_file.relative_to(root).as_posix()
            zf.write(work_file, arcname)
            included.append(arcname)

    print(f"Created clean Portal submission package: {out_zip} ({len(included)} files)")
    print("Excluded work/sandbox/ (tokens & SQLite DBs) and work/checkpoint-* automatically.")
    if warnings:
        print("WARNINGS to review before final upload:")
        for msg in warnings:
            print(f"  [!] {msg}")
    else:
        print("All automated pre-submission KPI checks PASSED!")


if __name__ == "__main__":
    main()
