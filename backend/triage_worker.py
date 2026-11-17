from __future__ import annotations

import asyncio
import json

from .db import Database
from .sync_engine import SyncEngine, content_key, now
from .triage_engine import TriageEngine


class TriageWorker:
    def __init__(self, db: Database, engine: TriageEngine) -> None:
        self.db = db
        self.engine = engine
        self._queue: asyncio.Queue[str] | None = None
        self.task: asyncio.Task[None] | None = None

    @property
    def queue(self) -> asyncio.Queue[str]:
        if self._queue is None:
            self._queue = asyncio.Queue()
        return self._queue

    async def start(self) -> None:
        self.task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def enqueue(self, note_id: str) -> None:
        await self.queue.put(note_id)

    async def run(self) -> None:
        while True:
            note_id = await self.queue.get()
            row = self.db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if row:
                note = self.db.decode(row)
                result = await self.engine.classify(note["raw_text"])
                note.update(result, triage_status="complete", revision=note["revision"] + 1, last_checked=now())
                note["actionable_needs"] = json.dumps(note["actionable_needs"])
                self.db.execute("UPDATE notes SET revision=?,triage_status=?,priority=?,category=?,location=?,summary=?,actionable_needs=?,last_checked=? WHERE id=?", (note["revision"], note["triage_status"], note["priority"], note["category"], note["location"], note["summary"], note["actionable_needs"], note["last_checked"], note_id))
                SyncEngine(self.db).append_event(note, content_key(note))
