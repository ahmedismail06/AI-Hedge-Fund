# Stop Loss Engine
# Enforces 3-tier stop structure:
#   Tier 1 — Position stop (per-trade max loss)
#   Tier 2 — Strategy stop (sector / factor group drawdown)
#   Tier 3 — Portfolio stop (total portfolio drawdown)
# Thresholds automatically tighten in Risk-Off regime
