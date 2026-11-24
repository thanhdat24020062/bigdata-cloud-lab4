#!/usr/bin/env python3
"""Bounded foreachBatch streaming adapter for T3.
Python mode is an explicit non-Spark functional fallback for local debugging.
"""
import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from common import (
    decision,
    events,
    load_impl,
    observation,
    read_json,
    request,
    validate_policy,
    verify_inputs,
    write_json,
)


def main():
    parser = argparse.ArgumentParser(description="Stream test events into the Action Server.")
    parser.add_argument("--mode", choices=["spark", "python"], default="spark")
    parser.add_argument("--impl", default="templates/decision_impl.py")
    parser.add_argument("--work", default="work")
    parser.add_argument("--input", default="data/events/test")
    parser.add_argument("--master", default="local[2]")
    parser.add_argument("--checkpoint", default="work/checkpoint-1")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--label", default="first")
    parser.add_argument("--trusted-manifest", required=True)
    args = parser.parse_args()

    if not args.label.replace("-", "").replace("_", "").isalnum():
        raise ValueError("simple alphanumeric run label required")

    root = Path.cwd()
    work_dir = Path(args.work)
    verify_inputs(root, args.trusted_manifest)

    module = load_impl(args.impl)
    baseline = read_json(work_dir / "baseline.json")
    handoff = read_json(work_dir / "handoff.json")
    contract = read_json(root / "schemas/decision_contract.json")
    policy = read_json(work_dir / "candidate_policy.json")
    validate_policy(policy, baseline, handoff, args.impl, contract)

    lock_receipt = read_json(work_dir / "lock_receipt.json")
    if lock_receipt["policy_id"] != policy["policy_id"]:
        raise ValueError("missing or mismatched policy lock receipt")

    token = os.environ["LAB4_PRODUCER_TOKEN"]
    batch_reports = []
    predictions = []

    def submit_batch(batch_events: list[dict], batch_id: int) -> None:
        batch_start = time.perf_counter()
        proposals = [decision(module, ev, baseline, policy, contract) for ev in batch_events]
        inserted_count = 0
        replay_count = 0

        for idx in range(0, len(proposals), 200):
            chunk = proposals[idx : idx + 200]
            status, resp = request(
                args.url + "/proposals",
                token,
                "POST",
                {"decisions": chunk},
            )
            if status != 200:
                raise RuntimeError(f"Service {status}: {resp}")
            inserted_count += resp["inserted"]
            replay_count += resp["replayed"]

        predictions.extend(proposals)
        batch_reports.append(
            {
                "batch_id": int(batch_id),
                "input_rows": len(batch_events),
                "new_decisions": inserted_count,
                "replays": replay_count,
                "callback_seconds": time.perf_counter() - batch_start,
            }
        )

    run_start = time.perf_counter()
    runtime_meta = {
        "mode": args.mode,
        "master": args.master,
        "host": platform.node(),
        "python": platform.python_version(),
    }

    if args.mode == "python":
        all_rows = events(args.input)
        if len(all_rows) > 10000:
            raise ValueError("bounded adapter input limit")
        for idx in range(0, len(all_rows), 200):
            submit_batch(all_rows[idx : idx + 200], idx // 200)
        runtime_meta["claim"] = (
            "Python HTTP batches only; NOT Spark/checkpoint/distributed evidence."
        )
    else:
        from pyspark.sql import SparkSession
        from pyspark.sql import types as T

        spark = (
            SparkSession.builder.master(args.master)
            .appName("Lab4-decisions")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        runtime_meta.update(
            spark=spark.version,
            application_id=spark.sparkContext.applicationId,
        )

        if args.master.startswith("local"):
            source_uri = Path(args.input).resolve().as_uri()
            checkpoint_uri = Path(args.checkpoint).resolve().as_uri()
        else:
            if not args.input.startswith(("s3a://", "hdfs://")) or not args.checkpoint.startswith(
                ("s3a://", "hdfs://")
            ):
                raise ValueError("shared input/checkpoint URIs required")
            source_uri = args.input
            checkpoint_uri = args.checkpoint

        schema = T.StructType(
            [T.StructField(col, T.StringType(), False) for col in contract["inherited_columns"]]
        )
        stream_df = (
            spark.readStream.schema(schema)
            .option("header", True)
            .option("maxFilesPerTrigger", 1)
            .csv(source_uri)
        )

        def foreach_batch_callback(df, batch_id: int) -> None:
            collected = df.limit(1001).collect()
            if len(collected) > 1000:
                raise ValueError("bounded callback limit exceeded")
            submit_batch([observation(row.asDict()) for row in collected], batch_id)

        query = (
            stream_df.writeStream.foreachBatch(foreach_batch_callback)
            .option("checkpointLocation", checkpoint_uri)
            .trigger(availableNow=True)
            .start()
        )
        query.awaitTermination()
        runtime_meta.update(query_id=str(query.id), run_id=str(query.runId))
        write_json(work_dir / f"stream_progress_{args.label}.json", json.loads(json.dumps(query.recentProgress, default=str)))
        spark.stop()

    runtime_meta["wall_seconds"] = time.perf_counter() - run_start
    write_json(
        work_dir / f"stream_run_{args.label}.json",
        {"runtime": runtime_meta, "batches": batch_reports},
    )
    write_json(work_dir / f"predictions_{args.label}.json", predictions)

    total_new = sum(r["new_decisions"] for r in batch_reports)
    total_replays = sum(r["replays"] for r in batch_reports)
    print(args.label, "new=", total_new, "replay=", total_replays)


if __name__ == "__main__":
    main()
