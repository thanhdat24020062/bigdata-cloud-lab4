#!/usr/bin/env python3
"""Self-contained HTML dashboard and OpenLineage renderer for T5."""
import argparse
import html
import math
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from common import canonical, digest, load_impl, read_json, write_json


def render_table(headers: list, rows: list[list]) -> str:
    esc = lambda val: html.escape(str(val))
    header_html = "".join(f"<th>{esc(col)}</th>" for col in headers)
    body_html = "".join(
        "<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table><tr>{header_html}</tr>{body_html}</table>"


def main():
    parser = argparse.ArgumentParser(description="Build Lab 4 HTML dashboard and OpenLineage events.")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--work", default="work")
    parser.add_argument("--audit-impl", default="templates/audit_impl.py")
    args = parser.parse_args()

    start_utc = datetime.now(timezone.utc).isoformat()
    work_dir = Path(args.work)

    raw_snap = read_json(args.snapshot)
    snapshot = raw_snap.get("result", raw_snap)
    raw_anchor = read_json(args.anchor)
    anchor = raw_anchor.get("result", raw_anchor)

    audit_mod = load_impl(args.audit_impl)
    if not audit_mod.verify_audit(snapshot["audit"], anchor):
        raise ValueError("audit or independent anchor mismatch")

    val_report = read_json(work_dir / "validation_report.json")
    gold_rows = read_json(work_dir / "gold_context.json")
    handoff = read_json(work_dir / "handoff.json")
    policy = read_json(work_dir / "candidate_policy.json")

    if snapshot["policy_id"] != policy["policy_id"]:
        raise ValueError("snapshot/policy mismatch")

    expected_gold = read_json(Path(__file__).resolve().parents[1] / "data/upstream/gold_lab3.json")
    if digest(sorted(gold_rows, key=lambda r: (r["site"], r["event_date"]))) != digest(
        sorted(expected_gold, key=lambda r: (r["site"], r["event_date"]))
    ):
        raise ValueError("historical Gold context mismatch")

    sealed_test = (
        read_json(work_dir / "test_metrics_TA.json")
        if (work_dir / "test_metrics_TA.json").exists()
        else None
    )
    if sealed_test and sealed_test["policy_id"] != policy["policy_id"]:
        raise ValueError("test report is for another policy")

    routes = Counter(d["payload"]["route"] for d in snapshot["decisions"])
    statuses = Counter(d["status"] for d in snapshot["decisions"])
    stream_files = sorted(work_dir.glob("stream_run_*.json"))

    callback_times = []
    for sfile in stream_files:
        for batch in read_json(sfile)["batches"]:
            if batch["input_rows"] > 0:
                callback_times.append(batch["callback_seconds"])
    callback_times.sort()
    p95_seconds = (
        callback_times[math.ceil(0.95 * len(callback_times)) - 1] if callback_times else None
    )

    site_totals = defaultdict(lambda: {"n": 0, "sum": Decimal("0"), "late": 0})
    for row in gold_rows:
        site_totals[row["site"]]["n"] += row["n_observations"]
        site_totals[row["site"]]["sum"] += Decimal(row["sum_temperature_c"])
        site_totals[row["site"]]["late"] += row["n_late"]

    summary = {
        "decision_count": len(snapshot["decisions"]),
        "routes": dict(routes),
        "statuses": dict(statuses),
        "receiver_tickets": len(snapshot["tickets"]),
        "audit_ok": True,
        "audit_anchor": anchor,
        "callback_sample_count": len(callback_times),
        "callback_p95_seconds": p95_seconds,
        "timing_scope": "Observed bounded callback time, not event-to-action latency or an SLO.",
        "historical_gold_rows": len(gold_rows),
    }
    write_json(work_dir / "dashboard_summary.json", summary)

    esc = lambda val: html.escape(str(val))
    html_parts = [
        '<!doctype html><meta charset="utf-8"><title>Lab 4 Decision Support</title>',
        (
            "<style>"
            "body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px;color:#17344a}"
            "h1,h2{color:#12536d}"
            "table{border-collapse:collapse;width:100%;margin:20px 0}"
            "th,td{padding:9px;border-bottom:1px solid #cdd9df;text-align:left}"
            "th{background:#eaf3f5}"
            ".bar{height:14px;display:block;background:#267f96}"
            "code{overflow-wrap:anywhere}"
            "</style>"
        ),
        (
            "<h1>Decision Support & Data-Driven Action</h1>"
            "<p>Sandbox inspection only. Synthetic outcomes are not real fault ground truth.</p>"
            f"<p>Locked policy: <code>{esc(policy['policy_id'])}</code></p>"
        ),
        (
            "<h2>1. Historical lakehouse context</h2>"
            f"<p>Lab 3 Gold; weighted means computed from sums and counts. Handoff mode: {esc(handoff['mode'])}. "
            "Not a training-label table.</p>"
        ),
        render_table(
            ["Site", "Observations", "Weighted mean C", "Late"],
            [
                [site, vals["n"], format(vals["sum"] / vals["n"], ".4f"), vals["late"]]
                for site, vals in sorted(site_totals.items())
            ],
        ),
        "<h2>2. Validation-only selection</h2>",
        render_table(
            ["Threshold", "TP", "FP", "FN", "HOLD", "F1", "Proxy cost"],
            [
                [
                    cand["threshold"],
                    cand["metrics"]["tp"],
                    cand["metrics"]["fp"],
                    cand["metrics"]["fn"],
                    cand["metrics"]["hold"],
                    cand["metrics"]["f1"],
                    cand["metrics"]["cost"],
                ]
                for cand in val_report["candidates"]
            ],
        ),
        render_table(
            ["Comparator", "TP", "FP", "FN", "Coverage", "Cost"],
            [
                [name, m["tp"], m["fp"], m["fn"], m["coverage"], m["cost"]]
                for name, m in val_report["comparators"].items()
            ],
        ),
        "<h2>3. Decisions and review queue</h2>",
    ]

    total_decisions = max(1, len(snapshot["decisions"]))
    for route_name in ("NORMAL", "ALERT", "HOLD"):
        pct = 100 * routes[route_name] / total_decisions
        html_parts.append(
            f'<p>{route_name}: {routes[route_name]}<span class="bar" style="width:{pct}%"></span></p>'
        )

    html_parts.extend(
        [
            render_table(["Status", "Count"], sorted(statuses.items())),
            (
                f"<p>Distinct receiver tickets: {len(snapshot['tickets'])}. "
                "Only the five selected examples are reviewed; pending alerts are not delivered.</p>"
            ),
            "<h2>4. Observability</h2>",
            render_table(
                ["Metric", "Value"],
                [
                    ["Callback samples", len(callback_times)],
                    ["Nearest-rank p95 seconds", p95_seconds],
                    ["Audit entries", anchor["count"]],
                ],
            ),
            "<p>These measured callback times do not establish real-time field performance.</p>",
            "<h2>5. Sealed test evaluation</h2>",
        ]
    )

    if sealed_test:
        html_parts.extend(
            [
                render_table(["Metric", "Value"], list(sealed_test["overall"].items())),
                render_table(
                    ["Site", "TP", "FP", "FN", "HOLD", "F1", "End-to-end recall"],
                    [
                        [
                            site,
                            m["tp"],
                            m["fp"],
                            m["fn"],
                            m["hold"],
                            m["f1"],
                            m["end_to_end_recall"],
                        ]
                        for site, m in sorted(sealed_test["by_site"].items())
                    ],
                ),
            ]
        )
    else:
        html_parts.append(
            "<p>Sealed test metrics are computed during automated grading against held-out test labels. "
            "Do not invent results or retune after test feedback.</p>"
        )

    html_parts.extend(
        [
            "<h2>6. Auditable action trace</h2>",
            render_table(
                ["Decision", "Record", "Status", "Proposer", "Reviewer"],
                [
                    [
                        d["id"][:16],
                        d["payload"]["event"]["record_id"],
                        d["status"],
                        d["proposer"],
                        d["reviewer"],
                    ]
                    for d in snapshot["decisions"]
                    if d["status"] in ("DISPATCHED", "REJECTED")
                ],
            ),
        ]
    )

    (work_dir / "dashboard.html").write_text("\n".join(html_parts), encoding="utf-8")

    lineage_inputs = [
        Path(args.snapshot),
        Path(args.anchor),
        work_dir / "validation_report.json",
        work_dir / "gold_context.json",
        work_dir / "handoff.json",
        work_dir / "candidate_policy.json",
        Path(__file__).resolve().parents[1] / "data/upstream/gold_lab3.json",
        *stream_files,
    ]
    if sealed_test:
        lineage_inputs.append(work_dir / "test_metrics_TA.json")

    to_dataset = lambda p: {"namespace": p.resolve().parent.as_uri(), "name": p.name}
    base_event = {
        "producer": "urn:uet-dis:lab4:dashboard:v1",
        "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
        "run": {"runId": str(uuid.uuid4())},
        "job": {"namespace": "urn:uet-dis:lab4", "name": "dashboard-render"},
        "inputs": [to_dataset(p) for p in lineage_inputs],
    }
    lineage_pair = [
        {**base_event, "eventType": "START", "eventTime": start_utc},
        {
            **base_event,
            "eventType": "COMPLETE",
            "eventTime": datetime.now(timezone.utc).isoformat(),
            "outputs": [
                to_dataset(work_dir / "dashboard.html"),
                to_dataset(work_dir / "dashboard_summary.json"),
            ],
        },
    ]
    (work_dir / "lineage_dashboard.jsonl").write_text(
        "".join(canonical(ev) + "\n" for ev in lineage_pair),
        encoding="utf-8",
    )
    print("Dashboard and rendering-run lineage written; audit verified.")


if __name__ == "__main__":
    main()
