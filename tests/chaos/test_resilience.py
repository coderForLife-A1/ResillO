import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import create_router, storage_error_response
from backend.config import Settings
from backend.db import Database
from backend.sync_engine import SyncEngine, content_key
from backend.triage_engine import TriageEngine
from backend.triage_worker import TriageWorker


class ClientOutbox:
    """Simulates the offline IndexedDB outbox queue in the ResillO frontend."""

    def __init__(self, client: TestClient, token: str):
        self.client = client
        self.token = token
        self.queue: list[dict[str, Any]] = []
        self.sync_failed: bool = False

    def enqueue(self, key: str, payload: dict[str, Any]) -> None:
        self.queue.append({"key": key, "payload": payload})

    def flush(self) -> list[dict[str, Any]]:
        headers = {"X-Resillo-Token": self.token, "Content-Type": "application/json"}
        still_pending = []
        for i, item in enumerate(list(self.queue)):
            response = self.client.post("/api/notes", json=item["payload"], headers=headers)
            if response.status_code == 200:
                pass  # Successfully synced, removed from outbox
            else:
                self.sync_failed = True
                # Preserve this item and all remaining un-synced items
                still_pending = self.queue[i:]
                break
        self.queue = still_pending
        return self.queue


def test_concurrent_offline_edits_with_clock_skew_and_packet_replay(tmp_path: Path):
    """
    Test two offline clients editing the same note concurrently with device clocks
    skewed by plus and minus 2 hours, replaying batches out of order with dropped packets:
    - Idempotency keys stop duplicates
    - Append-only history keeps both edits in the events table
    - Higher-severity triage statuses are never silently overwritten
    """
    db = Database(tmp_path / "notes_resilience.sqlite3")
    db.initialize()
    sync = SyncEngine(db)

    base_time = datetime(2026, 11, 17, 10, 0, 0, tzinfo=timezone.utc)
    note_id = "evac-zone-4"

    # Base note established in system
    initial_note = {
        "id": note_id,
        "operator_id": "HQ-BASE",
        "raw_text": "Zone 4 baseline report: nominal operations.",
        "timestamp": base_time.isoformat(),
        "revision": 1,
        "triage_status": "pending",
        "priority": "LOW",
        "category": "Status Update",
        "location": "Sector 4",
        "summary": "Nominal operations",
        "actionable_needs": json.dumps(["Monitor situation"]),
        "threat_status": "MONITORED",
        "check_count": 1,
        "last_checked": base_time.isoformat(),
        "human_override": 0,
    }
    sync.merge(initial_note)
    sync.append_event(initial_note, f"init:{note_id}")

    # Client A: offline in disaster area, clock skewed by -2 hours (reads 08:00 UTC)
    # Discovers severe situation: CRITICAL bridge collapse
    time_client_a = (base_time - timedelta(hours=2)).isoformat()
    client_a_edit = {
        "id": note_id,
        "operator_id": "OP-ALPHA",
        "raw_text": "CRITICAL: Central suspension bridge collapsed into river, multiple casualties trapped!",
        "timestamp": time_client_a,
        "revision": 2,
        "triage_status": "complete",
        "priority": "CRITICAL",
        "category": "Infrastructure",
        "location": "Route 9 Bridge",
        "summary": "Central bridge collapsed, multiple casualties",
        "actionable_needs": json.dumps(["Search & rescue divers", "Heavy cranes", "Medevac"]),
        "threat_status": "WORSENED",
        "check_count": 2,
        "last_checked": time_client_a,
        "human_override": 0,
    }
    key_a = "idempotency-client-a-edit-rev2"

    # Client B: offline in peripheral area, clock skewed by +2 hours (reads 12:00 UTC)
    # Routine status update: LOW priority
    time_client_b = (base_time + timedelta(hours=2)).isoformat()
    client_b_edit = {
        "id": note_id,
        "operator_id": "OP-BRAVO",
        "raw_text": "Sector 4 periphery nominal, traffic light.",
        "timestamp": time_client_b,
        "revision": 2,
        "triage_status": "complete",
        "priority": "LOW",
        "category": "Status Update",
        "location": "Sector 4 Periphery",
        "summary": "Periphery nominal",
        "actionable_needs": json.dumps(["Routine check in 4h"]),
        "threat_status": "MONITORED",
        "check_count": 2,
        "last_checked": time_client_b,
        "human_override": 0,
    }
    key_b = "idempotency-client-b-edit-rev2"

    # Chaos network replay:
    # Client B's batch arrives first (with +2h future timestamp)
    # Replay Client B's batch (duplicate packet)
    # Client A's batch arrives second (out-of-order, with -2h older timestamp)
    # Replay Client A's batch (duplicate packet)
    # Another duplicate Client B packet arrives

    # 1. First arrival: Client B
    accepted_b1 = sync.append_event(client_b_edit, key_b)
    assert accepted_b1 is True
    merged_b1 = sync.merge(client_b_edit)
    assert merged_b1["priority"] == "LOW"

    # 2. Replay duplicate of Client B (packet replay / retry)
    accepted_b_dup = sync.append_event(client_b_edit, key_b)
    assert accepted_b_dup is False, "Idempotency key must reject duplicate replay of Client B"

    # 3. Client A arrives out of order with -2h clock skew (timestamp is 4h older than Client B)
    accepted_a1 = sync.append_event(client_a_edit, key_a)
    assert accepted_a1 is True, "New edit from Client A must be accepted into append-only log"
    merged_a1 = sync.merge(client_a_edit)

    # Prove higher-severity triage status is NEVER silently overwritten
    assert merged_a1["priority"] == "CRITICAL", "Higher-severity CRITICAL triage must take precedence"
    assert "bridge collapsed" in merged_a1["raw_text"].lower()

    # 4. Replay duplicate of Client A (packet replay)
    accepted_a_dup = sync.append_event(client_a_edit, key_a)
    assert accepted_a_dup is False, "Idempotency key must reject duplicate replay of Client A"

    # 5. Client B replays again with dropped packets scenario
    accepted_b_dup2 = sync.append_event(client_b_edit, key_b)
    assert accepted_b_dup2 is False, "Idempotency key must stop duplicate replay"

    # Verify database state:
    # A) Append-only history keeps both edits
    event_rows = db.execute("SELECT note_id, revision, idempotency_key, payload FROM events WHERE note_id=? ORDER BY sequence", (note_id,)).fetchall()
    # 1 initial + 1 Client B + 1 Client A = 3 total events, NO duplicates
    assert len(event_rows) == 3
    event_keys = [r["idempotency_key"] for r in event_rows]
    assert key_b in event_keys
    assert key_a in event_keys

    # B) Higher-severity triage status in notes table remains CRITICAL
    final_row = db.decode(db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone())
    assert final_row["priority"] == "CRITICAL"
    assert final_row["threat_status"] == "WORSENED"
    assert "bridge collapsed" in final_row["raw_text"].lower()

    # Test reverse arrival order: Client A arrives first, Client B (+2h clock skew) arrives second
    note_id_2 = "evac-zone-5"
    initial_note_2 = dict(initial_note, id=note_id_2)
    sync.merge(initial_note_2)
    sync.append_event(initial_note_2, f"init:{note_id_2}")

    client_a_edit_2 = dict(client_a_edit, id=note_id_2)
    client_b_edit_2 = dict(client_b_edit, id=note_id_2)
    key_a2 = "idempotency-rev2-order2-a"
    key_b2 = "idempotency-rev2-order2-b"

    # Client A merges first
    assert sync.append_event(client_a_edit_2, key_a2) is True
    sync.merge(client_a_edit_2)

    # Client B arrives second with +2h skewed clock and newer timestamp
    assert sync.append_event(client_b_edit_2, key_b2) is True
    merged_b2 = sync.merge(client_b_edit_2)

    # Must NOT overwrite CRITICAL with LOW
    assert merged_b2["priority"] == "CRITICAL"

    # Verify both edits are retained in append-only events table
    events_2 = db.execute("SELECT idempotency_key FROM events WHERE note_id=? ORDER BY sequence", (note_id_2,)).fetchall()
    assert len(events_2) == 3
    assert {r["idempotency_key"] for r in events_2} == {f"init:{note_id_2}", key_a2, key_b2}

    # Verify final note in DB maintains CRITICAL
    final_note_2 = db.decode(db.execute("SELECT * FROM notes WHERE id=?", (note_id_2,)).fetchone())
    assert final_note_2["priority"] == "CRITICAL"

    db.close()


def test_simulated_disk_full_error_returns_structured_response_and_preserves_outbox(tmp_path: Path):
    """
    Test simulating disk-full database write errors:
    - API returns structured error responses instead of unhandled 500 tracebacks
    - Client outbox preserves un-synced data until recovery
    """
    db = Database(tmp_path / "notes_disk_full.sqlite3")
    db.initialize()
    engine = TriageEngine("http://127.0.0.1:1", "mock", 0.01, 0.01)
    worker = TriageWorker(db, engine)

    settings = Settings()
    settings.database_path = tmp_path / "notes_disk_full.sqlite3"
    token = settings.api_token

    app = FastAPI()
    app.include_router(create_router(db, worker, settings))

    client = TestClient(app, raise_server_exceptions=False)
    outbox = ClientOutbox(client, token)

    # Queue an emergency field note in client outbox
    outbox.enqueue("local-item-1", {
        "id": "note-disk-fail-1",
        "raw_text": "CRITICAL: Power failure at ICU backup generator.",
        "operator_id": "FIELD-OP-10",
    })
    outbox.enqueue("local-item-2", {
        "id": "note-disk-fail-2",
        "raw_text": "Water pipeline ruptured at 5th and Main.",
        "operator_id": "FIELD-OP-10",
    })

    assert len(outbox.queue) == 2

    # Simulate disk full error during database execute
    original_execute = db.execute

    def disk_full_execute(query: str, args: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        if "INSERT" in query.upper() or "UPDATE" in query.upper():
            raise sqlite3.OperationalError("database or disk is full")
        return original_execute(query, args)

    with patch.object(db, "execute", side_effect=disk_full_execute):
        # Direct API test to prove structured error response instead of 500 traceback
        res = client.post(
            "/api/notes",
            json={"id": "test-err-500", "raw_text": "urgent supplies needed", "operator_id": "OP-1"},
            headers={"X-Resillo-Token": token},
        )

        assert res.status_code == 507, f"Expected 507 Insufficient Storage, got {res.status_code}"
        data = res.json()
        assert data["error"] == "storage_failure"
        assert data["code"] == "DISK_FULL"
        assert data["recoverable"] is True
        assert "database or disk is full" in data["detail"]
        assert "Traceback" not in res.text

        # Client outbox flushes while disk is full
        outbox.flush()
        assert outbox.sync_failed is True
        # Outbox must preserve both un-synced notes until recovery
        assert len(outbox.queue) == 2
        assert outbox.queue[0]["key"] == "local-item-1"
        assert outbox.queue[1]["key"] == "local-item-2"

    # Recovery: disk space freed / failure resolved
    assert db.is_writable() is True
    outbox.flush()

    # On recovery, outbox empties and data is persisted
    assert len(outbox.queue) == 0

    # Verify both notes now exist in database
    row1 = db.execute("SELECT * FROM notes WHERE id='note-disk-fail-1'").fetchone()
    row2 = db.execute("SELECT * FROM notes WHERE id='note-disk-fail-2'").fetchone()
    assert row1 is not None
    assert row2 is not None

    db.close()


def test_simulated_readonly_database_write_error_returns_structured_response_and_preserves_outbox(tmp_path: Path):
    """
    Test simulating read-only database write errors:
    - API returns structured 503 error responses instead of unhandled 500 tracebacks
    - Client outbox preserves un-synced data until recovery
    """
    db = Database(tmp_path / "notes_readonly.sqlite3")
    db.initialize()
    engine = TriageEngine("http://127.0.0.1:1", "mock", 0.01, 0.01)
    worker = TriageWorker(db, engine)

    settings = Settings()
    settings.database_path = tmp_path / "notes_readonly.sqlite3"
    token = settings.api_token

    app = FastAPI()
    app.include_router(create_router(db, worker, settings))

    client = TestClient(app, raise_server_exceptions=False)
    outbox = ClientOutbox(client, token)

    outbox.enqueue("local-ro-1", {
        "id": "note-ro-1",
        "raw_text": "Medical supplies inventory updated.",
        "operator_id": "MED-01",
    })

    original_execute = db.execute

    def readonly_execute(query: str, args: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        if "INSERT" in query.upper() or "UPDATE" in query.upper():
            raise sqlite3.OperationalError("attempt to write a readonly database")
        return original_execute(query, args)

    with patch.object(db, "execute", side_effect=readonly_execute):
        # Direct API test: verify structured 503 instead of unhandled 500
        res = client.post(
            "/api/notes",
            json={"id": "test-ro", "raw_text": "blood packs arriving", "operator_id": "MED-01"},
            headers={"X-Resillo-Token": token},
        )
        assert res.status_code == 503
        data = res.json()
        assert data["error"] == "storage_failure"
        assert data["code"] == "DATABASE_READONLY"
        assert data["recoverable"] is True
        assert "attempt to write a readonly database" in data["detail"]
        assert "Traceback" not in res.text

        # Client outbox flushes during read-only error
        outbox.flush()
        assert outbox.sync_failed is True
        assert len(outbox.queue) == 1
        assert outbox.queue[0]["key"] == "local-ro-1"

    # Recovery: database made writable again
    outbox.flush()
    assert len(outbox.queue) == 0

    saved_row = db.execute("SELECT * FROM notes WHERE id='note-ro-1'").fetchone()
    assert saved_row is not None
    assert "Medical supplies inventory" in saved_row["raw_text"]

    db.close()


def test_batch_sync_idempotency_and_storage_error_handling(tmp_path: Path):
    """
    Test /api/sync endpoint with batch notes:
    - Verifies per-note and batch idempotency keys stop duplicates
    - Verifies storage failures return structured errors
    - Verifies out-of-order batches and duplicates
    """
    db = Database(tmp_path / "notes_batch.sqlite3")
    db.initialize()
    engine = TriageEngine("http://127.0.0.1:1", "mock", 0.01, 0.01)
    worker = TriageWorker(db, engine)

    settings = Settings()
    settings.database_path = tmp_path / "notes_batch.sqlite3"
    token = settings.api_token

    app = FastAPI()
    app.include_router(create_router(db, worker, settings))
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-Resillo-Token": token, "Content-Type": "application/json"}

    batch_payload = {
        "notes": [
            {
                "id": "batch-note-1",
                "operator_id": "OP-1",
                "raw_text": "HIGH: Contaminated water supply in Sector 3",
                "timestamp": "2026-11-17T10:00:00Z",
                "revision": 1,
                "triage_status": "complete",
                "priority": "HIGH",
                "category": "Supplies",
                "location": "Sector 3",
                "summary": "Water contamination",
                "actionable_needs": ["Water purification units"],
                "threat_status": "PERSISTING",
                "check_count": 1,
                "last_checked": "2026-11-17T10:00:00Z",
                "human_override": 0,
                "idempotency_key": "unique-note-key-1",
            },
            {
                "id": "batch-note-2",
                "operator_id": "OP-2",
                "raw_text": "Sector 5 roads cleared.",
                "timestamp": "2026-11-17T10:05:00Z",
                "revision": 1,
                "triage_status": "pending",
                "priority": "LOW",
                "category": "Infrastructure",
                "location": "Sector 5",
                "summary": "Roads cleared",
                "actionable_needs": [],
                "threat_status": "MONITORED",
                "check_count": 1,
                "last_checked": "2026-11-17T10:05:00Z",
                "human_override": 0,
                "idempotency_key": "unique-note-key-2",
            },
        ]
    }

    # 1. Normal sync succeeds
    res1 = client.post("/api/sync", json=batch_payload, headers=headers)
    assert res1.status_code == 200
    assert res1.json()["accepted"] == 2

    # 2. Duplicate sync replay: idempotency keys stop duplicates (accepted == 0)
    res2 = client.post("/api/sync", json=batch_payload, headers=headers)
    assert res2.status_code == 200
    assert res2.json()["accepted"] == 0

    # 3. Storage error during sync returns structured response
    original_execute = db.execute

    def full_execute(query: str, args: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        if "INSERT" in query.upper():
            raise sqlite3.OperationalError("database or disk is full")
        return original_execute(query, args)

    with patch.object(db, "execute", side_effect=full_execute):
        res_fail = client.post(
            "/api/sync",
            json={"notes": [{"id": "new-note-3", "operator_id": "OP-3", "raw_text": "test", "timestamp": "now", "revision": 1, "triage_status": "pending", "priority": "LOW", "category": "Status Update", "location": "Unknown", "summary": "test", "actionable_needs": [], "threat_status": "MONITORED", "check_count": 1, "last_checked": "now", "human_override": 0, "idempotency_key": "unique-note-key-3"}]},
            headers=headers,
        )
        assert res_fail.status_code == 507
        data = res_fail.json()
        assert data["error"] == "storage_failure"
        assert data["code"] == "DISK_FULL"
        assert data["recoverable"] is True
        assert "Traceback" not in res_fail.text

    db.close()


def test_protected_diagnostics_endpoint_and_sanitized_errors(tmp_path: Path):
    """
    Test protected /diagnostics endpoint:
    - Rejects unauthorized requests with 401
    - Exports sanitized queue depth, circuit breaker trips, conflict counts, and errors
    - Never leaks sensitive note text or patient/operator details in error exports
    """
    from backend.diagnostics import record_error, setup_logging

    db = Database(tmp_path / "notes_diag.sqlite3")
    db.initialize()
    engine = TriageEngine("http://127.0.0.1:1", "mock", 0.01, 0.01)
    worker = TriageWorker(db, engine)

    settings = Settings()
    settings.database_path = tmp_path / "notes_diag.sqlite3"
    token = settings.api_token

    app = FastAPI()
    app.include_router(create_router(db, worker, settings))
    client = TestClient(app, raise_server_exceptions=False)

    # 1. Unauthorized request must fail
    res_unauth = client.get("/api/diagnostics")
    assert res_unauth.status_code == 401, "Protected diagnostics must reject missing token"

    # 2. Trigger circuit breaker failure and trips
    for _ in range(4):
        engine.breaker.failure()
    assert engine.breaker.trips_count >= 1

    # 3. Simulate a merge conflict to increment conflict stats
    sync = SyncEngine(db)
    base_note = {
        "id": "diag-note-1",
        "operator_id": "MED-1",
        "raw_text": "Base triage note",
        "timestamp": "2026-11-17T10:00:00Z",
        "revision": 1,
        "triage_status": "complete",
        "priority": "HIGH",
        "category": "Medical",
        "location": "Hospital",
        "summary": "Base",
        "actionable_needs": "[]",
        "threat_status": "PERSISTING",
        "check_count": 1,
        "last_checked": "2026-11-17T10:00:00Z",
        "human_override": 0,
    }
    sync.merge(base_note)
    # Concurrent conflicting edit on same note id
    conflict_note = dict(base_note, revision=2, priority="CRITICAL")
    sync.merge(conflict_note)

    # 4. Record an operational error containing sensitive medical and casualty details
    sensitive_raw_text = "Patient John Doe with severe blast trauma and restricted identity ID-89423984729"
    record_error(
        code="STORAGE_ERROR",
        message=f"Error inserting record VALUES('{sensitive_raw_text}') into notes",
        endpoint="/api/notes",
    )

    # 5. Query authorized diagnostics endpoint
    res_diag = client.get("/api/diagnostics", headers={"X-Resillo-Token": token})
    assert res_diag.status_code == 200
    diag = res_diag.json()

    # Validate structure and metrics
    assert "queue_depth" in diag
    assert "circuit_breaker" in diag
    assert diag["circuit_breaker"]["trips"] >= 1
    assert "conflicts" in diag
    assert diag["conflicts"]["detected_in_session"] >= 1
    assert "storage" in diag
    assert diag["storage"]["db_online"] is True
    assert diag["storage"]["is_writable"] is True

    # Validate that sensitive text is sanitized and NOT leaked
    assert "recent_errors" in diag
    assert len(diag["recent_errors"]) >= 1
    error_texts = [e["message"] for e in diag["recent_errors"]]
    combined_errors = " ".join(error_texts)

    assert sensitive_raw_text not in combined_errors, "Sensitive note raw text leaked in error telemetry!"
    assert "John Doe" not in combined_errors
    assert "ID-89423984729" not in combined_errors

    db.close()


def test_rotating_file_logger_caps_storage(tmp_path: Path):
    """
    Test rotating file handler caps log storage size and file count:
    - Bounded log files prevent storage from filling during long-running trials
    """
    from backend.diagnostics import setup_logging

    log_dir = tmp_path / "logs"
    max_bytes = 1024  # 1 KB per file
    backup_count = 3  # Max 3 backups (total max 4 files, ~4KB)

    diag_logger = setup_logging(log_dir, max_bytes=max_bytes, backup_count=backup_count)

    # Write enough logs to trigger multiple rotations (>10KB)
    for i in range(200):
        diag_logger.info(f"Log event #{i:04d}: System heartbeat and operational metrics for long-running dogfood trial.")

    log_files = list(log_dir.glob("resillo.log*"))
    assert len(log_files) <= backup_count + 1, f"Expected at most {backup_count + 1} files, found {len(log_files)}"

    total_size = sum(f.stat().st_size for f in log_files)
    # Total size should not exceed (backup_count + 1) * max_bytes + safety margin
    assert total_size <= (backup_count + 2) * max_bytes * 1.5, f"Log storage grew unbounded: {total_size} bytes"

