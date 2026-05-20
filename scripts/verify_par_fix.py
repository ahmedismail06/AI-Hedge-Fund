
import sys
import os
from datetime import datetime, timezone

# Ensure backend is in path
sys.path.append(os.getcwd())

from backend.agents.orchestrator import _route_decision
from backend.db.utils import get_supabase_client

def verify():
    ticker = "PAR"
    client = get_supabase_client()
    
    print(f"--- Verification Script for {ticker} HOLD Fix ---")
    
    # 0. Setup state (force a TRIM if needed to test clearing)
    print("Setting up test state (exit_action='TRIM')...")
    setup_res = client.table('positions').update({
        "exit_action": "TRIM",
        "exit_trim_pct": 35.0
    }).eq("ticker", ticker).eq("status", "OPEN").execute()
    
    if not setup_res.data:
        print(f"Error: Setup failed. No OPEN position for {ticker}?")
        return
    print(f"Current database state: exit_action={setup_res.data[0].get('exit_action')}")

    # 1. Check current state via select
    resp = client.table('positions').select('ticker,exit_action,exit_trim_pct').eq('ticker', ticker).eq('status', 'OPEN').execute()
    pos = resp.data[0]
    print(f"Pre-fix state: exit_action='{pos.get('exit_action')}', exit_trim_pct={pos.get('exit_trim_pct')}")

    # 2. Simulate the HOLD decision routing
    decision_data = {"decision": "HOLD"}
    decision_record = {
        "category": "EXIT_TRIM", 
        "ticker": ticker,
        "memo_id": None
    }
    
    print(f"\nRouting HOLD decision for {ticker}...")
    status = _route_decision(decision_data, decision_record, auto_approve=True)
    print(f"Routing result status: {status}")
    
    # 3. Verify the database was cleared
    resp_new = client.table('positions').select('exit_action,exit_trim_pct').eq('ticker', ticker).eq('status', 'OPEN').execute()
    pos_new = resp_new.data[0]
    
    print(f"\nPost-fix state:")
    print(f"  exit_action: {pos_new.get('exit_action')} (Expected: None)")
    print(f"  exit_trim_pct: {pos_new.get('exit_trim_pct')} (Expected: None)")
    
    if pos_new.get('exit_action') is None and pos_new.get('exit_trim_pct') is None:
        print("\n✅ SUCCESS: Pending exit action successfully cleared.")
    else:
        print("\n❌ FAILURE: exit_action or exit_trim_pct still present in database.")

if __name__ == "__main__":
    verify()
