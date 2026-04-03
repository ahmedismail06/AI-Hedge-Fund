"""
Order Builder — ADV-based order type selection.

Converts an APPROVED positions table row into a validated (OrderRequest, Contract, Order)
triple ready for submission via order_manager.place_order().
"""

import logging
from decimal import Decimal, ROUND_CEILING

from dotenv import load_dotenv

load_dotenv()

import yfinance as yf
from ib_insync import LimitOrder, Stock

from backend.broker.schemas import OrderRequest

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Custom exception
# ──────────────────────────────────────────────────────────────────────────────


class OrderBuildError(Exception):
    pass


# ──────────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────────


def _fetch_adv(ticker: str) -> float:
    """Fetch 20-day average daily volume via yfinance. Returns 0.0 on any failure."""
    try:
        hist = yf.Ticker(ticker).history(period="30d")
        if hist.empty or len(hist) < 5:
            return 0.0
        return float(hist["Volume"].tail(20).mean())
    except Exception as exc:
        logger.warning("_fetch_adv failed for %s: %s — defaulting to 0.0", ticker, exc)
        return 0.0


def _select_order_type(share_count: int, adv: float) -> tuple:
    """
    Return (order_type, timeout_minutes) based on share_count relative to ADV.

    Rules (domain-rules.md):
      < 1% ADV   → LIMIT,    10 min
      1–5% ADV   → VWAP_30,  30 min
      > 5% ADV   → VWAP_DAY, 390 min (full session)
    """
    if adv <= 0:
        return ("LIMIT", 10)
    ratio = share_count / adv
    if ratio < 0.01:
        return ("LIMIT", 10)
    if ratio <= 0.05:
        return ("VWAP_30", 30)
    return ("VWAP_DAY", 390)


def _round_up_to_tick(price: float, tick: float = 0.01) -> float:
    """
    Round price UP to the next valid tick.

    IBKR rejects prices that don't conform to the contract's min tick.
    Defaulting to $0.01 works for most US equities.
    """
    p = Decimal(str(price))
    t = Decimal(str(tick))
    return float((p / t).to_integral_value(rounding=ROUND_CEILING) * t)


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────


def build_order(position_row: dict) -> tuple:
    """
    Build a (OrderRequest, Contract, Order) triple from an APPROVED positions row.

    Args:
        position_row: Dict from the Supabase `positions` table with at minimum
                      keys: id, ticker, direction, share_count, entry_price.

    Returns:
        Tuple of (OrderRequest, ib_insync.Stock, ib_insync.LimitOrder).

    Raises:
        OrderBuildError: if required fields are missing, None, or direction != LONG.
    """
    # 1. Validate required fields
    required = ("id", "ticker", "direction", "share_count", "entry_price")
    for field in required:
        if position_row.get(field) is None:
            raise OrderBuildError(f"position_row missing required field: '{field}'")

    ticker: str = str(position_row["ticker"])
    direction: str = str(position_row["direction"])
    share_count: int = int(position_row["share_count"])
    entry_price: float = float(position_row["entry_price"])

    if direction != "LONG":
        raise OrderBuildError(
            f"direction '{direction}' not supported — Phase 1 is long-only (SHORT deferred to Phase 2)"
        )

    # 2. Fetch ADV
    adv = _fetch_adv(ticker)

    # 3. Select order type
    order_type, timeout_minutes = _select_order_type(share_count, adv)

    logger.info(
        "Order for %s: %s (%d shares, ADV=%.0f)",
        ticker,
        order_type,
        share_count,
        adv,
    )

    # 4. Build OrderRequest
    limit_price_value = (
        _round_up_to_tick(entry_price * 1.001) if order_type == "LIMIT" else None
    )
    req = OrderRequest(
        position_id=str(position_row["id"]),
        ticker=ticker,
        direction="LONG",
        order_type=order_type,
        requested_qty=share_count,
        limit_price=limit_price_value,
        intended_price=entry_price,
        timeout_minutes=timeout_minutes,
    )

    # 5. Build ib_insync Contract
    contract = Stock(ticker, "SMART", "USD")

    # 6. Build ib_insync Order
    if order_type == "LIMIT":
        order = LimitOrder("BUY", share_count, _round_up_to_tick(entry_price * 1.001))
    else:
        # VWAP algo requires live IBKR algo permissions; using limit approximation
        order = LimitOrder("BUY", share_count, _round_up_to_tick(entry_price * 1.005))

    # Ensure TIF matches the gateway preset to avoid IBKR canceling the order.
    order.tif = "DAY"

    # 7. Return triple
    return (req, contract, order)
