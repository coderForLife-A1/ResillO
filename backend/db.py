from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection: sqlite3.Connection | None = None
        self.lock = threading.RLock()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self.connection.row_factory = sqlite3.Row
        with self.connection:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY, operator_id TEXT NOT NULL, raw_text TEXT NOT NULL,
                    timestamp TEXT NOT NULL, revision INTEGER NOT NULL, triage_status TEXT NOT NULL,
                    priority TEXT NOT NULL, category TEXT NOT NULL, location TEXT NOT NULL,
                    summary TEXT NOT NULL, actionable_needs TEXT NOT NULL, threat_status TEXT NOT NULL,
                    check_count INTEGER NOT NULL, last_checked TEXT NOT NULL, human_override INTEGER NOT NULL DEFAULT 0
                )"""
            )
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                    note_id TEXT NOT NULL, revision INTEGER NOT NULL, idempotency_key TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                )"""
            )
            self.connection.execute("CREATE INDEX IF NOT EXISTS notes_timestamp ON notes(timestamp DESC)")
            self.connection.execute("CREATE INDEX IF NOT EXISTS notes_triage ON notes(triage_status)")
            self.connection.execute("CREATE INDEX IF NOT EXISTS events_note ON events(note_id, revision)")

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def execute(self, query: str, args: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        if self.connection is None:
            raise RuntimeError("database is not initialized")
        with self.lock:
            cursor = self.connection.execute(query, args)
            self.connection.commit()
            return cursor

    def health(self) -> bool:
        try:
            self.execute("SELECT 1")
            return True
        except sqlite3.Error:
            return False

    @staticmethod
    def decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["actionable_needs"] = json.loads(result["actionable_needs"])
        result["human_override"] = bool(result["human_override"])
        return result
