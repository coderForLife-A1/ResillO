import os
import json
import uuid
import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
from pymongo import MongoClient
from bson import ObjectId

app = FastAPI()

# Native shells (Electron and Capacitor) call the same API as the browser UI.
# Keep the allow-list configurable so deployments do not need code changes.
cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000,capacitor://localhost,http://localhost,https://localhost").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory storage fallback
in_memory_notes = []

# MongoDB setup
MONGO_URI = os.getenv("MONGO_URI")
db_client = None
db_collection = None
if MONGO_URI:
    try:
        db_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        db_client.admin.command('ping')
        db = db_client["resilio"]
        db_collection = db["notes"]
    except Exception as e:
        print(f"MongoDB connection failed: {e}")
        db_client = None
        db_collection = None

class NoteRequest(BaseModel):
    raw_text: str
    operator_id: str

def fallback_parse(text: str):
    text_lower = text.lower()
    
    priority = "LOW"
    if any(word in text_lower for word in ["critical", "collapse", "death", "severe"]):
        priority = "CRITICAL"
    elif any(word in text_lower for word in ["high", "shortage", "urgent"]):
        priority = "HIGH"
    elif any(word in text_lower for word in ["medium", "injury"]):
        priority = "MEDIUM"

    category = "Status Update"
    if any(word in text_lower for word in ["medical", "doctor", "blood"]):
        category = "Medical"
    elif any(word in text_lower for word in ["supplies", "food", "water", "generators"]):
        category = "Supplies"
    elif any(word in text_lower for word in ["bridge", "road", "power", "infrastructure", "grid"]):
        category = "Infrastructure"
        
    return {
        "priority": priority,
        "category": category,
        "location": "Unknown",
        "summary": text[:100] + "..." if len(text) > 100 else text,
        "actionable_needs": ["Review manually", "Verify local Ollama node connection"],
        "status": "fallback_mode"
    }

def evaluate_threat_status(note: dict, check_increment: bool = False):
    """
    Evaluates whether the threat is PERSISTING, WORSENED, or MONITORED.
    Takes into account priority, elapsed time, and automated check cycles.
    """
    priority = (note.get("priority") or "LOW").upper()
    checks = note.get("check_count", 1) + (1 if check_increment else 0)
    note["check_count"] = checks
    
    # Calculate elapsed time if timestamp exists
    try:
        created_time = datetime.datetime.fromisoformat(note.get("timestamp"))
        elapsed_seconds = (datetime.datetime.utcnow() - created_time).total_seconds()
    except Exception:
        elapsed_seconds = checks * 5
    
    # Severe/critical threats worsen if unaddressed over time or consecutive checks
    if priority == "CRITICAL":
        if checks >= 3 or elapsed_seconds >= 15:
            return "WORSENED"
        return "PERSISTING"
    elif priority == "HIGH":
        if checks >= 5 or elapsed_seconds >= 30:
            return "WORSENED"
        return "PERSISTING"
    elif priority == "MEDIUM":
        return "PERSISTING"
    else:
        return "MONITORED"

@app.get("/", response_class=HTMLResponse)
async def read_index():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>index.html not found</h1>"

@app.post("/api/notes")
async def create_note(req: NoteRequest):
    prompt = f"""
You are an expert triage assistant. Extract the following from the raw text:
Priority: CRITICAL, HIGH, MEDIUM, or LOW
Category: Medical, Supplies, Infrastructure, or Status Update
Location: Extract the location if present
Summary: A short 1-sentence summary
Actionable Needs: A list of actionable items.

Output strictly in this JSON format:
{{
    "priority": "...",
    "category": "...",
    "location": "...",
    "summary": "...",
    "actionable_needs": ["...", "..."]
}}

Raw text: {req.raw_text}
"""
    
    parsed_data = None
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{os.getenv('OLLAMA_URL', 'http://localhost:11434').rstrip('/')}/api/generate",
                json={
                    "model": "gemma:2b",
                    "prompt": prompt,
                    "stream": False,
                    "format": "json"
                },
                timeout=3.0
            )
            response.raise_for_status()
            res_json = response.json()
            parsed_data = json.loads(res_json.get("response", "{}"))
            # Validate basic keys exist
            if not all(k in parsed_data for k in ("priority", "category", "location", "summary", "actionable_needs")):
                parsed_data = None
    except Exception as e:
        print(f"Ollama failed or timed out: {e}")
        parsed_data = None

    if not parsed_data:
        parsed_data = fallback_parse(req.raw_text)
        
    now_iso = datetime.datetime.utcnow().isoformat()
    note_id = str(uuid.uuid4())
    
    note_record = {
        "id": note_id,
        "operator_id": req.operator_id,
        "raw_text": req.raw_text,
        "timestamp": now_iso,
        "last_checked": now_iso,
        "check_count": 1,
        **parsed_data
    }
    
    # Set initial threat status based on priority
    note_record["threat_status"] = evaluate_threat_status(note_record, check_increment=False)
    
    if db_collection is not None:
        try:
            insert_result = db_collection.insert_one(note_record)
            note_record["_id"] = str(insert_result.inserted_id)
        except Exception as e:
            print(f"MongoDB insert failed: {e}")
            in_memory_notes.append(note_record)
    else:
        in_memory_notes.append(note_record)
        
    return note_record

@app.get("/api/notes")
async def get_notes():
    now_iso = datetime.datetime.utcnow().isoformat()
    if db_collection is not None:
        try:
            notes = list(db_collection.find().sort("timestamp", -1))
            for n in notes:
                n["_id"] = str(n["_id"])
                if "id" not in n:
                    n["id"] = n["_id"]
                if "last_checked" not in n:
                    n["last_checked"] = n.get("timestamp", now_iso)
                if "threat_status" not in n:
                    n["threat_status"] = evaluate_threat_status(n, check_increment=False)
            return notes
        except Exception as e:
            print(f"MongoDB read failed: {e}")
            
    # Fallback / In-memory
    for n in in_memory_notes:
        if "id" not in n:
            n["id"] = str(uuid.uuid4())
        if "last_checked" not in n:
            n["last_checked"] = n.get("timestamp", now_iso)
        if "threat_status" not in n:
            n["threat_status"] = evaluate_threat_status(n, check_increment=False)
            
    sorted_notes = sorted(in_memory_notes, key=lambda x: x["timestamp"], reverse=True)
    return sorted_notes

@app.post("/api/notes/{note_id}/refresh")
async def refresh_note(note_id: str):
    now_iso = datetime.datetime.utcnow().isoformat()
    
    # Try finding in MongoDB
    if db_collection is not None:
        try:
            query = {"$or": [{"id": note_id}]}
            if ObjectId.is_valid(note_id):
                query["$or"].append({"_id": ObjectId(note_id)})
                
            note = db_collection.find_one(query)
            if note:
                new_threat = evaluate_threat_status(note, check_increment=True)
                db_collection.update_one(
                    {"_id": note["_id"]},
                    {"$set": {
                        "last_checked": now_iso,
                        "threat_status": new_threat,
                        "check_count": note.get("check_count", 1)
                    }}
                )
                note["_id"] = str(note["_id"])
                note["last_checked"] = now_iso
                note["threat_status"] = new_threat
                return note
        except Exception as e:
            print(f"MongoDB refresh error: {e}")

    # Search in-memory notes
    for note in in_memory_notes:
        if note.get("id") == note_id or str(note.get("_id")) == note_id:
            new_threat = evaluate_threat_status(note, check_increment=True)
            note["last_checked"] = now_iso
            note["threat_status"] = new_threat
            return note

    # If note not found, synthesize a refreshed response so field app does not crash
    return {
        "id": note_id,
        "last_checked": now_iso,
        "threat_status": "PERSISTING",
        "status": "refreshed"
    }

@app.post("/api/notes/refresh-all")
async def refresh_all_notes():
    """Batch refresh all notes and recalculate threat status"""
    now_iso = datetime.datetime.utcnow().isoformat()
    
    if db_collection is not None:
        try:
            notes = list(db_collection.find())
            for n in notes:
                new_threat = evaluate_threat_status(n, check_increment=True)
                db_collection.update_one(
                    {"_id": n["_id"]},
                    {"$set": {
                        "last_checked": now_iso,
                        "threat_status": new_threat,
                        "check_count": n.get("check_count", 1)
                    }}
                )
            return await get_notes()
        except Exception as e:
            print(f"MongoDB batch refresh failed: {e}")

    for note in in_memory_notes:
        note["threat_status"] = evaluate_threat_status(note, check_increment=True)
        note["last_checked"] = now_iso
        
    return await get_notes()

@app.get("/api/health")
async def health_check():
    ollama_online = False
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{os.getenv('OLLAMA_URL', 'http://localhost:11434').rstrip('/')}/",
                timeout=2.0,
            )
            if res.status_code == 200:
                ollama_online = True
    except:
        pass
        
    db_online = False
    if db_client is not None:
        try:
            db_client.admin.command('ping')
            db_online = True
        except:
            pass
            
    return {
        "ollama_online": ollama_online,
        "db_online": db_online
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)