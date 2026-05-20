import os
from datetime import date, timedelta
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

QUALIFY_THRESHOLD = 6.5
TOP_N = 5
TODAY = date.today().isoformat()
MEMO_CUTOFF = (date.today() - timedelta(days=7)).isoformat()


def main():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    client = create_client(url, key)

    # Step 1: dequeue everything for today
    dequeue = (
        client.table("watchlist")
        .update({"queued_for_research": False})
        .eq("run_date", TODAY)
        .eq("queued_for_research", True)
        .execute()
    )
    print(f"Dequeued today's watchlist rows (run_date={TODAY})")

    # Step 2: fetch today's qualifying rows, sorted best score first
    result = (
        client.table("watchlist")
        .select("ticker,composite_score,material_event")
        .eq("run_date", TODAY)
        .gte("composite_score", QUALIFY_THRESHOLD)
        .order("composite_score", desc=True)
        .execute()
    )
    rows = result.data or []
    print(f"\nFound {len(rows)} tickers scoring ≥ {QUALIFY_THRESHOLD} today:")
    for r in rows:
        print(f"  {r['ticker']:8s}  {r['composite_score']:.3f}")

    # Step 3: filter out recently researched (memo < 7 days) unless material event
    eligible = []
    skipped = []
    for row in rows:
        ticker = row["ticker"]

        deferred = client.table("memos").select("id").eq("ticker", ticker).eq("status", "DEFERRED").limit(1).execute()
        if deferred.data:
            skipped.append((ticker, "DEFERRED memo"))
            continue

        recent_memo = client.table("memos").select("id").eq("ticker", ticker).gte("date", MEMO_CUTOFF).limit(1).execute()
        if recent_memo.data and not row.get("material_event", False):
            skipped.append((ticker, "memo < 7 days"))
            continue

        eligible.append(ticker)

    if skipped:
        print(f"\nSkipped:")
        for ticker, reason in skipped:
            print(f"  {ticker:8s}  ({reason})")

    to_queue = eligible[:TOP_N]

    if not to_queue:
        print("\nNo eligible tickers to queue.")
        return

    # Step 4: set queued_for_research=True on today's rows
    client.table("watchlist").update({"queued_for_research": True}).in_(
        "ticker", to_queue
    ).eq("run_date", TODAY).execute()

    print(f"\nQueued for research (top {TOP_N}):")
    for t in to_queue:
        score = next(r["composite_score"] for r in rows if r["ticker"] == t)
        print(f"  {t:8s}  {score:.3f}")


if __name__ == "__main__":
    main()
