"""Flatten the unintended short positions using market orders."""
from dotenv import load_dotenv; load_dotenv()
from ib_insync import IB, Stock, MarketOrder

ib = IB()
ib.connect("127.0.0.1", 7497, clientId=99)

shorts = [
    ("HRMY", "NASDAQ", 5291),
    ("NRDS", "NASDAQ", 8120),
    ("PAR",  "NYSE",   5760),
]

for sym, exch, qty in shorts:
    contract = Stock(sym, "SMART", "USD")
    ib.qualifyContracts(contract)
    order = MarketOrder("BUY", qty)
    trade = ib.placeOrder(contract, order)
    print(f"BUY {qty} {sym} MARKET — orderId={trade.order.orderId}")

ib.sleep(5)
for trade in ib.trades():
    print(f"{trade.contract.symbol}: status={trade.orderStatus.status} filled={trade.orderStatus.filled} avgPrice={trade.orderStatus.avgFillPrice}")

ib.disconnect()
