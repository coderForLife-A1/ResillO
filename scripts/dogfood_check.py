#!/usr/bin/env python3
"""Run the five-minute ResillO field health audit."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from backend.config import Settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a strict ResillO dogfood health audit.")
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/diagnostics")
    parser.add_argument("--token", default=os.getenv("RESILIO_API_TOKEN"))
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--drain-timeout", type=float, default=30.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    return parser.parse_args()


def fail(check: str, detail: str) -> bool:
    print(f"FAIL {check}: {detail}")
    return False


def check_logs(settings: Settings) -> bool:
    files = sorted(settings.log_dir.glob("resillo.log*"))
    cap = settings.log_max_bytes
    max_files = settings.log_backup_count + 1
    if len(files) > max_files:
        return fail("rotating_logs", f"{len(files)} files exceeds cap of {max_files}")
    oversized = [f"{path.name}={path.stat().st_size}" for path in files if path.stat().st_size > cap]
    if oversized:
        return fail("rotating_logs", f"oversized files: {', '.join(oversized)}")
    total = sum(path.stat().st_size for path in files)
    total_cap = cap * max_files
    if total > total_cap:
        return fail("rotating_logs", f"{total} bytes exceeds cap of {total_cap}")
    print(f"PASS rotating_logs: {len(files)} files, {total} bytes (cap {total_cap})")
    return True


def check_database(database_path: Path) -> bool:
    if not database_path.exists():
        return fail("database_integrity", f"missing database {database_path}")
    try:
        with sqlite3.connect(database_path) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            return fail("database_integrity", str(result[0] if result else "no result"))
    except sqlite3.Error as error:
        return fail("database_integrity", str(error))
    print(f"PASS database_integrity: {database_path}")
    return True


def fetch_diagnostics(client: httpx.Client, url: str, token: str) -> dict[str, Any]:
    response = client.get(url, headers={"X-Resillo-Token": token})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("diagnostics response is not an object")
    return payload


def check_queue_drain(client: httpx.Client, args: argparse.Namespace, token: str) -> bool:
    started = time.monotonic()
    while True:
        diagnostics = fetch_diagnostics(client, args.url, token)
        depth = diagnostics.get("queue_depth")
        if not isinstance(depth, int) or depth < 0:
            return fail("queue_drain", f"invalid queue_depth={depth!r}")
        if depth == 0:
            elapsed = time.monotonic() - started
            print(f"PASS queue_drain: depth=0, latency={elapsed:.2f}s")
            return True
        if time.monotonic() - started >= args.drain_timeout:
            return fail("queue_drain", f"depth={depth} after {args.drain_timeout:.1f}s")
        time.sleep(args.poll_interval)


def main() -> int:
    args = parse_args()
    settings = Settings()
    token = args.token or settings.api_token
    checks: list[bool] = []
    try:
        with httpx.Client(timeout=args.timeout) as client:
            diagnostics = fetch_diagnostics(client, args.url, token)
            storage = diagnostics.get("storage", {})
            if diagnostics.get("status") != "healthy":
                checks.append(fail("diagnostics", f"status={diagnostics.get('status')!r}"))
            elif not storage.get("db_online") or not storage.get("is_writable"):
                checks.append(fail("diagnostics", "database is offline or read-only"))
            else:
                print("PASS diagnostics: healthy and protected endpoint authorized")
                checks.append(True)
            database_path = Path(storage.get("database_path", settings.database_path))
            checks.append(check_database(database_path))
            checks.append(check_logs(settings))
            checks.append(check_queue_drain(client, args, token))
    except (httpx.HTTPError, ValueError, OSError) as error:
        checks.append(fail("diagnostics", str(error)))
    result = all(checks) if checks else False
    print("PASS dogfood_check: all checks passed" if result else "FAIL dogfood_check: trial health gate failed")
    return 0 if result else 1


if __name__ == "__main__":
    raise SystemExit(main())
