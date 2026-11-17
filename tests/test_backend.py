import asyncio
from pathlib import Path

import pytest

from backend.db import Database
from backend.triage_engine import TriageEngine
from backend.triage_worker import TriageWorker
from backend.sync_engine import SyncEngine


def test_ingestion_persists_when_ollama_is_offline(tmp_path: Path):
    asyncio.run(_test_ingestion_persists_when_ollama_is_offline(tmp_path))


def test_worker_requeues_pending_notes_after_restart(tmp_path: Path):
    db = Database(tmp_path / "restart.sqlite3")
    db.initialize()
    note = {
        "id": "restart-pending",
        "operator_id": "test",
        "raw_text": "pending after restart",
        "timestamp": "now",
        "revision": 1,
        "triage_status": "pending",
        "priority": "LOW",
        "category": "Status Update",
        "location": "Unknown",
        "summary": "pending",
        "actionable_needs": "[]",
        "threat_status": "MONITORED",
        "check_count": 1,
        "last_checked": "now",
        "human_override": 0,
    }
    db.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(note.values()))
    worker = TriageWorker(db, TriageEngine("http://127.0.0.1:1", "mock", 0.01, 0.01))

    async def start_and_stop() -> int:
        await worker.start()
        depth = worker.queue.qsize()
        await worker.stop()
        return depth

    assert asyncio.run(start_and_stop()) == 1
    db.close()


async def _test_ingestion_persists_when_ollama_is_offline(tmp_path: Path):
    db = Database(tmp_path / "notes.sqlite3")
    db.initialize()
    worker = TriageWorker(db, TriageEngine("http://127.0.0.1:1", "missing", 0.01, 0.01))
    await worker.start()
    note = {"id": "offline", "operator_id": "test", "raw_text": "critical bridge collapse", "timestamp": "2026-01-01T00:00:00+00:00", "revision": 1, "triage_status": "pending", "priority": "LOW", "category": "Status Update", "location": "Unknown", "summary": "critical bridge collapse", "actionable_needs": "[]", "threat_status": "MONITORED", "check_count": 1, "last_checked": "2026-01-01T00:00:00+00:00", "human_override": 0}
    db.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(note.values()))
    await worker.enqueue("offline")
    for _ in range(30):
        row = db.execute("SELECT triage_status, priority FROM notes WHERE id='offline'").fetchone()
        if row and row["triage_status"] == "complete":
            break
        await asyncio.sleep(0.1)
    assert row is not None
    assert row["triage_status"] == "complete"
    assert row["priority"] == "CRITICAL"
    await worker.stop()
    db.close()


def test_circuit_breaker_falls_back():
    engine = TriageEngine("http://127.0.0.1:1", "missing", 0.01, 0.01)
    assert asyncio.run(engine.classify("urgent water shortage"))["priority"] == "HIGH"
    for _ in range(2):
        asyncio.run(engine.classify("routine update"))
    assert engine.breaker.state == "open"


def test_duplicate_event_idempotency(tmp_path: Path):
    db = Database(tmp_path / "notes.sqlite3")
    db.initialize()
    note = {"id": "same", "operator_id": "test", "raw_text": "x", "timestamp": "now", "revision": 1, "triage_status": "pending", "priority": "LOW", "category": "Status Update", "location": "Unknown", "summary": "x", "actionable_needs": [], "threat_status": "MONITORED", "check_count": 1, "last_checked": "now", "human_override": 0}
    engine = SyncEngine(db)
    assert engine.append_event(note, "same-key")
    assert not engine.append_event(note, "same-key")
    assert db.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"] == 1
    db.close()


def test_human_override_wins_conflict(tmp_path: Path):
    db = Database(tmp_path / "notes.sqlite3")
    db.initialize()
    engine = SyncEngine(db)
    base = {"id": "conflict", "operator_id": "test", "raw_text": "x", "timestamp": "now", "revision": 2, "triage_status": "complete", "priority": "CRITICAL", "category": "Medical", "location": "Unknown", "summary": "x", "actionable_needs": "[]", "threat_status": "PERSISTING", "check_count": 1, "last_checked": "now", "human_override": 1}
    db.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(base.values()))
    incoming = dict(base, priority="LOW", human_override=0, revision=3)
    assert engine.merge(incoming)["priority"] == "CRITICAL"
    db.close()
