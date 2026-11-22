"""Local action sandbox engine. Control/outbox and mock receiver use separate SQLite DBs."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from common import canonical, digest


class APIError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


class Engine:
    def __init__(self, state_dir, config: dict):
        self.state = Path(state_dir)
        self.state.mkdir(parents=True, exist_ok=True)
        self.cfg = config

        with self.control() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    proposer TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reviewer TEXT,
                    review_reason TEXT,
                    created_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    ticket_id TEXT
                );
                CREATE TABLE IF NOT EXISTS audit (
                    seq INTEGER PRIMARY KEY,
                    entry TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            conn.execute("INSERT OR IGNORE INTO settings VALUES ('paused', 'false')")
            existing_policy = conn.execute(
                "SELECT value FROM settings WHERE key = 'policy_id'"
            ).fetchone()
            if existing_policy and existing_policy[0] != config["policy"]["policy_id"]:
                raise ValueError("sandbox state belongs to another policy")
            conn.execute(
                "INSERT OR IGNORE INTO settings VALUES ('policy_id', ?)",
                (config["policy"]["policy_id"],),
            )

        with self.receiver() as rconn:
            rconn.execute(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    id TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    ticket_id TEXT NOT NULL,
                    created_utc TEXT NOT NULL
                )
                """
            )

    @contextmanager
    def db(self, filename: str):
        conn = sqlite3.connect(self.state / filename, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def control(self):
        return self.db("control.sqlite")

    def receiver(self):
        return self.db("receiver.sqlite")

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def append(self, conn, actor: str, action: str, object_id: str, details: dict) -> None:
        row = conn.execute("SELECT entry FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        prev = json.loads(row[0]) if row else None
        entry = {
            "seq": prev["seq"] + 1 if prev else 1,
            "time_utc": self.now(),
            "actor": actor,
            "action": action,
            "object_id": object_id,
            "details": details,
            "prev_hash": prev["hash"] if prev else "0" * 64,
        }
        entry["hash"] = digest(entry)
        conn.execute("INSERT INTO audit VALUES (?, ?)", (entry["seq"], canonical(entry)))

    def denied(self, actor: str, reason: str, object_id: str = "") -> None:
        with self.control() as conn:
            self.append(conn, actor, "DENIED", object_id, {"reason": reason})

    def authenticate(self, token: str) -> dict:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        actor = self.cfg["identities"].get(token_hash)
        if not actor:
            self.denied("unknown", "invalid credential")
            raise APIError(401, "invalid credential")
        return actor

    def require(self, actor: dict, role: str) -> None:
        if role not in actor["roles"]:
            self.denied(actor["id"], "role required: " + role)
            raise APIError(403, "role required: " + role)

    def propose(self, actor: dict, proposals: list[dict]) -> dict:
        self.require(actor, "producer")
        if not isinstance(proposals, list) or not (1 <= len(proposals) <= 200):
            raise APIError(400, "1..200 decisions required")

        for item in proposals:
            expected_item = self.cfg["expected"].get(
                item.get("decision_id") if isinstance(item, dict) else None
            )
            if not isinstance(item, dict) or canonical(expected_item) != canonical(item):
                self.denied(actor["id"], "altered or untrusted decision")
                raise APIError(409, "decision differs from locked rule/data")

        inserted = 0
        replayed = 0
        with self.control() as conn:
            for item in proposals:
                decision_id = item["decision_id"]
                payload_hash = digest(item)
                existing = conn.execute(
                    "SELECT payload_hash FROM decisions WHERE id = ?",
                    (decision_id,),
                ).fetchone()
                if existing:
                    if existing[0] != payload_hash:
                        raise APIError(409, "key reused with changed payload")
                    replayed += 1
                    continue

                status = "PENDING" if item["route"] == "ALERT" else "RECORDED"
                conn.execute(
                    "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        decision_id,
                        canonical(item),
                        payload_hash,
                        actor["id"],
                        status,
                        None,
                        None,
                        self.now(),
                    ),
                )
                inserted += 1
                self.append(
                    conn,
                    actor["id"],
                    "PROPOSE" if status == "PENDING" else "RECORD",
                    decision_id,
                    {
                        "payload_sha256": payload_hash,
                        "route": item["route"],
                        "policy_id": item["policy_id"],
                    },
                )
            self.append(
                conn,
                actor["id"],
                "INGEST_REQUEST",
                "",
                {"new": inserted, "replayed": replayed},
            )
        return {"inserted": inserted, "replayed": replayed}

    def review(self, actor: dict, decision_id: str, approve: bool, reason: str) -> dict:
        self.require(actor, "reviewer")
        if not isinstance(approve, bool) or not isinstance(reason, str) or len(reason.strip()) < 8:
            raise APIError(400, "boolean approve and reason >=8 characters required")

        with self.control() as conn:
            row = conn.execute(
                "SELECT * FROM decisions WHERE id = ?",
                (decision_id,),
            ).fetchone()
            if not row:
                raise APIError(404, "unknown decision")
            if row["proposer"] == actor["id"]:
                pass
            elif row["status"] != "PENDING":
                raise APIError(409, "not pending; review is immutable")
            else:
                new_status = "APPROVED" if approve else "REJECTED"
                conn.execute(
                    "UPDATE decisions SET status = ?, reviewer = ?, review_reason = ? WHERE id = ?",
                    (new_status, actor["id"], reason, decision_id),
                )
                if approve:
                    conn.execute(
                        "INSERT INTO outbox VALUES (?, ?, ?, NULL)",
                        (decision_id, row["payload_hash"], "PENDING"),
                    )
                self.append(
                    conn,
                    actor["id"],
                    new_status,
                    decision_id,
                    {"reason": reason, "payload_sha256": row["payload_hash"]},
                )
                return {"status": new_status, "decision_id": decision_id}

        self.denied(actor["id"], "self approval prohibited", decision_id)
        raise APIError(403, "self approval prohibited")

    def pause(self, actor: dict, paused: bool) -> dict:
        self.require(actor, "owner")
        if not isinstance(paused, bool):
            raise APIError(400, "paused must be boolean")

        with self.control() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = 'paused'",
                ("true" if paused else "false",),
            )
            self.append(
                conn,
                actor["id"],
                "PAUSE" if paused else "RESUME",
                "",
                {"policy_id": self.cfg["policy"]["policy_id"]},
            )
        return {"paused": paused}

    def dispatch(self, actor: dict, fail_after_effect: bool = False) -> dict:
        self.require(actor, "dispatcher")
        with self.control() as conn:
            is_paused = (
                conn.execute("SELECT value FROM settings WHERE key = 'paused'").fetchone()[0]
                == "true"
            )
            if is_paused:
                raise APIError(409, "dispatch paused")
            jobs = conn.execute(
                """
                SELECT o.*, d.status AS ds, d.reviewer, d.proposer
                FROM outbox o
                JOIN decisions d ON o.id = d.id
                WHERE o.status = 'PENDING'
                ORDER BY o.id
                """
            ).fetchall()

        delivered = 0
        deduplicated = 0
        for job in jobs:
            if job["ds"] != "APPROVED" or not job["reviewer"] or job["reviewer"] == job["proposer"]:
                raise APIError(409, "approval invariant failed")

            ticket_id = "TICKET-" + job["id"][:16]
            with self.receiver() as rconn:
                existing = rconn.execute(
                    "SELECT * FROM tickets WHERE id = ?",
                    (job["id"],),
                ).fetchone()
                if existing:
                    if existing["payload_hash"] != job["payload_hash"]:
                        raise APIError(409, "receiver payload conflict")
                    deduplicated += 1
                    ticket_id = existing["ticket_id"]
                else:
                    rconn.execute(
                        "INSERT INTO tickets VALUES (?, ?, ?, ?)",
                        (job["id"], job["payload_hash"], ticket_id, self.now()),
                    )

            if fail_after_effect:
                with self.control() as conn:
                    self.append(
                        conn,
                        actor["id"],
                        "FAULT_AFTER_EFFECT",
                        job["id"],
                        {"ticket_id": ticket_id},
                    )
                raise APIError(
                    503,
                    "injected failure after receiver commit, before sender acknowledgement",
                )

            with self.control() as conn:
                conn.execute(
                    "UPDATE outbox SET status = 'DELIVERED', ticket_id = ? WHERE id = ?",
                    (ticket_id, job["id"]),
                )
                conn.execute(
                    "UPDATE decisions SET status = 'DISPATCHED' WHERE id = ?",
                    (job["id"],),
                )
                self.append(
                    conn,
                    actor["id"],
                    "DISPATCHED",
                    job["id"],
                    {
                        "ticket_id": ticket_id,
                        "receiver_deduplicated": bool(existing),
                    },
                )
            delivered += 1

        return {"delivered": delivered, "receiver_deduplicated": deduplicated}

    def snapshot(self, actor: dict) -> dict:
        self.require(actor, "reader")
        with self.control() as conn:
            decisions = []
            for row in conn.execute("SELECT * FROM decisions ORDER BY id"):
                item = dict(row)
                item["payload"] = json.loads(item["payload"])
                decisions.append(item)
            outbox = [dict(row) for row in conn.execute("SELECT * FROM outbox ORDER BY id")]
            audit = [
                json.loads(row[0])
                for row in conn.execute("SELECT entry FROM audit ORDER BY seq")
            ]
            paused = (
                conn.execute("SELECT value FROM settings WHERE key = 'paused'").fetchone()[0]
                == "true"
            )

        with self.receiver() as rconn:
            tickets = [dict(row) for row in rconn.execute("SELECT * FROM tickets ORDER BY id")]

        return {
            "policy_id": self.cfg["policy"]["policy_id"],
            "decisions": decisions,
            "outbox": outbox,
            "tickets": tickets,
            "audit": audit,
            "paused": paused,
        }

    def anchor(self, actor: dict) -> dict:
        self.require(actor, "owner")
        with self.control() as conn:
            row = conn.execute("SELECT entry FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        last_entry = json.loads(row[0]) if row else None
        return {
            "count": last_entry["seq"] if last_entry else 0,
            "last_hash": last_entry["hash"] if last_entry else "0" * 64,
        }
