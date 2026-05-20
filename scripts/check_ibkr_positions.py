"""
Quick check: print live IBKR positions vs Supabase share_count.
Run: python scripts/check_ibkr_positions.py
"""
from dotenv import load_dotenv
load_dotenv()

from ib_insync import IB
from backend.memory.vector_store import _get_client

ib = IB()
ib.connect("127.0.0.1", 7497, clientId=99)  # clientId=99 avoids colliding with live agent (2)

print("\n=== IBKR Positions ===")
ibkr = {}
for p in ib.portfolio():
    sym = p.contract.symbol
    ibkr[sym] = {"shares": p.position, "avg_cost": p.averageCost, "market_price": p.marketPrice, "market_value": p.marketValue, "unrealized_pnl": p.unrealizedPNL}
    print(f"  {sym:6s}  shares={p.position:>8.0f}  avg_cost=${p.averageCost:.4f}  price=${p.marketPrice:.4f}  unreal_pnl=${p.unrealizedPNL:+.2f}")

print("\n=== Supabase OPEN Positions ===")
client = _get_client()
rows = client.table("positions").select("ticker,share_count,entry_price,current_price,pnl_pct,dollar_size").eq("status", "OPEN").execute().data or []
db = {}
for r in rows:
    sym = r["ticker"]
    db[sym] = r
    print(f"  {sym:6s}  share_count={float(r.get('share_count') or 0):>8.0f}  entry=${float(r.get('entry_price') or 0):.4f}  pnl={float(r.get('pnl_pct') or 0):+.2f}%")

print("\n=== Delta (IBKR - DB) ===")
for sym in set(list(ibkr.keys()) + list(db.keys())):
    ibkr_shares = ibkr.get(sym, {}).get("shares", 0)
    db_shares = float(db.get(sym, {}).get("share_count") or 0)
    delta = ibkr_shares - db_shares
    flag = " *** DESYNCED" if abs(delta) > 0 else ""
    print(f"  {sym:6s}  ibkr={ibkr_shares:>8.0f}  db={db_shares:>8.0f}  delta={delta:>+8.0f}{flag}")

print("\n=== Cash ===")
summary = {v.tag: v.value for v in ib.accountSummary() if v.currency in ("USD", "")}
ibkr_cash = float(summary.get("CashBalance") or 0)
ibkr_nav  = float(summary.get("NetLiquidation") or 0)
db_cash   = float((client.table("pm_config").select("cash_balance").limit(1).execute().data or [{}])[0].get("cash_balance") or 0)
print(f"  IBKR CashBalance:    ${ibkr_cash:>14,.2f}")
print(f"  IBKR NetLiquidation: ${ibkr_nav:>14,.2f}")
print(f"  DB   cash_balance:   ${db_cash:>14,.2f}")
delta = ibkr_cash - db_cash
flag = " *** DESYNCED" if abs(delta) > 1 else ""
print(f"  Delta:               ${delta:>+14,.2f}{flag}")

ib.disconnect()

 