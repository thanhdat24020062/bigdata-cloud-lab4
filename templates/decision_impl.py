"""Four assessed decision functions. Complete all four BEFORE freezing your policy.

Use Decimal with ROUND_HALF_UP. Do not read label files inside fit_baseline or score_event.
See schemas/decision_contract.json and Lab4_Decision_Support_Data_Driven_Action.docx
for exact formulas and edge-case specifications.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


# ---------------------------------------------------------------------------
# DÁN 3 HÀM PHỤ + fit_baseline THAY CHO fit_baseline CŨ (raise NotImplementedError)
# ---------------------------------------------------------------------------
def _parse_utc(value, field: str) -> datetime:
    """Parse 'YYYY-MM-DDTHH:MM:SSZ' -> datetime; sai định dạng thì ValueError."""
    try:
        return datetime.strptime(value, _TS_FORMAT)
    except (TypeError, ValueError):
        raise ValueError(f"invalid {field}: {value!r}")


def _median(sorted_values: list) -> Decimal:
    """Trung vị của list Decimal đã sắp xếp. Số phần tử chẵn -> trung điểm chính xác."""
    n = len(sorted_values)
    mid = n // 2
    if n % 2 == 1:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / Decimal(2)


def _fmt(value: Decimal, places: int) -> str:
    """Làm tròn ROUND_HALF_UP rồi in thành chuỗi đúng số chữ số thập phân."""
    quantum = Decimal(1).scaleb(-places)  # places=2 -> 0.01 ; places=6 -> 0.000001
    return str(value.quantize(quantum, rounding=ROUND_HALF_UP))


def fit_baseline(rows: list[dict], contract: dict) -> dict[str, dict]:
    """Fit per-sensor historical location and scale parameters on past Silver data.

    Returns:
        dict mapping sensor_id -> {
            "n": int,
            "median_c": str (2 decimal places),
            "mad_c": str (2 decimal places),
            "scale_c": str (6 decimal places),
        }
    """
    # Bước 0: hằng số lấy từ contract (không gõ cứng)
    cutoff = _parse_utc(contract["fit_cutoff_utc"], "fit_cutoff_utc")
    mad_scale = Decimal(str(contract["mad_scale"]))
    scale_floor = Decimal(str(contract["scale_floor_c"]))
    t_min = Decimal(str(contract["min_temperature_c"]))
    t_max = Decimal(str(contract["max_temperature_c"]))

    seen_ids = set()
    by_sensor: dict[str, list] = {}

    # Bước 1 + 2: kiểm tra từng dòng, rồi gom theo sensor
    for row in rows:
        record_id = row.get("record_id")
        sensor_id = row.get("sensor_id")
        if record_id is None or sensor_id is None:
            raise ValueError(f"missing record_id or sensor_id in row: {row!r}")

        if record_id in seen_ids:
            raise ValueError(f"duplicate record_id: {record_id}")
        seen_ids.add(record_id)

        # Cả HAI mốc thời gian phải nhỏ hơn cutoff (bitemporal)
        event_time = _parse_utc(row.get("event_time_utc"), "event_time_utc")
        ingest_time = _parse_utc(row.get("ingest_time_utc"), "ingest_time_utc")
        if event_time >= cutoff or ingest_time >= cutoff:
            raise ValueError(f"timestamp at/after cutoff for record_id {record_id}")

        # Nhiệt độ: parse từ chuỗi gốc bằng Decimal, kiểm tra hữu hạn trước rồi mới so khoảng
        try:
            temperature = Decimal(str(row.get("temperature_c")))
        except InvalidOperation:
            raise ValueError(f"unparseable temperature_c for record_id {record_id}")
        if not temperature.is_finite():
            raise ValueError(f"non-finite temperature_c for record_id {record_id}")
        if temperature < t_min or temperature > t_max:
            raise ValueError(f"temperature_c out of range for record_id {record_id}")

        by_sensor.setdefault(sensor_id, []).append(temperature)

    # Bước 3-6: thống kê từng sensor (tính chính xác, chỉ làm tròn khi xuất chuỗi)
    result: dict[str, dict] = {}
    with localcontext() as ctx:
        ctx.prec = 50
        for sensor_id in sorted(by_sensor):
            values = sorted(by_sensor[sensor_id])
            median = _median(values)
            mad = _median(sorted(abs(v - median) for v in values))
            scale = max(mad_scale * mad, scale_floor)
            result[sensor_id] = {
                "n": len(values),
                "median_c": _fmt(median, 2),
                "mad_c": _fmt(mad, 2),
                "scale_c": _fmt(scale, 6),
            }
    return result

def score_event(event: dict, baseline: dict, threshold: str, contract: dict) -> dict:
    """Score a single observation and route it to HOLD, ALERT, or NORMAL.

    Returns:
        {"score": str (6 decimal places) | None, "route": str, "reason": str}

    Priority order:
        1. Missing sensor in baseline or n < 30 -> {"score": None, "route": "HOLD", "reason": "INSUFFICIENT_BASELINE"}
        2. Unparseable, non-finite, or out-of-range temperature_c -> {"score": None, "route": "HOLD", "reason": "INVALID_VALUE"}
        3. Compute score = round_half_up(|temperature_c - median_c| / scale_c, 6)
        4. Arrival lag (ingest_time_utc - event_time_utc) > 900s -> {"score": score_str, "route": "HOLD", "reason": "LATE_FOR_ACTION"}
        5. Fresh event with Decimal(score_str) >= Decimal(str(threshold)) -> {"score": score_str, "route": "ALERT", "reason": "THRESHOLD"}
        6. Otherwise -> {"score": score_str, "route": "NORMAL", "reason": "BELOW_THRESHOLD"}
    """
    raise NotImplementedError("T2: implement score_event")


def evaluate(predictions: list[dict], labels: dict[str, int], contract: dict) -> dict:
    """Compute confusion counts, coverage, eligible vs. end-to-end recall, and proxy cost.

    Returns a dict with 17 keys:
        n, eligible, hold, tp, fp, tn, fn, alerts, positive_total, held_positive,
        precision, recall, f1, coverage, end_to_end_recall, cost_units, cost.

    Requirements:
        - Raise ValueError if prediction record_ids contain duplicates, do not match
          labels keys 1-to-1, or if any label is not in {0, 1}.
        - Compute tp, fp, tn, fn over eligible rows (ALERT and NORMAL) only.
        - Return None for any ratio whose denominator is 0.
        - Include held_positive in positive_total for end_to_end_recall = tp / positive_total.
        - cost_units = 50*fn + 10*fp + 2*alerts + 10*hold; cost = cost_units / 10.
    """
    raise NotImplementedError("T2: implement evaluate")


def make_action_key(team_id: str, event: dict, policy_id: str) -> str:
    """Build a deterministic SHA-256 semantic idempotency key for an event and policy.

    Hash UTF-8 json.dumps of:
        [team_id, event["source_object"], int(event["source_row"]),
         event["source_sha256"], event["record_id"], policy_id]
    with ensure_ascii=False and separators=(",", ":").
    Never include batch_id, checkpoint path, arrival order, or wall-clock timestamps.
    """
    raise NotImplementedError("T3: implement make_action_key")
