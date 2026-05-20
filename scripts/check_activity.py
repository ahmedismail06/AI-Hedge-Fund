import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

def check_activity():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    supabase = create_client(url, key)

    print("Checking watchlist...")
    wl = supabase.table("watchlist").select("ticker, run_date").limit(5).execute()
    print(f"Total watchlist rows: {len(wl.data)} (sampled)")
    for r in wl.data:
        print(f"  {r['ticker']} on {r['run_date']}")

    print("\nChecking research memos...")
    memos = supabase.table("memos").select("ticker, created_at").limit(5).execute()
    print(f"Total memos: {len(memos.data)} (sampled)")

    print("\nChecking orders...")
    orders = supabase.table("orders").select("ticker, status, created_at").limit(5).execute()
    print(f"Total orders: {len(orders.data)} (sampled)")

if __name__ == "__main__":
    check_activity()
