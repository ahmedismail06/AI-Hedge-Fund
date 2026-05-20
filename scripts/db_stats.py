
import os
from dotenv import load_dotenv
from supabase import create_client
from tabulate import tabulate

load_dotenv()

def get_stats():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    supabase = create_client(url, key)

    print("\n" + "="*80)
    print(f"{'DATABASE PORTFOLIO SUMMARY':^80}")
    print("="*80)

    # 1. Cash Balance
    config = supabase.table("pm_config").select("cash_balance").eq("id", 1).execute().data[0]
    cash = float(config['cash_balance'])
    print(f"\nCASH BALANCE: ${cash:,.2f}")

    # 2. Open Positions
    res_pos = supabase.table("positions").select("*").eq("status", "OPEN").execute()
    if res_pos.data:
        print("\nOPEN POSITIONS:")
        headers = ["Ticker", "Shares", "Entry", "Current", "Value", "PnL %", "% Port"]
        table = []
        total_market_value = 0
        for p in res_pos.data:
            val = float(p['dollar_size'])
            total_market_value += val
            table.append([
                p['ticker'],
                f"{float(p['share_count']):,.0f}",
                f"${float(p['entry_price']):.2f}",
                f"${float(p['current_price'] or 0):.2f}",
                f"${val:,.2f}",
                f"{float(p['pnl_pct'] or 0):.2f}%",
                f"{float(p['pct_of_portfolio']):.2f}%"
            ])
        print(tabulate(table, headers=headers, tablefmt="simple"))
        print(f"\nTOTAL MARKET VALUE: ${total_market_value:,.2f}")
        print(f"TOTAL NET ASSET VALUE: ${cash + total_market_value:,.2f}")
    else:
        print("\nNo open positions found.")

    # 3. Recent Orders (Last 5)
    res_orders = supabase.table("orders").select("*").order("created_at", desc=True).limit(5).execute()
    if res_orders.data:
        print("\nRECENT ORDERS (Last 5):")
        headers = ["Ticker", "Side", "Status", "Filled / Req", "Avg Price", "Created"]
        table = []
        for o in res_orders.data:
            table.append([
                o['ticker'],
                o['direction'],
                o['status'],
                f"{float(o['total_filled_qty']):.0f} / {float(o['requested_qty']):.0f}",
                f"${float(o['avg_fill_price'] or 0):.2f}",
                o['created_at'][:16].replace('T', ' ')
            ])
        print(tabulate(table, headers=headers, tablefmt="simple"))

    # 4. Fills Count
    res_fills = supabase.table("fills").select("id", count="exact").execute()
    print(f"\nTOTAL FILL RECORDS: {res_fills.count}")
    print("="*80 + "\n")

if __name__ == "__main__":
    get_stats()
