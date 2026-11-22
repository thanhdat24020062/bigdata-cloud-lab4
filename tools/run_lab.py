#!/usr/bin/env python3
"""Offline CLI runner for T1 (preflight, baseline) and T2 (calibrate)."""
import argparse
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from common import (
    accept_handoff,
    events,
    file_sha,
    labels,
    load_impl,
    policy_from,
    read_json,
    scored,
    verify_inputs,
    write_json,
)


def main():
    parser = argparse.ArgumentParser(description="Run offline Lab 4 T1/T2 pipeline steps.")
    parser.add_argument("step", choices=["preflight", "baseline", "calibrate"])
    parser.add_argument("--trusted-manifest", required=True)
    parser.add_argument("--impl", default="templates/decision_impl.py")
    parser.add_argument("--team", default="team-01")
    parser.add_argument("--upstream", default="data/upstream")
    parser.add_argument("--out", default="work")
    args = parser.parse_args()

    root = Path.cwd()
    out_dir = Path(args.out)

    checked = verify_inputs(root, args.trusted_manifest)
    silver_rows, gold_rows, handoff = accept_handoff(root, args.upstream)

    write_json(
        out_dir / "input_check.json",
        {
            "verified": checked,
            "trusted_manifest_sha256": file_sha(args.trusted_manifest),
            "handoff_mode": handoff["mode"],
            "silver_rows": len(silver_rows),
            "gold_rows": len(gold_rows),
            "history_max_event": max(r["event_time_utc"] for r in silver_rows),
            "history_max_ingest": max(r["ingest_time_utc"] for r in silver_rows),
        },
    )
    write_json(out_dir / "handoff.json", handoff)
    write_json(out_dir / "gold_context.json", gold_rows)

    if args.step == "preflight":
        print("Inputs verified.")
        return

    module = load_impl(args.impl)
    contract = read_json(root / "schemas/decision_contract.json")
    baseline = module.fit_baseline(silver_rows, contract)
    write_json(out_dir / "baseline.json", baseline)

    if args.step == "baseline":
        print("Baseline written.")
        return

    val_events = events(root / "data/events/validation.csv")
    val_labels = labels(root / "data/labels/validation_labels.csv")

    candidates = []
    for threshold in contract["thresholds"]:
        preds_for_t = scored(module, val_events, baseline, threshold, contract)
        candidates.append(
            {
                "threshold": threshold,
                "metrics": module.evaluate(preds_for_t, val_labels, contract),
            }
        )

    best = min(
        candidates,
        key=lambda item: (
            item["metrics"]["cost_units"],
            item["metrics"]["fn"],
            item["metrics"]["fp"],
            -Decimal(item["threshold"]),
        ),
    )
    best_preds = scored(module, val_events, baseline, best["threshold"], contract)

    comparators = {}
    for comp_name in ("never_alert", "temperature_ge_30"):
        comp_preds = [
            {
                "record_id": ev["record_id"],
                "route": "HOLD"
                if pred["route"] == "HOLD"
                else (
                    "ALERT"
                    if comp_name == "temperature_ge_30" and Decimal(ev["temperature_c"]) >= 30
                    else "NORMAL"
                ),
            }
            for ev, pred in zip(val_events, best_preds)
        ]
        comparators[comp_name] = module.evaluate(comp_preds, val_labels, contract)

    write_json(
        out_dir / "validation_report.json",
        {
            "candidates": candidates,
            "selected": best,
            "comparators": comparators,
        },
    )
    write_json(out_dir / "validation_predictions.json", best_preds)
    write_json(
        out_dir / "candidate_policy.json",
        policy_from(args.team, best["threshold"], baseline, handoff, args.impl, contract),
    )
    print(
        f"Validation complete (selected threshold = {best['threshold']}). "
        "Freeze templates/decision_impl.py and run python tools/init_sandbox.py."
    )


if __name__ == "__main__":
    main()
