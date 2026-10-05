import httpx
import time
import sys

API_BASE = "http://localhost:8000"

def test_pipeline():
    print("--- Resilio Local E2E Test ---")
    
    # 1. Test backend health
    try:
        health_res = httpx.get(f"{API_BASE}/api/health", timeout=5.0)
        health_res.raise_for_status()
        health = health_res.json()
        print(f"Backend Health: OK")
        print(f"Ollama Status: {'ONLINE' if health.get('ollama_online') else 'OFFLINE (Fallback regex logic will be used)'}")
        print(f"Database Status: {'ONLINE' if health.get('db_online') else 'FALLBACK (In-memory list)'}")
    except Exception as e:
        print(f"Failed to connect to backend at {API_BASE}: {e}")
        print("Please ensure you have started the server with: uvicorn main:app --reload")
        sys.exit(1)

    print("\n--- Sending Mock Payload ---")
    payload = {
        "raw_text": "Emergency! The central power grid is down in the downtown sector after the storm. We need portable generators and electricians immediately.",
        "operator_id": "TEST-OP-1"
    }
    
    try:
        t0 = time.time()
        res = httpx.post(f"{API_BASE}/api/notes", json=payload, timeout=15.0)
        res.raise_for_status()
        note = res.json()
        t1 = time.time()
        
        print(f"\n✅ Success in {t1-t0:.2f}s!")
        print("Triaged Result:")
        print(f"  Priority: {note.get('priority')}")
        print(f"  Category: {note.get('category')}")
        print(f"  Summary: {note.get('summary')}")
        print(f"  Actionable Needs: {note.get('actionable_needs')}")
        print(f"  Location: {note.get('location')}")
        
    except Exception as e:
        print(f"\n❌ Failed to create note: {e}")
        
if __name__ == "__main__":
    test_pipeline()
