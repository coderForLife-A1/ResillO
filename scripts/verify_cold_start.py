#!/usr/bin/env python3
"""
scripts/verify_cold_start.py

Single-command bootstrap & validation script for cold-start field deployment.
Validates:
1. Python dependencies
2. SQLite DB initialization, schemas, and indexes (WAL mode + FULL sync)
3. Local authentication token generation and verification
4. Ollama status & deterministic circuit-breaker fallback readiness
5. Mobile and web asset synchronization (index.html -> www/index.html)
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def check_python_dependencies() -> bool:
    print("[1/5] Checking Python dependencies...")
    required_packages = ["fastapi", "uvicorn", "httpx", "pydantic", "pytest", "sqlite3"]
    missing = []
    for pkg in required_packages:
        try:
            importlib.import_module(pkg)
            print(f"  ✓ {pkg} available")
        except ImportError:
            print(f"  ✗ {pkg} MISSING")
            missing.append(pkg)

    if missing:
        print(f"FAIL: Missing required dependencies: {missing}")
        return False
    print("  -> Python dependencies OK.\n")
    return True


def check_database_indexes() -> bool:
    print("[2/5] Validating SQLite schema, WAL mode, and indexes...")
    from backend.db import Database

    with tempfile.TemporaryDirectory() as tmp_dir:
        test_db_path = Path(tmp_dir) / "cold_start_test.sqlite3"
        db = Database(test_db_path)
        db.initialize()

        # Check WAL mode
        cursor = db.execute("PRAGMA journal_mode")
        journal_mode = cursor.fetchone()[0].upper()
        if journal_mode != "WAL":
            print(f"  ✗ Expected journal_mode=WAL, got {journal_mode}")
            return False
        print("  ✓ PRAGMA journal_mode = WAL")

        # Check synchronous setting
        cursor = db.execute("PRAGMA synchronous")
        sync_mode = cursor.fetchone()[0]
        # SQLite returns 2 for FULL
        if sync_mode not in (2, "FULL"):
            print(f"  ✗ Expected synchronous=FULL (2), got {sync_mode}")
            return False
        print("  ✓ PRAGMA synchronous = FULL (2)")

        # Check tables
        tables = [
            row["name"]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        for tbl in ["notes", "events"]:
            if tbl not in tables:
                print(f"  ✗ Missing table: {tbl}")
                return False
            print(f"  ✓ Table '{tbl}' verified")

        # Check indexes
        cursor = db.execute("SELECT name, tbl_name FROM sqlite_master WHERE type='index'")
        indexes = {row["name"]: row["tbl_name"] for row in cursor.fetchall()}

        expected_indexes = {
            "notes_timestamp": "notes",
            "notes_triage": "notes",
            "events_note": "events",
        }
        for idx, tbl in expected_indexes.items():
            if idx not in indexes or indexes[idx] != tbl:
                print(f"  ✗ Missing or misconfigured index: {idx} on {tbl}")
                return False
            print(f"  ✓ Index '{idx}' on table '{tbl}' verified")

        # Verify write capability
        if not db.is_writable():
            print("  ✗ Database is not writable")
            return False
        print("  ✓ Database write capability confirmed")

        db.close()

    print("  -> Database schema and indexes OK.\n")
    return True


def check_auth_token_setup() -> bool:
    print("[3/5] Validating local authentication token setup...")
    from backend.config import Settings

    with tempfile.TemporaryDirectory() as tmp_dir:
        test_db = Path(tmp_dir) / "auth_test.sqlite3"
        orig_db = os.environ.get("RESILIO_DB_PATH")
        os.environ["RESILIO_DB_PATH"] = str(test_db)
        try:
            settings = Settings()
            token = settings.api_token
            token_path = test_db.with_suffix(".token")

            if not token:
                print("  ✗ Token is empty")
                return False
            if len(token) < 16:
                print(f"  ✗ Token is too short: {len(token)} chars")
                return False
            print(f"  ✓ Token generated (length={len(token)})")

            if not token_path.exists():
                print(f"  ✗ Token file was not persisted at {token_path}")
                return False
            print(f"  ✓ Token file persisted at {token_path.name}")

            digest = settings.token_digest()
            expected_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            if digest != expected_digest or len(digest) != 64:
                print("  ✗ Token digest calculation mismatch")
                return False
            print(f"  ✓ SHA-256 token digest valid: {digest[:12]}...")

        finally:
            if orig_db is not None:
                os.environ["RESILIO_DB_PATH"] = orig_db
            else:
                os.environ.pop("RESILIO_DB_PATH", None)

    print("  -> Authentication token setup OK.\n")
    return True


def check_ollama_and_fallback_readiness() -> bool:
    print("[4/5] Checking Ollama connectivity & deterministic fallback readiness...")
    import httpx
    from backend.config import settings
    from backend.triage_engine import TriageEngine, fallback_parse

    # 1. Probe Ollama daemon
    ollama_online = False
    try:
        res = httpx.get(f"{settings.ollama_url}/api/tags", timeout=1.0)
        if res.status_code == 200:
            ollama_online = True
            models = [m.get("name") for m in res.json().get("models", [])]
            print(f"  ✓ Ollama daemon ONLINE at {settings.ollama_url} (Models: {models})")
    except Exception:
        print(f"  ℹ Ollama daemon is OFFLINE at {settings.ollama_url} (Expected in field air-gap mode)")

    # 2. Test deterministic fallback triage
    sample_text = "CRITICAL: Flood barrier collapsed in Sector 4. Multiple injuries, immediate rescue helicopters needed."
    t0 = time.perf_counter()
    parsed = fallback_parse(sample_text)
    latency_ms = (time.perf_counter() - t0) * 1000

    if parsed.get("priority") != "CRITICAL":
        print(f"  ✗ Fallback triage failed priority check: {parsed}")
        return False
    if parsed.get("category") not in ("Infrastructure", "Medical"):
        print(f"  ✗ Fallback triage failed category check: {parsed}")
        return False
    print(f"  ✓ Deterministic fallback triage verified in {latency_ms:.2f}ms (Priority: {parsed['priority']}, Category: {parsed['category']})")

    # 3. Test CircuitBreaker transition
    engine = TriageEngine("http://127.0.0.1:1", "mock", 0.01, 0.01)
    breaker = engine.breaker
    if breaker.state != "closed":
        print(f"  ✗ Initial circuit breaker state should be closed, got {breaker.state}")
        return False
    for _ in range(3):
        breaker.failure()
    if breaker.state != "open":
        print(f"  ✗ Circuit breaker failed to open after 3 failures: {breaker.state}")
        return False
    print("  ✓ Circuit breaker 3-strike trip verified (state: open, fallback active)")

    print("  -> Ollama and triage fallback readiness OK.\n")
    return True


def check_mobile_web_asset_sync() -> bool:
    print("[5/5] Checking mobile and web asset synchronization...")
    source = ROOT_DIR / "index.html"
    target = ROOT_DIR / "www" / "index.html"

    if not source.exists():
        print(f"  ✗ Missing source dashboard file: {source}")
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = source.read_bytes()

    if not target.exists() or target.read_bytes() != source_bytes:
        print(f"  ℹ Synchronizing {target.relative_to(ROOT_DIR)} with source...")
        target.write_bytes(source_bytes)

    if target.read_bytes() == source_bytes:
        print(f"  ✓ Asset sync confirmed: www/index.html ({len(source_bytes)} bytes) matches root index.html")
    else:
        print("  ✗ Asset sync failed")
        return False

    print("  -> Mobile/web assets OK.\n")
    return True


def main() -> int:
    print("=" * 60)
    print("🛡️  ResillO Cold-Start Deployment Bootstrap & Verification")
    print("=" * 60 + "\n")

    checks = [
        ("Python Dependencies", check_python_dependencies),
        ("Database & Indexes", check_database_indexes),
        ("Authentication Token", check_auth_token_setup),
        ("Ollama & Fallback Triage", check_ollama_and_fallback_readiness),
        ("Mobile/Web Asset Sync", check_mobile_web_asset_sync),
    ]

    for name, fn in checks:
        if not fn():
            print(f"\n❌ COLD-START VERIFICATION FAILED at stage: '{name}'")
            return 1

    print("=" * 60)
    print("✅ ALL COLD-START PRE-FLIGHT VERIFICATIONS PASSED!")
    print("ResillO is ready for air-gapped field deployment & 7-day dogfood trial.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
