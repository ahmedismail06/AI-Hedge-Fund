
import os
from dotenv import load_dotenv
from backend.memory.vector_store import _get_client
from datetime import date

load_dotenv()

def check_status():
    client = _get_client()
    
    # Check pipeline status
    cfg = client.table("pm_config").select("pipeline_is_running, pipeline_last_run").eq("id", 1).single().execute()
    print(f"Pipeline Is Running: {cfg.data.get('pipeline_is_running')}")
    print(f"Pipeline Last Run: {cfg.data.get('pipeline_last_run')}")
    
    # Check recent logs
    logs = client.table("orchestrator_log").select("*").order("created_at", desc=True).limit(5).execute()
    print("\nRecent Orchestrator Logs:")
    for log in logs.data:
        print(f"[{log['created_at']}] {log['event_type']}: {log['detail']}")

    # Check if watchlist is being populated for today
    today = date.today().isoformat()
    count = client.table("watchlist").select("ticker", count="exact").eq("run_date", today).execute()
    print(f"\nWatchlist entries for today ({today}): {count.count}")

if __name__ == "__main__":
    check_status()
