# 🛡️ ResillO: Offline Emergency Notes Triage

An offline-first tool for field operators in connectivity-dead zones. It turns messy field notes into structured triage data (priority, category, location, summary, needs) using a **local** open-weight model (Gemma 2B via Ollama), so nothing leaves the device. Reports are stored in MongoDB Atlas, or in memory when the database is unreachable.

## Quickstart (about 5 minutes)

### 1. Install and start the local model
Install [Ollama](https://ollama.com), then:

```bash
ollama pull gemma:2b      # one-time download (~1.7 GB)
ollama run gemma:2b       # starts/loads the model; type /bye to exit the chat
```
Ollama serves its API at `http://localhost:11434`. If it isn't running, start it with `ollama serve`.

### 2. Install dependencies
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. (Optional) Connect MongoDB Atlas
```bash
export MONGO_URI="mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority"
# Windows PowerShell: $env:MONGO_URI="..."
```
Data goes to database `field_notes`, collection `triaged_reports`.
If `MONGO_URI` is missing or unreachable, ResillO falls back to **in-memory storage** (data is lost on restart). The header badge shows which mode is active.

### 4. Run
```bash
uvicorn main:app --reload
```
Open http://127.0.0.1:8000

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MONGO_URI` | *(empty)* | MongoDB connection string; empty means in-memory |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama base URL |
| `OLLAMA_MODEL` | `gemma:2b` | Model used for triage |
| `OLLAMA_TIMEOUT` | `180` | Seconds to wait for the model (first load is slow) |

## API

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Dashboard |
| POST | `/api/notes` | Body: `{"raw_text": "...", "operator_id": "..."}`; triages and stores a note |
| GET | `/api/notes` | All notes, newest first |
| GET | `/api/search?q=water` | Keyword search with emergency-term synonym expansion |
| GET | `/api/status` | Ollama and storage status (powers the header badges) |

Try it:
```bash
curl -X POST http://127.0.0.1:8000/api/notes \
  -H "Content-Type: application/json" \
  -d '{"raw_text":"Road on Sector 4 blocked by debris, team short on clean water, 2 injured","operator_id":"op-01"}'
```

## Resilience
- **Ollama down or bad JSON:** the note is still triaged by a keyword rule engine and tagged `rule-based` in the UI, so reports are never lost.
- **MongoDB down:** notes are kept in memory, and the app retries the connection periodically.
- **Model output drift:** responses are validated and normalized to the strict schema (`CRITICAL|HIGH|MEDIUM|LOW`, four categories).

## Project layout
```
main.py            FastAPI backend
index.html         Tailwind dark-mode dashboard
requirements.txt
README.md
```