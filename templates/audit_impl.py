"""Fifth assessed function, kept separate from the frozen decision_impl.py module."""
import hashlib
import hmac
import json

_ZERO = "0" * 64
_FIELDS = {"seq", "time_utc", "actor", "action", "object_id", "details", "prev_hash", "hash"}


def _is_sha256_hex(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def _is_int(value) -> bool:
    # bool is a subclass of int in Python: True == 1 would otherwise pass as seq 1.
    return isinstance(value, int) and not isinstance(value, bool)


def _digest(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    canonical = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_audit(events: list[dict], anchor: dict) -> bool:
    """Verify an append-only SHA-256 hash chain against an external audit anchor.

    Requirements:
        - Check seq == 1..N in order, with initial prev_hash == "0" * 64.
        - Recompute each entry's SHA-256 digest over the canonical JSON of the entry
          WITHOUT its "hash" key (sort_keys=True, separators=(",", ":"),
          ensure_ascii=False, allow_nan=False) and verify it matches entry["hash"].
        - Verify anchor["count"] == len(events) and anchor["last_hash"] == final hash.
        - An empty log ([]) is valid only when anchor["count"] == 0 and
          anchor["last_hash"] == "0" * 64.
        - Return False cleanly on any missing field, type mismatch, or tamper attempt.
    """
    try:
        # --- anchor shape -------------------------------------------------
        if not isinstance(anchor, dict):
            return False
        a_count = anchor.get("count")
        a_last = anchor.get("last_hash")
        if not _is_int(a_count) or not _is_sha256_hex(a_last):
            return False

        # --- events shape -------------------------------------------------
        if not isinstance(events, list):
            return False

        # --- walk the chain ----------------------------------------------
        prev_hash = _ZERO
        for index, entry in enumerate(events, start=1):
            if not isinstance(entry, dict) or set(entry) != _FIELDS:
                return False
            if not _is_int(entry["seq"]) or entry["seq"] != index:
                return False
            if not _is_sha256_hex(entry["prev_hash"]) or entry["prev_hash"] != prev_hash:
                return False
            if not _is_sha256_hex(entry["hash"]):
                return False
            if not hmac.compare_digest(_digest(entry), entry["hash"]):
                return False
            prev_hash = entry["hash"]

        # --- compare with the external anchor ----------------------------
        # For [] prev_hash is still "0"*64, so the empty-log rule needs no special case.
        return a_count == len(events) and hmac.compare_digest(a_last, prev_hash)
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError, OverflowError):
        # e.g. NaN/Inf in details (allow_nan=False), non-serialisable objects,
        # circular structures: treat every one as tampered/malformed input.
        return False
