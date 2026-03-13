# Execution Agent
# Translates human-approved sizing recommendations into IBKR orders
# Order type selection: limit (<1% ADV), VWAP (1-5% ADV), day VWAP (>5% ADV)
# Manages order lifecycle: placement, monitoring, fills, cancellations
# Single code path; ENV flag switches between paper (7497) and live (7496)
