import os
import json
import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import httpx
from pymongo import MongoClient

app = FastAPI()

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
                "http://localhost:11434/api/generate",
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
        
    note_record = {
        "operator_id": req.operator_id,
        "raw_text": req.raw_text,
        "timestamp": datetime.datetime.utcnow().isoformat(),
        **parsed_data
    }
    
    if db_collection is not None:
        try:
            db_collection.insert_one(note_record)
            note_record["_id"] = str(note_record["_id"])
        except Exception as e:
            print(f"MongoDB insert failed: {e}")
            in_memory_notes.append(note_record)
    else:
        in_memory_notes.append(note_record)
        
    return note_record

@app.get("/api/notes")
async def get_notes():
    if db_collection is not None:
        try:
            notes = list(db_collection.find().sort("timestamp", -1))
            for n in notes:
                n["_id"] = str(n["_id"])
            return notes
        except Exception as e:
            print(f"MongoDB read failed: {e}")
            
    # Fallback / In-memory
    sorted_notes = sorted(in_memory_notes, key=lambda x: x["timestamp"], reverse=True)
    return sorted_notes

@app.get("/api/health")
async def health_check():
    ollama_online = False
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get("http://localhost:11434/", timeout=2.0)
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