from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .db import Database


def content_key(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SyncEngine:
    def __init__(self, db: Database) -> None:
        self.db = db

    def append_event(self, note: dict[str, Any], key: str) -> bool:
        payload = dict(note)
        if isinstance(payload["actionable_needs"], str):
            payload["actionable_needs"] = json.loads(payload["actionable_needs"])
        else:
            payload["actionable_needs"] = list(payload["actionable_needs"])
        try:
            self.db.execute(
                "INSERT INTO events(event_id,note_id,revision,idempotency_key,payload,created_at) VALUES(?,?,?,?,?,?)",
                (str(uuid4()), note["id"], note["revision"], key, json.dumps(payload, sort_keys=True), now()),
            )
            return True
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                return False
            raise

    def merge(self, incoming: dict[str, Any]) -> dict[str, Any]:
        existing_row = self.db.execute("SELECT * FROM notes WHERE id=?", (incoming["id"],)).fetchone()
        if existing_row is None:
            self.db.execute(            "INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(incoming[field] for field in ("id", "operator_id", "raw_text", "timestamp", "revision", "triage_status", "priority", "category", "location", "summary", "actionable_needs", "threat_status", "check_count", "last_checked", "human_override")) )
            return incoming
        existing = self.db.decode(existing_row)
        if existing["human_override"] or (existing["priority"] == "CRITICAL" and incoming["priority"] != "CRITICAL"):
            return existing
        if incoming["revision"] >= existing["revision"]:
            self.db.execute("UPDATE notes SET operator_id=?,raw_text=?,timestamp=?,revision=?,triage_status=?,priority=?,category=?,location=?,summary=?,actionable_needs=?,threat_status=?,check_count=?,last_checked=?,human_override=? WHERE id=?", tuple(incoming[field] for field in ("operator_id", "raw_text", "timestamp", "revision", "triage_status", "priority", "category", "location", "summary", "actionable_needs", "threat_status", "check_count", "last_checked", "human_override")) + (incoming["id"],))
            return incoming
        return existing
