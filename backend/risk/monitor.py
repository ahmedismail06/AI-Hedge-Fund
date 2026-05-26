"""
Risk Monitor — orchestrates one 60-second monitoring cycle.

Called by the APScheduler interval job in backend/main.py every 60 seconds.
The market-hours guard is handled here so the scheduler fires continuously
but cycles exit immediately when the market is closed.

Each cycle:
  1. Check market hours (9:30–16:00 ET, Mon–Fri) — return early if closed
  2. Fetch all OPEN positions from Supabase
  3. Refresh current prices via Polygon snapshot API
  4. Compute pnl_pct for each position against current price
  5. Run check_stops() → StopEvent list
  6. Run check_exposure_drift() → ExposureBreach list
  7. Build RiskAlert objects via build_alerts()
  8. Dispatch via dispatch_alerts() (Supabase + optional Slack)
  9. Return summary dict
"""

import logging
import os
from datetime import datetime, time, timezone

import pytz
import requests
from dotenv import load_dotenv

from backend.risk.alerts import build_alerts
from backend.risk.exposure_monitor import check_exposure_drift
from backend.risk.notifier import dispatch_alerts
from backend.risk.stop_loss import check_stops

load_dotenv()

logger = logging.getLogger(__name__)

_ET = pytz.timezone("America/New_York")
_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)
_MARKET_WEEKDAYS = {0, 1, 2, 3, 4}  # Mon–Fri


def write_heartbeat(supabase_client) -> bool:
    """
    Confirm Supabase connectivity by performing a lightweight read on risk_alerts.
    Logs success/failure without writing a row (avoids polluting the alerts table).
    Returns True if the table is reachable, False otherwise.
    """
    try:
        supabase_client.table("risk_alerts").select("id").limit(1).execute()
        logger.info("risk_alerts connectivity confirmed — risk monitor started")
        return True
    except Exception as exc:
        logger.error(
            "risk_alerts connectivity check FAILED: %s "
            "(check SUPABASE_URL/SUPABASE_KEY and that risk_alerts table exists)",
            exc, exc_info=True,
        )
        return False


def is_market_open() -> bool:
    """Return True if the US equity market is currently open."""
    now_et = datetime.now(_ET)
    if now_et.weekday() not in _MARKET_WEEKDAYS:
        return False
    current_time = now_et.time()
    return _MARKET_OPEN <= current_time < _MARKET_CLOSE


def run_monitor_cycle(supabase_client, regime: str, force: bool = False) -> dict:
    """
    Execute one 60-second risk monitoring cycle.

    Args:
        supabase_client: initialised supabase-py client
        regime:          current macro regime string (passed in by risk_agent.py)
        force:           if True, bypasses the market-hours guard (for manual/test runs)

    Returns:
        Summary dict: {positions_checked, alerts_fired, critical_count, skipped}
    """
    # ── 1. Fetch OPEN positions ───────────────────────────────────────────────
    resp = (
        supabase_client
        .table("positions")
        .select(
            "id,ticker,direction,entry_price,current_price,pnl_pct,"
            "pct_of_portfolio,stop_loss_price,sector"
        )
        .eq("status", "OPEN")
        .execute()
    )
    positions = resp.data or []

    if not positions:
        logger.debug("no OPEN positions — risk cycle done")
        return {"positions_checked": 0, "alerts_fired": 0, "critical_count": 0, "skipped": False}

    # ── 2. Refresh prices (always — keeps current_price fresh for PM + order builder) ──
    tickers = list({p["ticker"] for p in positions if p.get("ticker")})
    original_count = len(positions)
    positions_with_prices = _refresh_prices(positions, tickers, supabase_client)
    live_count = len(positions_with_prices)

    if live_count < original_count:
        logger.warning(
            "%d/%d position(s) had no price from any source this cycle",
            original_count - live_count,
            original_count,
        )

    # ── 3. Stop checks and alerts only during market hours ───────────────────
    # Price refresh above always runs so the PM and order builder see fresh data.
    # Stop checks outside market hours produce noise (bid/ask spreads widen,
    # pre-market prints are thin) so we skip them.
    if not force and not is_market_open():
        logger.debug(
            "market closed — prices refreshed for %d position(s), stop checks skipped",
            live_count,
        )
        return {"positions_checked": live_count, "alerts_fired": 0, "critical_count": 0, "skipped": True}

    if not positions_with_prices:
        logger.error(
            "no positions have live prices this cycle — stop checks skipped entirely "
            "(Polygon unavailable or all tickers returned no data)"
        )
        return {"positions_checked": 0, "alerts_fired": 0, "critical_count": 0, "skipped": False}

    stop_events = check_stops(positions_with_prices, regime)

    from backend.broker.ibkr import get_portfolio_value as _get_portfolio_value
    exposure_breaches = check_exposure_drift(positions_with_prices, regime, _get_portfolio_value())

    alerts = build_alerts(stop_events, exposure_breaches, regime)

    if alerts:
        logger.info("generated %d alert(s) — dispatching to Supabase", len(alerts))
        dispatch_alerts(alerts, supabase_client)
    else:
        logger.info(
            "risk cycle complete: %d positions checked, 0 alerts — all clear",
            len(positions_with_prices),
        )

    critical_count = sum(1 for a in alerts if a.tier == 3)
    logger.info(
        "risk cycle complete: %d positions, %d alerts (%d critical)",
        len(positions_with_prices), len(alerts), critical_count,
    )

    return {
        "positions_checked": len(positions_with_prices),
        "alerts_fired": len(alerts),
        "critical_count": critical_count,
        "skipped": False,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_prices_ibkr(tickers: list[str]) -> dict[str, float]:
    """
    Read real-time market prices from IBKR's portfolio cache.

    ib_insync keeps ib.portfolio() updated continuously for every position held —
    no extra market data subscription required. Only covers tickers we own.
    Returns {ticker: price}. Empty dict on any failure (caller falls back to Polygon).
    """
    try:
        from backend.broker.ibkr import connect as _ibkr_connect
    except ImportError:
        return {}

    try:
        ib = _ibkr_connect()
    except Exception as exc:
        logger.warning("IBKR not available for price fetch: %s", exc)
        return {}

    try:
        result: dict[str, float] = {}
        wanted = set(tickers)
        for item in ib.portfolio():
            symbol = item.contract.symbol if item.contract else None
            if symbol and symbol in wanted and item.marketPrice and item.marketPrice > 0:
                result[symbol] = float(item.marketPrice)
                logger.debug("IBKR portfolio price: %s = $%.4f", symbol, item.marketPrice)
        return result
    except Exception as exc:
        logger.warning("IBKR portfolio price read failed: %s", exc)
        return {}


def _fetch_prices_polygon(tickers: list[str]) -> dict[str, float]:
    """
    Batch-fetch prices from Polygon snapshot API.
    Returns {ticker: price}. Empty dict on failure.
    Note: free-tier Polygon returns 15-min delayed lastTrade; day.c is previous close.
    """
    polygon_key = os.getenv("POLYGON_API_KEY")
    if not polygon_key:
        logger.error("POLYGON_API_KEY not set — cannot fetch Polygon prices")
        return {}

    try:
        resp = requests.get(
            "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
            params={"tickers": ",".join(tickers), "apiKey": polygon_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("Polygon price fetch failed: %s", exc)
        return {}

    price_map: dict[str, float] = {}
    for item in data.get("tickers", []):
        ticker = item.get("ticker")
        last_trade = (item.get("lastTrade") or {}).get("p")
        day_close = (item.get("day") or {}).get("c")
        price = last_trade or day_close
        if ticker and price:
            price_map[ticker] = float(price)
            logger.debug(
                "Polygon price: %s = $%.4f (source=%s)",
                ticker, float(price), "lastTrade" if last_trade else "day.close",
            )
        elif ticker:
            logger.warning("Polygon returned %s but no price fields", ticker)

    return price_map


def _refresh_prices(
    positions: list[dict], tickers: list[str], supabase_client=None
) -> list[dict]:
    """
    Fetch fresh prices and persist them to Supabase.

    Strategy:
      - Market hours: IBKR first (real-time), Polygon as fallback per-ticker.
      - Outside market hours: Polygon only; skip DB write if price is unchanged
        (avoids noisy writes when market is closed and prices don't move).

    Returns ONLY positions that received a fresh price. Stop checks must never
    run against a stale DB value, so positions with no price are excluded.
    """
    if not tickers:
        return positions

    market_open = is_market_open()
    price_map: dict[str, float] = {}

    if market_open:
        # Try IBKR first — real-time, no API tier limitations.
        price_map = _fetch_prices_ibkr(tickers)
        missing = [t for t in tickers if t not in price_map]
        if missing:
            logger.info(
                "IBKR missing %d ticker(s) — falling back to Polygon: %s",
                len(missing), ", ".join(missing),
            )
            polygon_prices = _fetch_prices_polygon(missing)
            price_map.update(polygon_prices)
    else:
        # Outside market hours: IBKR holds last-close prices for owned positions;
        # Polygon day.c is the secondary source; stored current_price is the final
        # fallback (prices don't move overnight so the DB value is always valid).
        price_map = _fetch_prices_ibkr(tickers)
        missing = [t for t in tickers if t not in price_map]
        if missing:
            polygon_prices = _fetch_prices_polygon(missing)
            price_map.update(polygon_prices)
        for pos in positions:
            t = pos.get("ticker")
            if t and t not in price_map and pos.get("current_price"):
                price_map[t] = float(pos["current_price"])
                logger.debug("%s: no live price — using stored DB price $%.4f", t, price_map[t])

    if not price_map:
        logger.error("no prices obtained from any source — all positions excluded this cycle")
        return []

    # Warn for tickers with no price from any source.
    for m in sorted(set(tickers) - set(price_map)):
        logger.warning("no price available for %s from IBKR or Polygon", m)

    updated: list[dict] = []
    for pos in positions:
        ticker = pos.get("ticker")
        if not ticker or ticker not in price_map:
            continue

        live_price = price_map[ticker]
        db_price = float(pos.get("current_price") or 0)
        entry_price = pos.get("entry_price")

        pos = dict(pos)
        pos["current_price"] = live_price

        if entry_price:
            try:
                ep = float(entry_price)
                direction = str(pos.get("direction", "LONG")).upper()
                if direction == "SHORT":
                    pnl = (ep - live_price) / ep if ep else 0.0
                else:
                    pnl = (live_price - ep) / ep if ep else 0.0
                pos["pnl_pct"] = pnl
                logger.debug(
                    "pnl: %s %s entry=$%.4f live=$%.4f pnl_pct=%.2f%%",
                    ticker, direction, ep, live_price, pnl * 100,
                )
            except (TypeError, ValueError) as exc:
                logger.warning("pnl_pct computation failed for %s: %s", ticker, exc)

        if supabase_client:
            pos_id = pos.get("id")
            # Outside market hours: skip write if price is unchanged (< $0.01 diff).
            price_changed = abs(live_price - db_price) >= 0.01
            if not market_open and not price_changed:
                logger.debug("%s price unchanged at $%.4f — skipping DB write", ticker, live_price)
            elif pos_id:
                try:
                    supabase_client.table("positions").update({
                        "current_price": round(live_price, 4),
                        "pnl_pct": round(float(pos.get("pnl_pct") or 0), 6),
                    }).eq("id", pos_id).execute()
                except Exception as exc:
                    logger.warning("failed to persist price for %s: %s", ticker, exc)

        updated.append(pos)

    return updated
