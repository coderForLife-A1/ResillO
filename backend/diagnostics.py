from __future__ import annotations

import logging
import re
import time
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .db import Database
from .triage_worker import TriageWorker

START_TIME = time.time()

# Ring buffer for recent sanitized errors (capped at 50 to prevent unbounded memory growth)
_ERROR_BUFFER: deque[dict[str, Any]] = deque(maxlen=50)

# Logger instance for ResillO
logger = logging.getLogger("resillo")


def setup_logging(log_dir: Path, max_bytes: int = 1_048_576, backup_count: int = 5) -> logging.Logger:
    """
    Configures rotating file logging with bounded disk consumption.
    Default: 1MB per file, 5 backup files (max ~5MB storage footprint).
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "resillo.log"

    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Avoid duplicate handlers if setup_logging is called multiple times
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    rotating_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    rotating_handler.setFormatter(formatter)
    logger.addHandler(rotating_handler)

    # Optional console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    logger.info("ResillO logging initialized with rotating handler (max_bytes=%d, backup_count=%d)", max_bytes, backup_count)
    return logger


def sanitize_message(raw: str) -> str:
    """
    Strips raw user input, note content, or SQL parameters from error strings,
    preserving only operational exception types and technical details.
    """
    clean = re.sub(r"VALUES\s*\(.*?\)", "VALUES (...)", raw, flags=re.IGNORECASE)
    clean = re.sub(r"['\"][a-zA-Z0-9_\-\s]{15,}['\"]", "'<sanitized>'", clean)
    clean = re.sub(r"([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})", "<uuid>", clean)
    return clean[:200]


def record_error(code: str, message: str, endpoint: str | None = None) -> None:
    """Records a sanitized operational error to the ring buffer and rotating log."""
    sanitized_msg = sanitize_message(message)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "code": code,
        "message": sanitized_msg,
        "endpoint": endpoint or "internal",
    }
    _ERROR_BUFFER.append(entry)
    logger.error("Operational error [%s] at %s: %s", code, entry["endpoint"], sanitized_msg)


def get_diagnostics(db: Database, worker: TriageWorker, conflict_counter: int = 0) -> dict[str, Any]:
    """Generates sanitized diagnostics telemetry for the protected endpoint."""
    uptime = round(time.time() - START_TIME, 2)
    breaker = worker.engine.breaker

    # Database telemetry
    db_online = db.health()
    db_writable = db.is_writable()
    total_notes = 0
    total_events = 0
    try:
        notes_row = db.execute("SELECT COUNT(*) AS count FROM notes").fetchone()
        events_row = db.execute("SELECT COUNT(*) AS count FROM events").fetchone()
        if notes_row:
            total_notes = notes_row["count"]
        if events_row:
            total_events = events_row["count"]
    except Exception:
        pass

    return {
        "status": "healthy" if db_online and db_writable else "degraded",
        "uptime_seconds": uptime,
        "queue_depth": worker.queue.qsize(),
        "circuit_breaker": breaker.info,
        "conflicts": {
            "detected_in_session": conflict_counter,
            "total_notes": total_notes,
            "total_events": total_events,
            "divergent_events": max(0, total_events - total_notes),
        },
        "storage": {
            "db_online": db_online,
            "is_writable": db_writable,
            "database_path": str(db.path),
            "database_size_bytes": db.path.stat().st_size if db.path.exists() else 0,
        },
        "recent_errors": list(_ERROR_BUFFER),
    }
