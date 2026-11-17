# 🛡️ ResillO

<p align="center">
  <strong>Mission-Critical, Offline-First Emergency Field Notes Triage Platform</strong>
</p>

<p align="center">
  <a href="#key-features"><img src="https://img.shields.io/badge/Status-Field%20Ready-success?style=flat-square" alt="Status"></a>
  <a href="#tech-stack"><img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI"></a>
  <a href="#tech-stack"><img src="https://img.shields.io/badge/SQLite-WAL%20Mode-003B57?style=flat-square&logo=sqlite&logoColor=white" alt="SQLite"></a>
  <a href="#tech-stack"><img src="https://img.shields.io/badge/Ollama-Gemma%202B-black?style=flat-square&logo=ollama&logoColor=white" alt="Ollama"></a>
  <a href="#tech-stack"><img src="https://img.shields.io/badge/TailwindCSS-Dark%20Mode-38B2AC?style=flat-square&logo=tailwind-css&logoColor=white" alt="Tailwind"></a>
  <a href="#tech-stack"><img src="https://img.shields.io/badge/Platforms-Desktop%20%7C%20Android%20%7C%20Web-blue?style=flat-square" alt="Platforms"></a>
</p>

---

## 📌 Overview

**ResillO** is designed for first responders, humanitarian aid teams, and field operators navigating connectivity-dead zones, post-disaster areas, and hostile environments. 

It transforms unstructured, chaotic field notes into structured, actionable triage intelligence (Priority, Category, Location, Summary, and Immediate Needs) using **on-device, local open-weight AI (Gemma 2B via Ollama)**—ensuring **zero data ever leaves the local perimeter**.

> [!IMPORTANT]
> **Zero Data Loss Guarantee**: If local AI hangs, crashes, or is offline, ResillO’s **Circuit Breaker** instantly switches to an inline deterministic rule engine. Ingestion never blocks, and field reports are never lost.

---

## 🏗️ System Architecture

```mermaid
flowchart TD
    subgraph Clients["Field Clients"]
        WEB["🌐 Browser UI\n(IndexedDB Outbox)"]
        DSK["💻 Electron App\n(Local Bundle)"]
        AND["📱 Android App\n(Capacitor Shell)"]
    end

    subgraph Gateway["ResillO Core (FastAPI)"]
        API["REST Ingestion API\n/api/notes"]
        AUTH["Token Guard & Auth"]
        SYNC["Sync & Conflict Engine\n(Event Log & Idempotency)"]
    end

    subgraph Storage["Durable Storage"]
        DB[("SQLite 3\n(WAL + FULL Synchronous)")]
    end

    subgraph TriagePipeline["Resilient Triage Pipeline"]
        QUEUE["Async Triage Queue"]
        WORKER["Background Triage Worker"]
        CB{"Circuit Breaker"}
        LLM["🦙 Ollama (Gemma:2b)\nLocal JSON Inference"]
        FALLBACK["⚡ Deterministic Fallback\nRegex & Keyword Rule Engine"]
    end

    WEB & DSK & AND -->|HTTP / JSON| AUTH
    AUTH --> API
    API -->|Atomic Write| DB
    API -->|Append Event| SYNC
    API -->|Push Task| QUEUE
    QUEUE --> WORKER
    WORKER --> CB
    CB -->|Closed / Healthy| LLM
    CB -->|Open / Timeout| FALLBACK
    LLM -.->|Classified| DB
    FALLBACK -.->|Rule-Classified| DB
```

---

## ✨ Key Features

| Capability | Description |
|---|---|
| **🔒 Air-Gapped Intelligence** | Powered by local open-weight models (Gemma 2B via Ollama). Operates in complete radio silence with no external cloud dependencies. |
| **⚡ Multi-Tier Resilience** | Automatic 3-strike circuit breaker with cooldown isolation. Instant failover to keyword triage if Ollama stalls or crashes. |
| **🛡️ Crash-Durable Storage** | SQLite running with `WAL` (Write-Ahead Logging) and `FULL` synchronous disk flush guarantees zero database corruption on abrupt power loss. |
| **🔁 Idempotent Event Sourcing** | SHA-256 payload content hashing and UUID event sequencing prevents duplicate processing across intermittent mesh synchronizations. |
| **📲 Multi-Platform Support** | Unified dark-mode UI with native packaging for **Web**, **Electron Desktop**, and **Android (Capacitor)**. |

---

## 🚀 Quickstart (5 Minutes)

### 1. Start the Local AI Model
ResillO uses [Ollama](https://ollama.com) to run models locally on your CPU/GPU:

```bash
# 1. Pull the compact Gemma 2B model (~1.7 GB)
ollama pull gemma:2b

# 2. Verify model runs (type /bye to exit)
ollama run gemma:2b
```
> [!NOTE]
> Ensure Ollama is serving at `http://localhost:11434` (run `ollama serve` if not started as a system service).

---

### 2. Set Up the Python Backend

```bash
# Clone the repository
git clone https://github.com/your-org/resillo.git
cd resillo

# Create and activate virtual environment
python -m venv .venv

# On Linux/macOS:
source .venv/bin/activate
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

---

### 3. Launch ResillO

```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```
Open your browser at [http://127.0.0.1:8000](http://127.0.0.1:8000) to access the operator dashboard.

---

## 💻 Native Client Builds

### Electron Desktop App
The desktop application automatically bundles and manages the local backend process:

```bash
# Install Node dependencies
npm install

# Start the desktop application
npm run desktop
```

> [!TIP]
> If your Python virtual environment is in a non-default path, specify it via:
> ```powershell
> $env:RESILIO_PYTHON="D:\path\to\.venv\Scripts\python.exe"
> npm run desktop
> ```

### Android Mobile App (Capacitor)
The mobile client packages the field UI into an offline-ready APK:

```bash
# Initialize Android project (first-time only)
npm run android:add

# Sync assets and open in Android Studio
npm run android:sync
npm run android:open
```

* **Android Emulator:** Set the API Server URL in the app's settings dialog to `http://10.0.2.2:8000`.
* **Physical Device:** Connect over local Wi-Fi / ad-hoc network and set the server URL to your host machine's LAN IP (e.g. `http://192.168.1.50:8000`).

---

## ⚙️ Configuration Reference

All settings can be customized through environment variables or a local `.env` file:

| Environment Variable | Default | Purpose |
|---|---|---|
| `RESILIO_HOST` | `127.0.0.1` | Host interface to bind the API server |
| `RESILIO_PORT` | `8000` | Port for the API service |
| `RESILIO_DB_PATH` | `data/resillo.sqlite3` | File path for persistent SQLite database |
| `RESILIO_API_TOKEN` | *Auto-generated* | Secret token stored in `.token` file |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama daemon endpoint |
| `OLLAMA_MODEL` | `gemma:2b` | Ollama model tag for inference |
| `OLLAMA_CONNECT_TIMEOUT` | `0.5` | Connection timeout in seconds |
| `OLLAMA_READ_TIMEOUT` | `2.0` | Read timeout before triggering circuit breaker |
| `RESILIO_MAX_PAYLOAD` | `65536` | Maximum allowed request payload size (bytes) |
| `CORS_ORIGINS` | *Localhost, Capacitor* | Allowed origins for cross-origin access |

---

## 📡 REST API Reference

ResillO provides a lightweight, token-secured REST API:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Web dashboard single-page interface |
| `GET` | `/api/health` | Diagnostics for DB, queue depth, and Ollama circuit status |
| `GET` | `/api/diagnostics` | Protected operational telemetry (queue depth, circuit trips, conflicts, sanitized errors) |
| `GET` | `/api/auth/token` | Retrieve local session authorization token |
| `POST` | `/api/notes` | Ingest and enqueue raw field note for triage |
| `GET` | `/api/notes` | Fetch all triaged notes (sorted newest first) |
| `POST` | `/api/notes/{id}/refresh` | Trigger threat re-evaluation & escalation counter |
| `POST` | `/api/notes/refresh-all` | Batch update assessment states across all active notes |
| `POST` | `/api/sync` | Reconcile peer-to-peer event logs with conflict resolution |

### Sample Ingestion Request

```bash
curl -X POST http://127.0.0.1:8000/api/notes \
  -H "Content-Type: application/json" \
  -H "X-Resillo-Token: <YOUR_TOKEN>" \
  -d '{
    "raw_text": "Sector 4 bridge collapsed. 3 casualties reported, urgent medical supplies and generator required.",
    "operator_id": "RESCUE-UNIT-07"
  }'
```

### Sample Triage Response

```json
{
  "id": "c3a504be-bfd2-43ce-9a3d-4c33c3e6cf50",
  "operator_id": "RESCUE-UNIT-07",
  "raw_text": "Sector 4 bridge collapsed. 3 casualties reported, urgent medical supplies and generator required.",
  "timestamp": "2026-11-16T17:00:00.000Z",
  "revision": 2,
  "triage_status": "complete",
  "priority": "CRITICAL",
  "category": "Infrastructure",
  "location": "Sector 4 bridge",
  "summary": "Sector 4 bridge collapsed with 3 casualties; medical supplies and generator urgently needed.",
  "actionable_needs": [
    "Medical supplies",
    "Power generator",
    "Search and rescue team"
  ],
  "threat_status": "MONITORED",
  "check_count": 1,
  "last_checked": "2026-11-16T17:00:01.000Z",
  "human_override": false
}
```

---

## 🏕️ 7-Day Unassisted Field Dogfood & Recovery Guide

This protocol guides first responders and field engineers conducting autonomous, air-gapped 7-day field deployments without external connectivity or administrative intervention.

### 1. Pre-Flight Cold-Start Bootstrap (Single Command)
Before deploying hardware into the field, execute the automated pre-flight bootstrap script to validate the environment:

```bash
python scripts/verify_cold_start.py
```

The script verifies five critical layers:
1. **Python Dependencies:** Validates runtime libraries (`fastapi`, `uvicorn`, `httpx`, `pydantic`, `pytest`, `sqlite3`).
2. **Database Schema & Indexes:** Verifies `WAL` mode, `FULL` synchronous disk writes, table schemas (`notes`, `events`), indexes (`notes_timestamp`, `notes_triage`, `events_note`), and write readiness.
3. **Local Authentication:** Verifies `.token` persistence and SHA-256 token digest.
4. **Ollama & Deterministic Fallback:** Probes local Ollama inference and verifies < 50ms regex/keyword fallback triage with 3-strike circuit breaker failover.
5. **Mobile/Web Asset Sync:** Validates byte-for-byte synchronization between `index.html` and `www/index.html`.

---

### 2. Operational Monitoring & Diagnostics Cadence
During the 7-day trial, monitor node health without leaking sensitive field reports using the token-protected diagnostics API:

```bash
# Query sanitized system telemetry
TOKEN=$(cat data/resillo.token)
curl -s http://127.0.0.1:8000/api/diagnostics -H "X-Resillo-Token: $TOKEN" | jq .
```

#### Sanitized Telemetry Schema
```json
{
  "status": "healthy",
  "uptime_seconds": 86400.0,
  "queue_depth": 0,
  "circuit_breaker": {
    "state": "closed",
    "trips": 0,
    "current_failures": 0,
    "total_failures": 2,
    "total_successes": 412,
    "cooldown_remaining_seconds": 0.0
  },
  "conflicts": {
    "detected_in_session": 3,
    "total_notes": 85,
    "total_events": 92,
    "divergent_events": 7
  },
  "storage": {
    "db_online": true,
    "is_writable": true,
    "database_path": "data/resillo.sqlite3",
    "database_size_bytes": 147456
  },
  "recent_errors": []
}
```

> [!NOTE]
> **Data Privacy Guarantee**: `/api/diagnostics` exports sanitized error codes and technical summaries only. Note text, casualties, locations, and operator identifiers are stripped by regex sanitizers before export.

#### Bounded Storage Log Rotation
Log storage is strictly bounded to prevent disk exhaustion on resource-constrained field hardware:
- **Max File Size:** `1 MB` (`RESILIO_LOG_MAX_BYTES=1048576`)
- **Max Rotated Files:** `5` backups (`RESILIO_LOG_BACKUP_COUNT=5`)
- **Storage Cap:** Bounded to **~5 MB total log footprint** over the entire 7-day deployment. Logs rotate automatically into `data/logs/resillo.log.1` ... `.5`.

#### Daily 5-Minute Dogfood Audit
Run this exact audit once at the start of every field day. It authenticates to the protected diagnostics endpoint, checks SQLite integrity, verifies the rotating-log cap, and waits for the backend triage queue to drain:

```bash
python scripts/dogfood_check.py
```

PowerShell equivalent:

```powershell
python .\scripts\dogfood_check.py
```

The command must finish with `PASS dogfood_check: all checks passed` and exit code `0`. It is intentionally strict: stop the trial and reset the 7-day clock immediately for any `FAIL`, HTTP/API authentication error, database integrity error, queue that does not drain within 30 seconds, log-cap violation, missing note, duplicate note, orphaned process, or UI freeze.

#### Reboot and Force-Stop Recovery Check
At least once before Day 1 and after every device or app restart:

1. Record the current audit result:
   ```bash
   python scripts/dogfood_check.py
   ```
2. Force-stop the desktop window or Android package, then launch ResillO again. On Android:
   ```bash
   adb shell am force-stop com.resillo.triage
   adb shell monkey -p com.resillo.triage 1
   ```
3. Run the audit again:
   ```bash
   python scripts/dogfood_check.py
   ```
4. Confirm the previously captured IndexedDB outbox notes are visible exactly once and that the diagnostics `queue_depth` returns to `0`.

The browser/Capacitor IndexedDB outbox is durable across force-stop/reboot and flushes on the next successful launch. SQLite stores pending triage notes durably; the backend re-enqueues every `pending` note during startup, so pending jobs resume without user intervention. Any missing, duplicated, or permanently pending item is a hard failure.

#### Seven-Day Daily Record
Copy one line per day into the field log:

```text
Day __ / Date __________ / dogfood_check exit=__ / queue drain=__s / notes reconciled=YES|NO / orphaned processes=0|NO / UI freeze=NO|YES / operator=________
```

Do not continue the trial after a hard failure. Preserve the diagnostics output and reset Day 1 only after the cause is corrected and the recovery check passes twice.

---

### 3. Storage & Disaster Recovery Runbook

#### Scenario A: Abrupt Power Loss / Hard Crash
- **Behavior:** SQLite WAL mode with `PRAGMA synchronous = FULL` commits every transaction directly to disk.
- **Recovery:** Simply restart the service:
  ```bash
  python -m uvicorn main:app --host 127.0.0.1 --port 8000
  ```
  SQLite will automatically replay uncheckpointed WAL frames on startup with zero database corruption.

#### Scenario B: Disk-Full Storage Error (`507 Insufficient Storage`)
- **Symptom:** API returns HTTP `507` with `{"error": "storage_failure", "code": "DISK_FULL", "recoverable": true}`.
- **Client Outbox Durability:** Client browser and mobile apps detect the 507 status and immediately halt queue draining, safely preserving un-synced notes in local IndexedDB without data loss.
- **Remediation:**
  1. Free temporary disk space or purge expired diagnostic archives:
     ```bash
     rm -rf /tmp/* data/logs/*.log.*
     ```
  2. Verify write readiness:
     ```bash
     curl -s http://127.0.0.1:8000/api/diagnostics -H "X-Resillo-Token: $TOKEN" | grep '"is_writable":true'
     ```
  3. Client outboxes will automatically flush pending triage reports on the next 5-second sync cadence.

#### Scenario C: Read-Only Filesystem / Lockup (`503 Service Unavailable`)
- **Symptom:** API returns HTTP `503` with `{"error": "storage_failure", "code": "DATABASE_READONLY", "recoverable": true}`.
- **Client Outbox Durability:** Client outboxes preserve all field reports in IndexedDB storage.
- **Remediation:**
  1. Check filesystem mount permissions:
     ```bash
     mount -o remount,rw /data
     chmod 664 data/resillo.sqlite3*
     ```
  2. Once writable, the backend resumes accepting writes and client outboxes drain automatically.

#### Scenario D: Token Loss / Desynchronization
- If the client loses the authorization token:
  1. Read the persisted local token directly:
     ```bash
     cat data/resillo.token
     ```
  2. Paste into the client settings dialog via **API SERVER** button in the top navigation bar.

---

### 4. Deterministic Native Build Commands

Run deterministic build and asset synchronization scripts:

```bash
# Sync web assets to mobile directory and run Capacitor Android sync
npm run build:mobile

# Deterministic Electron desktop launch
npm run build:desktop

# Complete full asset & native bundle verification
npm run build
```

---

## 📂 Repository Structure

```
ResillO/
├── backend/                  # Core Python FastAPI backend
│   ├── api.py                # REST endpoints and authentication routes
│   ├── config.py             # Configuration defaults & token management
│   ├── db.py                 # SQLite WAL connection & schema setup
│   ├── sync_engine.py        # Event append log, SHA-256 idempotency & merge logic
│   ├── triage_engine.py      # Ollama client, CircuitBreaker & fallback rule engine
│   └── triage_worker.py      # Async background queue triage worker
├── desktop/                  # Electron desktop container
│   ├── main.cjs              # Electron main process & backend supervisor
│   └── preload.cjs           # Secure bridge & inter-process communication
├── android/                  # Capacitor Android native wrapper
├── tests/                    # Test suites
│   └── test_backend.py       # Triage, circuit breaker & idempotency tests
├── main.py                   # Server entrypoint & FastAPI lifespan hooks
├── index.html                # Responsive Tailwind CSS field dashboard
├── capacitor.config.ts       # Capacitor native bridge configuration
├── package.json              # Scripts for desktop & mobile toolchains
└── requirements.txt          # Python dependencies
```

---

## 🧪 Testing & Verification

Run the test suite to verify circuit breaker failover, idempotency, and conflict resolution:

```bash
pytest -v
```

---

<p align="center">
  Built for resilient crisis communications & emergency response 🕊️
</p>
