
import os
import asyncio
from dotenv import load_dotenv
from supabase import create_client
from ib_insync import IB

load_dotenv()

async def sync_to_ibkr():
    # 1. Connect to Supabase
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    supabase = create_client(url, key)

    # 2. Connect to IBKR
    ib = IB()
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = 7497 if os.getenv("ENV", "paper").lower() == "paper" else 7496
    
    print(f"Connecting to IBKR on {host}:{port}...")
    try:
        await ib.connectAsync(host, port, clientId=102, timeout=10)
        print("✅ Connected to IBKR")
    except Exception as e:
        print(f"❌ Error: Could not connect to IBKR: {e}")
        return

    try:
        # 3. Fetch IBKR Positions
        ib_positions = await ib.reqPositionsAsync()
        
        print("\n--- Synchronizing Supabase to IBKR Truth ---")
        
        for p in ib_positions:
            ticker = p.contract.symbol
            qty = float(p.position)
            avg_cost = float(p.avgCost)
            dollar_size = round(qty * avg_cost, 2)
            
            print(f"Processing {ticker}: {qty} shares @ ${avg_cost:.4f}")
            
            # Check if position exists in DB
            res = supabase.table("positions").select("id").eq("ticker", ticker).eq("status", "OPEN").execute()
            
            if res.data:
                pos_id = res.data[0]['id']
                print(f"  -> Updating existing OPEN position {pos_id}")
                supabase.table("positions").update({
                    "share_count": qty,
                    "entry_price": round(avg_cost, 4),
                    "current_price": round(avg_cost, 4),
                    "dollar_size": dollar_size,
                    "pnl": 0.0,
                    "pnl_pct": 0.0
                }).eq("id", pos_id).execute()
            else:
                print(f"  -> No OPEN position found for {ticker}. Searching for APPROVED/PENDING...")
                # Try to find any row to take over
                res_any = supabase.table("positions").select("id").eq("ticker", ticker).order("created_at", desc=True).limit(1).execute()
                if res_any.data:
                    pos_id = res_any.data[0]['id']
                    print(f"  -> Promoting position {pos_id} to OPEN")
                    supabase.table("positions").update({
                        "status": "OPEN",
                        "share_count": qty,
                        "entry_price": round(avg_cost, 4),
                        "current_price": round(avg_cost, 4),
                        "dollar_size": dollar_size,
                        "opened_at": "2026-04-20T14:00:00Z" # Approx
                    }).eq("id", pos_id).execute()
                else:
                    print(f"  !! WARNING: No record of {ticker} found in positions table at all.")

            # Update Orders table to FILLED for this ticker to stop retries
            supabase.table("orders").update({
                "status": "FILLED",
                "total_filled_qty": qty,
                "avg_fill_price": round(avg_cost, 4)
            }).eq("ticker", ticker).in_("status", ["SUBMITTED", "PARTIAL", "PARTIAL_FILLED", "TIMEOUT"]).execute()

        print("\n✅ Sync Complete. DB now matches IBKR.")
        
    finally:
        ib.disconnect()

if __name__ == "__main__":
    asyncio.run(sync_to_ibkr())
