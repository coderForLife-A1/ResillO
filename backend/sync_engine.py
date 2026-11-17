from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .db import Database


PRIORITY_RANK: dict[str, int] = {
    "CRITICAL": 4,
    "HIGH": 3,
    "MEDIUM": 2,
    "LOW": 1,
}

THREAT_RANK: dict[str, int] = {
    "WORSENED": 3,
    "PERSISTING": 2,
    "MONITORED": 1,
}

TRIAGE_STATUS_RANK: dict[str, int] = {
    "complete": 2,
    "pending": 1,
}


def content_key(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SyncEngine:
    total_conflicts: int = 0

    def __init__(self, db: Database) -> None:
        self.db = db

    @classmethod
    def get_conflict_count(cls) -> int:
        return cls.total_conflicts

    def append_event(self, note: dict[str, Any], key: str) -> bool:
        payload = dict(note)
        needs = payload.get("actionable_needs", [])
        if isinstance(needs, str):
            try:
                payload["actionable_needs"] = json.loads(needs)
            except Exception:
                payload["actionable_needs"] = [needs]
        elif isinstance(needs, (list, tuple)):
            payload["actionable_needs"] = list(needs)
        else:
            payload["actionable_needs"] = [str(needs)]
        try:
            self.db.execute(
                "INSERT INTO events(event_id,note_id,revision,idempotency_key,payload,created_at) VALUES(?,?,?,?,?,?)",
                (str(uuid4()), note["id"], note.get("revision", 1), key, json.dumps(payload, sort_keys=True), now()),
            )
            return True
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                return False
            raise

    def merge(self, incoming: dict[str, Any]) -> dict[str, Any]:
        needs = incoming.get("actionable_needs", [])
        if not isinstance(needs, str):
            incoming["actionable_needs"] = json.dumps(list(needs))

        existing_row = self.db.execute("SELECT * FROM notes WHERE id=?", (incoming["id"],)).fetchone()
        if existing_row is None:
            self.db.execute(
                "INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(incoming[field] for field in (
                    "id", "operator_id", "raw_text", "timestamp", "revision",
                    "triage_status", "priority", "category", "location", "summary",
                    "actionable_needs", "threat_status", "check_count", "last_checked",
                    "human_override"
                )),
            )
            return incoming

        SyncEngine.total_conflicts += 1
        existing = self.db.decode(existing_row)

        # 1. Human override cannot be silently replaced by a non-human override
        if existing["human_override"] and not incoming.get("human_override"):
            return existing

        # 2. Compare priority severities: higher-severity triage status is NEVER silently overwritten
        existing_p = PRIORITY_RANK.get(str(existing.get("priority", "LOW")).upper(), 1)
        incoming_p = PRIORITY_RANK.get(str(incoming.get("priority", "LOW")).upper(), 1)

        # If existing has strictly higher severity, keep existing's priority and emergency fields
        if existing_p > incoming_p and not incoming.get("human_override"):
            return existing

        # If incoming has strictly higher severity, incoming elevates the triage status!
        if incoming_p > existing_p:
            merged = dict(incoming)
            merged["priority"] = incoming["priority"]
            merged["revision"] = max(existing["revision"], incoming.get("revision", 1))
            if incoming.get("revision", 1) <= existing["revision"]:
                merged["revision"] = existing["revision"] + 1
            if THREAT_RANK.get(existing.get("threat_status"), 0) > THREAT_RANK.get(incoming.get("threat_status"), 0):
                merged["threat_status"] = existing["threat_status"]
            if TRIAGE_STATUS_RANK.get(existing.get("triage_status"), 0) > TRIAGE_STATUS_RANK.get(incoming.get("triage_status"), 0):
                merged["triage_status"] = existing["triage_status"]
            merged["human_override"] = int(bool(existing.get("human_override") or incoming.get("human_override")))
            self.db.execute(
                "UPDATE notes SET operator_id=?,raw_text=?,timestamp=?,revision=?,triage_status=?,priority=?,category=?,location=?,summary=?,actionable_needs=?,threat_status=?,check_count=?,last_checked=?,human_override=? WHERE id=?",
                tuple(merged[field] for field in (
                    "operator_id", "raw_text", "timestamp", "revision",
                    "triage_status", "priority", "category", "location", "summary",
                    "actionable_needs", "threat_status", "check_count", "last_checked",
                    "human_override"
                )) + (incoming["id"],),
            )
            return merged

        # 3. Same priority severity: resolve by revision and higher threat/triage statuses
        if incoming.get("revision", 1) >= existing["revision"]:
            merged = dict(incoming)
            if incoming.get("revision", 1) == existing["revision"]:
                merged["revision"] = existing["revision"] + 1
            if THREAT_RANK.get(existing.get("threat_status"), 0) > THREAT_RANK.get(incoming.get("threat_status"), 0):
                merged["threat_status"] = existing["threat_status"]
            if TRIAGE_STATUS_RANK.get(existing.get("triage_status"), 0) > TRIAGE_STATUS_RANK.get(incoming.get("triage_status"), 0):
                merged["triage_status"] = existing["triage_status"]
            merged["human_override"] = int(bool(existing.get("human_override") or incoming.get("human_override")))
            self.db.execute(
                "UPDATE notes SET operator_id=?,raw_text=?,timestamp=?,revision=?,triage_status=?,priority=?,category=?,location=?,summary=?,actionable_needs=?,threat_status=?,check_count=?,last_checked=?,human_override=? WHERE id=?",
                tuple(merged[field] for field in (
                    "operator_id", "raw_text", "timestamp", "revision",
                    "triage_status", "priority", "category", "location", "summary",
                    "actionable_needs", "threat_status", "check_count", "last_checked",
                    "human_override"
                )) + (incoming["id"],),
            )
            return merged

        return existing
