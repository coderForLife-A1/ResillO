from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .db import Database
from .sync_engine import SyncEngine, content_key
from .triage_worker import TriageWorker


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_text: str = Field(min_length=1, max_length=50000)
    operator_id: str = Field(min_length=1, max_length=128)
    id: str | None = Field(default=None, max_length=128)


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: list[dict] = Field(default_factory=list, max_length=100)


def create_router(db: Database, worker: TriageWorker, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api")

    async def authorized(authorization: str | None = Header(default=None), x_resillo_token: str | None = Header(default=None)) -> None:
        token = x_resillo_token or (authorization[7:] if authorization and authorization.startswith("Bearer ") else None)
        if token != settings.api_token:
            raise HTTPException(status_code=401, detail="invalid local API token")

    @router.get("/auth/token")
    async def token() -> dict[str, str]:
        return {"token": settings.api_token}

    @router.get("/health")
    async def health() -> dict[str, object]:
        return {"db_online": db.health(), "queue_depth": worker.queue.qsize(), "ollama_circuit": worker.engine.breaker.state, "ollama_online": worker.engine.breaker.state == "closed"}

    @router.post("/notes", dependencies=[Depends(authorized)])
    async def create_note(req: NoteRequest, request: Request) -> dict:
        if request.headers.get("content-length") and int(request.headers["content-length"]) > settings.max_payload:
            raise HTTPException(status_code=413, detail="payload too large")
        timestamp = datetime.now(timezone.utc).isoformat()
        note = {"id": req.id or str(uuid4()), "operator_id": req.operator_id, "raw_text": req.raw_text, "timestamp": timestamp, "revision": 1, "triage_status": "pending", "priority": "LOW", "category": "Status Update", "location": "Unknown", "summary": req.raw_text[:100], "actionable_needs": json.dumps(["Pending triage"]), "threat_status": "MONITORED", "check_count": 1, "last_checked": timestamp, "human_override": 0}
        db.execute("INSERT OR IGNORE INTO notes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(note.values()))
        SyncEngine(db).append_event(note, content_key(note))
        await worker.enqueue(note["id"])
        return db.decode(db.execute("SELECT * FROM notes WHERE id=?", (note["id"],)).fetchone())

    @router.get("/notes", dependencies=[Depends(authorized)])
    async def notes() -> list[dict]:
        return [db.decode(row) for row in db.execute("SELECT * FROM notes ORDER BY timestamp DESC").fetchall()]

    @router.post("/sync", dependencies=[Depends(authorized)])
    async def sync(payload: SyncRequest, x_idempotency_key: str | None = Header(default=None)) -> dict[str, object]:
        key = x_idempotency_key or content_key(payload.model_dump())
        accepted = 0
        sync_engine = SyncEngine(db)
        for incoming in payload.notes:
            incoming["actionable_needs"] = json.dumps(incoming.get("actionable_needs", []))
            if sync_engine.append_event(incoming, key):
                sync_engine.merge(incoming)
                accepted += 1
        return {"accepted": accepted, "idempotency_key": key}

    @router.post("/notes/{note_id}/refresh", dependencies=[Depends(authorized)])
    async def refresh(note_id: str) -> dict:
        row = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="note not found")
        note = db.decode(row)
        note["check_count"] += 1
        note["last_checked"] = datetime.now(timezone.utc).isoformat()
        if note["priority"] == "CRITICAL" and note["check_count"] >= 3:
            note["threat_status"] = "WORSENED"
        elif note["priority"] == "HIGH" and note["check_count"] >= 5:
            note["threat_status"] = "WORSENED"
        db.execute("UPDATE notes SET check_count=?,last_checked=?,threat_status=?,revision=revision+1 WHERE id=?", (note["check_count"], note["last_checked"], note["threat_status"], note_id))
        return db.decode(db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone())

    @router.post("/notes/refresh-all", dependencies=[Depends(authorized)])
    async def refresh_all() -> list[dict]:
        for row in db.execute("SELECT id FROM notes").fetchall():
            await refresh(row["id"])
        return await notes()

    return router
