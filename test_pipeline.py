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
        "raw_text": "CRITICAL: The central power grid has collapsed in the downtown hospital zone. Backup generators failing rapidly, patients in ICU at severe risk.",
        "operator_id": "TEST-OP-1"
    }
    
    try:
        t0 = time.time()
        res = httpx.post(f"{API_BASE}/api/notes", json=payload, timeout=15.0)
        res.raise_for_status()
        note = res.json()
        t1 = time.time()
        
        note_id = note.get("id") or note.get("_id")
        print(f"\n✅ Note Created in {t1-t0:.2f}s! [ID: {note_id}]")
        print("Triaged Result:")
        print(f"  Priority: {note.get('priority')}")
        print(f"  Threat Status: {note.get('threat_status')}")
        print(f"  Category: {note.get('category')}")
        print(f"  Summary: {note.get('summary')}")
        print(f"  Actionable Needs: {note.get('actionable_needs')}")
        print(f"  Last Checked: {note.get('last_checked')}")
        
        # Test 5s cadence refresh endpoint
        print("\n--- Testing Note Refresh Endpoint ---")
        time.sleep(1)
        ref_res = httpx.post(f"{API_BASE}/api/notes/{note_id}/refresh", timeout=5.0)
        ref_res.raise_for_status()
        refreshed_note = ref_res.json()
        print(f"✅ Refresh verified!")
        print(f"  Updated Last Checked: {refreshed_note.get('last_checked')}")
        print(f"  Updated Threat Status: {refreshed_note.get('threat_status')}")
        
    except Exception as e:
        print(f"\n❌ Failed during test: {e}")
        
if __name__ == "__main__":
    test_pipeline()
