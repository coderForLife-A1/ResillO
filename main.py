"""
AegisField: Offline Emergency Notes Triage
Single-file FastAPI backend. Local LLM (Ollama / gemma:2b) + MongoDB Atlas with in-memory fallback.
"""
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from pymongo import DESCENDING, MongoClient

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
OLLAMA_BASE = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma:2b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "180"))  # first model load can be slow
MONGO_URI = os.getenv("MONGO_URI", "").strip()
DB_NAME = "field_notes"
COLLECTION_NAME = "triaged_reports"

PRIORITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
CATEGORIES = ["Medical", "Supplies", "Infrastructure", "Status Update"]

SYSTEM_PROMPT = """You are an emergency field-operations triage assistant.
You receive a raw, messy field note written by a disaster relief operator.
Respond with ONE valid JSON object and NOTHING else (no markdown, no commentary).

The JSON object must have exactly these keys:
{
  "priority": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW",
  "category": "Medical" | "Supplies" | "Infrastructure" | "Status Update",
  "location": "<place mentioned in the note, or \\"Unknown\\">",
  "summary": "<1-2 sentence plain summary>",
  "actionable_needs": ["<short concrete need>", "..."]
}

Rules:
- CRITICAL: immediate threat to life (injuries, trapped people, collapse, fire, flooding, no medicine for acute conditions).
- HIGH: urgent shortage or blockage that will become life-threatening soon (no clean water, blocked access route).
- MEDIUM: important but not urgent problems.
- LOW: routine check-ins or informational updates.
- actionable_needs must be an empty list [] if nothing is needed.
- Never invent facts that are not in the note."""

# --------------------------------------------------------------------------- #
# Storage: MongoDB Atlas with graceful in-memory fallback
# --------------------------------------------------------------------------- #
memory_store: List[Dict[str, Any]] = []
memory_lock = threading.Lock()
mongo_collection = None
_last_mongo_attempt = 0.0


def connect_mongo(force: bool = False) -> None:
    """Try to (re)connect to MongoDB. Never raises; falls back to memory on failure."""
    global mongo_collection, _last_mongo_attempt
    if not MONGO_URI or mongo_collection is not None:
        return
    now = time.time()
    if not force and now - _last_mongo_attempt < 30:  # don't hammer a dead cluster
        return
    _last_mongo_attempt = now
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
        client.admin.command("ping")
        col = client[DB_NAME][COLLECTION_NAME]
        col.create_index([("timestamp", DESCENDING)])
        mongo_collection = col
        print("[AegisField] MongoDB connected.")
    except Exception as exc:  # noqa: BLE001
        mongo_collection = None
        print(f"[AegisField] MongoDB unavailable, using in-memory storage: {exc}")


connect_mongo(force=True)


def storage_mode() -> str:
    return "mongodb" if mongo_collection is not None else "memory"


def save_record(record: Dict[str, Any]) -> None:
    global mongo_collection
    connect_mongo()
    if mongo_collection is not None:
        try:
            mongo_collection.insert_one(dict(record))  # copy: pymongo mutates with _id
            return
        except Exception as exc:  # noqa: BLE001
            print(f"[AegisField] Mongo insert failed, falling back to memory: {exc}")
            mongo_collection = None
    with memory_lock:
        memory_store.append(record)


def load_records() -> List[Dict[str, Any]]:
    global mongo_collection
    connect_mongo()
    records: List[Dict[str, Any]] = []
    if mongo_collection is not None:
        try:
            records.extend(
                mongo_collection.find({}, {"_id": 0}).sort("timestamp", DESCENDING).limit(500)
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[AegisField] Mongo read failed, using memory only: {exc}")
            mongo_collection = None
    with memory_lock:  # includes anything saved during an outage
        records.extend(memory_store)
    records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return records


# --------------------------------------------------------------------------- #
# Triage: Ollama (primary) + keyword heuristic (safety net)
# --------------------------------------------------------------------------- #
CRITICAL_KW = ["collapse", "trapped", "unconscious", "bleeding", "fire", "drown", "not breathing",
               "cardiac", "critical", "dying", "explosion", "landslide", "flood", "severe"]
HIGH_KW = ["injured", "injury", "short on", "shortage", "no water", "out of", "blocked", "stranded",
           "urgent", "running low", "contaminated", "fever", "insulin", "medicine"]
MEDIUM_KW = ["damaged", "delay", "limited", "low on", "need", "repair", "intermittent"]
CATEGORY_KW = {
    "Medical": ["injur", "medic", "doctor", "wound", "bleed", "insulin", "fever", "sick", "patient",
                "hospital", "first aid", "bandage"],
    "Supplies": ["water", "food", "blanket", "tent", "fuel", "supply", "supplies", "rations",
                 "shelter", "generator", "batter"],
    "Infrastructure": ["road", "bridge", "power", "electric", "debris", "tower", "network",
                       "collapse", "blocked", "dam", "pipeline", "building"],
}
LOCATION_RE = re.compile(
    r"\b(?:sector|zone|block|camp|district|ward|village|bridge|highway|route|road|street|"
    r"station|school|hospital)\s+[\w-]+(?:\s+[A-Z][\w-]+)?",
    re.IGNORECASE,
)


def heuristic_triage(text: str) -> Dict[str, Any]:
    t = text.lower()
    if any(k in t for k in CRITICAL_KW):
        priority = "CRITICAL"
    elif any(k in t for k in HIGH_KW):
        priority = "HIGH"
    elif any(k in t for k in MEDIUM_KW):
        priority = "MEDIUM"
    else:
        priority = "LOW"

    category, best = "Status Update", 0
    for cat, kws in CATEGORY_KW.items():
        hits = sum(1 for k in kws if k in t)
        if hits > best:
            category, best = cat, hits

    match = LOCATION_RE.search(text)
    location = match.group(0).strip() if match else "Unknown"

    clauses = [c.strip() for c in re.split(r"[.;,\n]+", text) if c.strip()]
    need_markers = HIGH_KW + MEDIUM_KW + ["need", "require", "request"]
    needs = [c for c in clauses if any(k in c.lower() for k in need_markers)][:5]

    summary = " ".join(clauses[:2])[:220] or text[:220]
    return {
        "priority": priority,
        "category": category,
        "location": location,
        "summary": summary,
        "actionable_needs": needs,
    }


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Pull a JSON object out of model output, tolerating fences or stray prose."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def normalize_triage(parsed: Dict[str, Any], raw_text: str) -> Dict[str, Any]:
    """Coerce model output into the strict schema, filling gaps from the heuristic."""
    fallback = heuristic_triage(raw_text)

    priority = str(parsed.get("priority", "")).strip().upper()
    if priority not in PRIORITIES:
        priority = fallback["priority"]

    category_in = str(parsed.get("category", "")).strip().lower()
    category = next((c for c in CATEGORIES if c.lower() == category_in), fallback["category"])

    location = str(parsed.get("location") or "").strip() or "Unknown"

    summary = str(parsed.get("summary") or "").strip() or fallback["summary"]

    needs = parsed.get("actionable_needs", [])
    if isinstance(needs, str):
        needs = [n.strip() for n in re.split(r"[;\n]+", needs) if n.strip()]
    if not isinstance(needs, list):
        needs = []
    needs = [str(n).strip() for n in needs if str(n).strip()][:8]

    return {
        "priority": priority,
        "category": category,
        "location": location,
        "summary": summary,
        "actionable_needs": needs,
    }


def triage_with_ollama(raw_text: str) -> Dict[str, Any]:
    payload = {
        "model": OLLAMA_MODEL,
        "system": SYSTEM_PROMPT,
        "prompt": f"Field note:\n\"\"\"\n{raw_text}\n\"\"\"\n\nJSON:",
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_predict": 400},
    }
    resp = requests.post(f"{OLLAMA_BASE}/api/generate", json=payload, timeout=OLLAMA_TIMEOUT)
    resp.raise_for_status()
    parsed = extract_json(resp.json().get("response", ""))
    if parsed is None:
        raise ValueError("Model did not return valid JSON")
    return normalize_triage(parsed, raw_text)


# --------------------------------------------------------------------------- #
# Search: keyword scoring with a small emergency-domain synonym expansion
# --------------------------------------------------------------------------- #
SYNONYM_GROUPS = [
    {"water", "hydration", "drinking", "thirst", "well", "contaminated"},
    {"medical", "medic", "medicine", "injured", "injury", "wound", "doctor", "hospital", "insulin", "bleeding"},
    {"food", "rations", "hunger", "meal"},
    {"road", "bridge", "highway", "route", "debris", "blocked", "collapse", "collapsed"},
    {"power", "electricity", "generator", "fuel", "battery", "outage"},
    {"shelter", "tent", "housing", "blanket"},
    {"critical", "urgent", "emergency", "severe"},
]


def expand_terms(query: str) -> List[str]:
    tokens = [t for t in re.findall(r"[\w-]+", query.lower()) if t]
    expanded = set(tokens)
    for tok in tokens:
        for group in SYNONYM_GROUPS:
            if tok in group:
                expanded |= group
    return list(expanded), tokens  # type: ignore[return-value]


def score_record(rec: Dict[str, Any], terms: List[str], originals: List[str]) -> int:
    t = rec.get("triage", {})
    fields = [
        (t.get("summary", ""), 3),
        (" ".join(t.get("actionable_needs", [])), 3),
        (t.get("location", ""), 3),
        (t.get("category", ""), 2),
        (t.get("priority", ""), 2),
        (rec.get("raw_text", ""), 1),
    ]
    score = 0
    for text, weight in fields:
        low = str(text).lower()
        for term in terms:
            if term in low:
                score += weight * (2 if term in originals else 1)
    return score


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
app = FastAPI(title="AegisField", description="Offline Emergency Notes Triage")


class NoteIn(BaseModel):
    raw_text: str = Field(..., min_length=1, max_length=5000)
    operator_id: str = Field(default="unknown", max_length=100)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    page = BASE_DIR / "index.html"
    if not page.exists():
        return HTMLResponse("<h1>index.html not found next to main.py</h1>", status_code=500)
    return HTMLResponse(page.read_text(encoding="utf-8"))


@app.get("/api/status")
def status() -> Dict[str, Any]:
    ollama_up, model_ready = False, False
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=2)
        r.raise_for_status()
        ollama_up = True
        names = [m.get("name", "") for m in r.json().get("models", [])]
        model_ready = any(n == OLLAMA_MODEL or n.startswith(OLLAMA_MODEL.split(":")[0] + ":") and n == OLLAMA_MODEL
                          or n.split(":")[0] == OLLAMA_MODEL.split(":")[0] and ":" not in OLLAMA_MODEL
                          for n in names)
    except Exception:  # noqa: BLE001
        pass
    return {
        "ollama": ollama_up,
        "model_ready": model_ready,
        "model": OLLAMA_MODEL,
        "storage": storage_mode(),
    }


@app.post("/api/notes")
def create_note(note: NoteIn) -> Dict[str, Any]:  # sync def -> runs in threadpool, won't block the loop
    raw_text = note.raw_text.strip()
    if not raw_text:
        raise HTTPException(status_code=422, detail="raw_text cannot be empty")

    try:
        triage = triage_with_ollama(raw_text)
        source = f"ollama:{OLLAMA_MODEL}"
    except Exception as exc:  # noqa: BLE001
        print(f"[AegisField] Ollama triage failed, using heuristic fallback: {exc}")
        triage = heuristic_triage(raw_text)
        source = "heuristic-fallback"

    record = {
        "id": uuid.uuid4().hex,
        "operator_id": note.operator_id.strip() or "unknown",
        "raw_text": raw_text,
        "triage": triage,
        "triage_source": source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    save_record(record)
    return {**record, "storage": storage_mode()}


@app.get("/api/notes")
def list_notes() -> List[Dict[str, Any]]:
    return load_records()


@app.get("/api/search")
def search_notes(q: str = Query("", description="Search query")) -> List[Dict[str, Any]]:
    records = load_records()
    q = q.strip()
    if not q:
        return records
    terms, originals = expand_terms(q)
    scored = [(score_record(r, terms, originals), r) for r in records]
    scored = [s for s in scored if s[0] > 0]
    scored.sort(key=lambda s: (s[0], s[1].get("timestamp", "")), reverse=True)
    return [r for _, r in scored]