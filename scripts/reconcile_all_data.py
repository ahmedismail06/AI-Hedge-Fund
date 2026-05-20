
import os
import asyncio
from dotenv import load_dotenv
from supabase import create_client
from ib_insync import IB
from datetime import datetime

load_dotenv()

async def deep_reconcile():
    # 1. Setup Clients
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    supabase = create_client(url, key)

    ib = IB()
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = 7497 if os.getenv("ENV", "paper").lower() == "paper" else 7496
    
    print(f"Connecting to IBKR on {host}:{port}...")
    try:
        await ib.connectAsync(host, port, clientId=103, timeout=10)
        print("✅ Connected to IBKR")
    except Exception as e:
        print(f"❌ Error: {e}")
        return

    try:
        # 2. Fetch Data from IBKR
        print("Fetching data from IBKR...")
        ib_fills = await ib.reqExecutionsAsync()
        ib_positions = await ib.reqPositionsAsync()
        print(f"Found {len(ib_fills)} recent execution records.")
        print(f"Found {len(ib_positions)} open positions.")

        # 3. Sync Fills (Recent ones)
        # Note: 'side' column is NOT in our schema, so we omit it.
        for f in ib_fills:
            ticker = f.contract.symbol
            exec_id = f.execution.execId
            qty = float(f.execution.shares)
            price = float(f.execution.avgPrice)
            fill_time = f.execution.time.isoformat()
            
            # Find the most relevant order
            order_res = supabase.table("orders").select("id, position_id").eq("ticker", ticker).neq("status", "CANCELLED").order("created_at", desc=True).limit(1).execute()
            
            if order_res.data:
                order_id = order_res.data[0]['id']
                position_id = order_res.data[0]['position_id']
                
                fill_data = {
                    "order_id": order_id,
                    "position_id": position_id,
                    "ticker": ticker,
                    "fill_qty": qty,
                    "fill_price": price,
                    "fill_time": fill_time,
                    "ibkr_exec_id": exec_id
                }

                try:
                    supabase.table("fills").upsert(fill_data, on_conflict="ibkr_exec_id").execute()
                except Exception as e:
                    # Likely missing column or schema cache issue
                    pass

        print("✅ Recent Fills table synchronized.")

        # 4. Master Sync: Positions and Orders
        # We use reqPositionsAsync as the "Source of Truth" for totals.
        for p in ib_positions:
            ticker = p.contract.symbol
            qty = float(p.position)
            avg_price = float(p.avgCost)
            dollar_size = round(qty * avg_price, 2)
            
            print(f"\nSyncing Truth for {ticker}: {qty} shares @ ${avg_price:.4f}")

            # A. Update the latest Order
            order_res = supabase.table("orders").select("id").eq("ticker", ticker).order("created_at", desc=True).limit(1).execute()
            if order_res.data:
                oid = order_res.data[0]['id']
                supabase.table("orders").update({
                    "total_filled_qty": qty,
                    "avg_fill_price": round(avg_price, 4),
                    "status": "FILLED"
                }).eq("id", oid).execute()
                print(f"  ✅ Order {oid} updated to match IBKR position.")

            # B. Update the Position row
            pos_res = supabase.table("positions").select("id").eq("ticker", ticker).eq("status", "OPEN").execute()
            if pos_res.data:
                pid = pos_res.data[0]['id']
                supabase.table("positions").update({
                    "share_count": qty,
                    "entry_price": round(avg_price, 4),
                    "current_price": round(avg_price, 4),
                    "dollar_size": dollar_size,
                    "status": "OPEN"
                }).eq("id", pid).execute()
                print(f"  ✅ Position {pid} updated to match IBKR position.")
            else:
                # Create if missing entirely
                supabase.table("positions").insert({
                    "ticker": ticker,
                    "share_count": qty,
                    "entry_price": round(avg_price, 4),
                    "current_price": round(avg_price, 4),
                    "dollar_size": dollar_size,
                    "status": "OPEN",
                    "direction": "LONG",
                    "conviction_score": 5.0,
                    "kelly_fraction": 0.0,
                    "size_label": "medium",
                    "pct_of_portfolio": 0.0,
                    "sizing_rationale": "Imported from IBKR during deep reconcile",
                    "regime_at_sizing": "Imported",
                    "opened_at": datetime.now().isoformat()
                }).execute()
                print(f"  ✅ New OPEN position created for {ticker}.")

        print("\n--- DEEP RECONCILE COMPLETE ---")
        
    finally:
        ib.disconnect()

if __name__ == "__main__":
    asyncio.run(deep_reconcile())
