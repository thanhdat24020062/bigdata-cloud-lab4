"""Fifth assessed function, kept separate from the frozen decision_impl.py module."""


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
    raise NotImplementedError("T5: implement verify_audit")
