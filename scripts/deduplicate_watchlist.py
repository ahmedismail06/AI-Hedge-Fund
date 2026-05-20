
import os
import argparse
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

def deduplicate_watchlist(dry_run=True):
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    if not url or not key:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_KEY/SUPABASE_KEY must be set in .env")
        return

    supabase = create_client(url, key)

    print("Fetching watchlist entries...")
    resp = supabase.table("watchlist").select("id, ticker, run_date").execute()
    rows = resp.data
    
    if not rows:
        print("No entries found in watchlist.")
        return

    ticker_map = {}
    for row in rows:
        ticker = row['ticker']
        if ticker not in ticker_map:
            ticker_map[ticker] = []
        ticker_map[ticker].append(row)

    to_delete = []
    for ticker, entries in ticker_map.items():
        if len(entries) > 1:
            # Sort by run_date descending
            entries.sort(key=lambda x: x['run_date'], reverse=True)
            # Keep the most recent, delete the rest
            to_delete.extend([e['id'] for e in entries[1:]])

    if not to_delete:
        print("No duplicates found (each ticker has only one entry).")
        return

    print(f"Found {len(to_delete)} duplicate entries to delete.")
    
    if dry_run:
        print("DRY RUN: No entries will be deleted. Run with --execute to perform the deletion.")
    else:
        print(f"Deleting {len(to_delete)} entries...")
        for i in range(0, len(to_delete), 100):
            batch = to_delete[i:i+100]
            supabase.table("watchlist").delete().in_("id", batch).execute()
        print("Deduplication complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Delete duplicate ticker entries in watchlist, keeping only the most recent.")
    parser.add_argument("--execute", action="store_true", help="Actually perform the deletion")
    args = parser.parse_args()
    deduplicate_watchlist(dry_run=not args.execute)
