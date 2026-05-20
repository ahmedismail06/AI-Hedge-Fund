import os
import logging
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

def check_tables():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    supabase = create_client(url, key)

    print("Checking positions...")
    pos = supabase.table("positions").select("ticker, status").execute()
    print(f"Total positions: {len(pos.data)}")
    for p in pos.data:
        print(f"  {p['ticker']}: {p['status']}")

    print("\nChecking portfolio_metrics...")
    metrics = supabase.table("portfolio_metrics").select("*").execute()
    print(f"Total metrics rows: {len(metrics.data)}")

    print("\nChecking pm_calibration...")
    cal = supabase.table("pm_calibration").select("*").execute()
    print(f"Total calibration rows: {len(cal.data)}")

if __name__ == "__main__":
    check_tables()
