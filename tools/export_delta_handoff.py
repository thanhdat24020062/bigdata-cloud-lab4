#!/usr/bin/env python3
"""Optional read-only Delta Lake version-pinned export adapter from Lab 3."""
import argparse
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from common import accept_handoff, canonical, file_sha, observation, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description="Export pinned Silver/Gold Delta versions from Lab 3.")
    parser.add_argument("--silver", required=True)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--silver-version", type=int, required=True)
    parser.add_argument("--gold-version", type=int, required=True)
    parser.add_argument("--gold-source-silver-version", type=int, required=True)
    parser.add_argument("--ta-approval-reference", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if (
        min(args.silver_version, args.gold_version) < 0
        or args.silver_version != args.gold_source_silver_version
    ):
        raise ValueError("explicit bound versions required")

    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F

    spark = SparkSession.builder.appName("Lab4-pinned-export").getOrCreate()
    spark.conf.set("spark.sql.session.timeZone", "UTC")

    inherited_cols = read_json("schemas/decision_contract.json")["inherited_columns"]
    silver_df = (
        spark.read.format("delta")
        .option("versionAsOf", args.silver_version)
        .load(args.silver)
        .select(*inherited_cols)
    )
    for ts_col in ("event_time_utc", "ingest_time_utc"):
        silver_df = silver_df.withColumn(
            ts_col, F.date_format(F.col(ts_col), "yyyy-MM-dd'T'HH:mm:ss'Z'")
        )

    silver_rows = [observation(r.asDict()) for r in silver_df.limit(25001).collect()]

    gold_rows = []
    raw_gold = (
        spark.read.format("delta")
        .option("versionAsOf", args.gold_version)
        .load(args.gold)
        .limit(25001)
        .collect()
    )
    for row in raw_gold:
        item = row.asDict()
        for key, val in list(item.items()):
            if isinstance(val, (datetime, date)):
                item[key] = val.isoformat()
            elif isinstance(val, Decimal):
                fmt = ".6f" if key in ("mean_temperature_c", "late_rate") else ".2f"
                item[key] = format(val, fmt)
        gold_rows.append(item)

    if max(len(silver_rows), len(gold_rows)) > 25000:
        raise ValueError("bounded export limit")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "silver_lab3.jsonl").write_text(
        "".join(canonical(r) + "\n" for r in sorted(silver_rows, key=lambda x: x["record_id"])),
        encoding="utf-8",
    )
    write_json(out_dir / "gold_lab3.json", sorted(gold_rows, key=lambda x: (x["site"], x["event_date"])))
    write_json(
        out_dir / "handoff.json",
        {
            "mode": "team-delta-export",
            "silver_table_uri": args.silver,
            "gold_table_uri": args.gold,
            "silver_delta_version": args.silver_version,
            "gold_delta_version": args.gold_version,
            "gold_source_silver_version": args.gold_source_silver_version,
            "ta_approval_reference": args.ta_approval_reference,
            "silver_rows": len(silver_rows),
            "gold_rows": len(gold_rows),
            "silver_sha256": file_sha(out_dir / "silver_lab3.jsonl"),
            "gold_sha256": file_sha(out_dir / "gold_lab3.json"),
            "note": "Pinned Delta Lake export verified against reference contract.",
        },
    )
    accept_handoff(Path.cwd(), out_dir)
    spark.stop()
    print("Pinned logical handoff verified.")


if __name__ == "__main__":
    main()
