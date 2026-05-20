
import sys
import os
sys.path.append(os.getcwd())
from backend.db.utils import get_supabase_client

def debug_update():
    client = get_supabase_client()
    ticker = "PAR"
    
    print(f"--- Debugging Supabase Update for {ticker} ---")
    
    # 1. Fetch current row
    resp = client.table('positions').select('*').eq('ticker', ticker).eq('status', 'OPEN').execute()
    if not resp.data:
        print("No row found.")
        return
    print(f"Current row: {resp.data[0]}")

    # 2. Try explicit strings first to test connectivity/permission
    print("\nAttempting update to 'DEBUG_VAL'...")
    res1 = client.table('positions').update({"exit_action": "DEBUG_VAL"}).eq('ticker', ticker).eq('status', 'OPEN').execute()
    print(f"Update response (DEBUG_VAL): {res1.data}")
    
    # 3. Try None
    print("\nAttempting update to None...")
    res2 = client.table('positions').update({"exit_action": None, "exit_trim_pct": None}).eq('ticker', ticker).eq('status', 'OPEN').execute()
    print(f"Update response (None): {res2.data}")

if __name__ == "__main__":
    debug_update()
