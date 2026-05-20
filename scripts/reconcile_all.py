
import os
import asyncio
from dotenv import load_dotenv
from supabase import create_client
from ib_insync import IB

load_dotenv()

async def reconcile():
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
        await ib.connectAsync(host, port, clientId=101, timeout=10)
        print("✅ Connected to IBKR")
    except Exception as e:
        print(f"❌ Error: Could not connect to IBKR: {e}")
        return

    try:
        # 3. Fetch IBKR Data
        ib_positions = await ib.reqPositionsAsync()
        ib_summary = await ib.accountSummaryAsync()
        
        ib_cash = 0.0
        for s in ib_summary:
            if s.tag == 'CashBalance' and s.currency == 'USD':
                ib_cash = float(s.value)

        # 4. Fetch Supabase Data
        sb_config = supabase.table("pm_config").select("cash_balance").eq("id", 1).execute().data[0]
        sb_cash = float(sb_config['cash_balance'])
        sb_positions = supabase.table("positions").select("*").eq("status", "OPEN").execute().data

        # 5. Compare Cash
        print("\n" + "="*80)
        print(f"{'CASH RECONCILIATION':^80}")
        print("-" * 80)
        print(f"IBKR Cash:     ${ib_cash:,.2f}")
        print(f"Supabase Cash: ${sb_cash:,.2f}")
        print(f"Difference:    ${ib_cash - sb_cash:,.2f} {'✅ MATCH' if abs(ib_cash - sb_cash) < 1 else '❌ MISMATCH'}")

        # 6. Compare Positions
        ib_map = {p.contract.symbol: p.position for p in ib_positions}
        sb_map = {p['ticker']: p['share_count'] for p in sb_positions}
        all_tickers = sorted(set(list(ib_map.keys()) + list(sb_map.keys())))

        print("\n" + "="*80)
        print(f"{'TICKER':<10} | {'IBKR QTY':<15} | {'SUPABASE QTY':<15} | {'STATUS'}")
        print("-" * 80)

        for ticker in all_tickers:
            ib_qty = ib_map.get(ticker, 0)
            sb_qty = sb_map.get(ticker, 0)
            
            status = "✅ MATCH" if ib_qty == sb_qty else "❌ MISMATCH"
            if ib_qty > 0 and sb_qty == 0:
                status = "⚠️ IBKR ONLY"
            elif sb_qty > 0 and ib_qty == 0:
                status = "⚠️ DB ONLY"

            print(f"{ticker:<10} | {ib_qty:<15.0f} | {sb_qty:<15.0f} | {status}")

        print("="*80 + "\n")
    
    finally:
        ib.disconnect()

if __name__ == "__main__":
    asyncio.run(reconcile())
