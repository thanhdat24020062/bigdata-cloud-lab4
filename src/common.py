"""Supplied I/O and content-binding helpers; no assessed decision logic."""
import csv
import hashlib
import importlib.util
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


def canonical(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_sha(path) -> str:
    hasher = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    target.write_text(payload, encoding="utf-8")


def load_impl(path):
    spec = importlib.util.spec_from_file_location("lab4_impl", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def utc(iso_str: str) -> datetime:
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(timezone.utc)


def csvrows(path) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def observation(row: dict) -> dict:
    item = dict(row)
    if not isinstance(item["is_late"], bool):
        raw_late = str(item["is_late"]).lower()
        if raw_late not in ("true", "false"):
            raise ValueError("invalid is_late")
        item["is_late"] = raw_late == "true"
    item["source_row"] = int(item["source_row"])
    item["temperature_c"] = format(Decimal(str(item["temperature_c"])), ".2f")
    return item


def history(path) -> list[dict]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [observation(json.loads(line)) for line in lines if line.strip()]


def events(path) -> list[dict]:
    target = Path(path)
    files = sorted(target.glob("*.csv")) if target.is_dir() else [target]
    return [observation(row) for file_path in files for row in csvrows(file_path)]


def labels(path) -> dict[str, int]:
    rows = csvrows(path)
    label_map = {row["record_id"]: int(row["scenario_positive"]) for row in rows}
    if len(label_map) != len(rows) or any(val not in (0, 1) for val in label_map.values()):
        raise ValueError("invalid label population")
    return label_map


def verify_inputs(root, manifest) -> list[str]:
    root_path = Path(root).resolve()
    checked = []
    for entry in read_json(manifest)["files"]:
        file_path = (root_path / entry["path"]).resolve()
        if (
            not file_path.is_relative_to(root_path)
            or not file_path.is_file()
            or file_path.stat().st_size != entry["bytes"]
            or file_sha(file_path) != entry["sha256"]
        ):
            raise ValueError("input mismatch: " + entry["path"])
        checked.append(entry["path"])
    return checked


def accept_handoff(root, upstream_dir):
    root_path = Path(root)
    upstream_path = Path(upstream_dir)
    ref_dir = root_path / "data/upstream"

    silver_rows = history(upstream_path / "silver_lab3.jsonl")
    gold_rows = read_json(upstream_path / "gold_lab3.json")
    binding = read_json(upstream_path / "handoff.json")

    ref_silver = history(ref_dir / "silver_lab3.jsonl")
    if digest(sorted(silver_rows, key=lambda x: x["record_id"])) != digest(
        sorted(ref_silver, key=lambda x: x["record_id"])
    ):
        raise ValueError("Silver differs from fixed fixture: recalibration required")

    ref_gold = read_json(ref_dir / "gold_lab3.json")
    if digest(sorted(gold_rows, key=lambda x: (x["site"], x["event_date"]))) != digest(
        sorted(ref_gold, key=lambda x: (x["site"], x["event_date"]))
    ):
        raise ValueError("Gold mismatch")

    for filename, hash_key in [
        ("silver_lab3.jsonl", "silver_sha256"),
        ("gold_lab3.json", "gold_sha256"),
    ]:
        if binding[hash_key] != file_sha(upstream_path / filename):
            raise ValueError("handoff bytes mismatch")

    if binding["mode"] == "team-delta-export":
        for version_key in ("silver_delta_version", "gold_delta_version"):
            if not isinstance(binding.get(version_key), int) or binding[version_key] < 0:
                raise ValueError("real versions required")
        if (
            binding.get("gold_source_silver_version") != binding["silver_delta_version"]
            or not binding.get("ta_approval_reference")
        ):
            raise ValueError("unapproved/unbound versions")
    elif binding["mode"] != "course-fallback":
        raise ValueError("unknown handoff mode")

    return silver_rows, gold_rows, binding


def policy_from(team: str, threshold, baseline: dict, binding: dict, code_path, contract: dict) -> dict:
    body = {
        "team_id": team,
        "threshold": format(Decimal(str(threshold)), ".2f"),
        "baseline_sha256": digest(baseline),
        "handoff_sha256": digest(binding),
        "decision_code_sha256": file_sha(code_path),
        "contract_sha256": digest(contract),
    }
    return {"policy_id": digest(body), **body}


def validate_policy(policy: dict, baseline: dict, handoff: dict, code_path, contract: dict) -> None:
    expected = policy_from(
        policy["team_id"],
        policy["threshold"],
        baseline,
        handoff,
        code_path,
        contract,
    )
    if policy != expected:
        raise ValueError("frozen code/policy/data binding mismatch")


def scored(module, rows: list[dict], baseline: dict, threshold, contract: dict) -> list[dict]:
    return [
        {
            "record_id": row["record_id"],
            "site": row["site"],
            **module.score_event(row, baseline, threshold, contract),
        }
        for row in rows
    ]


def decision(module, event: dict, baseline: dict, policy: dict, contract: dict) -> dict:
    return {
        "decision_id": module.make_action_key(policy["team_id"], event, policy["policy_id"]),
        "policy_id": policy["policy_id"],
        "event": event,
        **module.score_event(event, baseline, policy["threshold"], contract),
    }


def request(url: str, token: str, method: str = "GET", body=None) -> tuple[int, dict]:
    encoded_body = None if body is None else canonical(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=encoded_body,
        method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode("utf-8"))
