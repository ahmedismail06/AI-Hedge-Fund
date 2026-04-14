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
import uuid
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
    Write a SYSTEM heartbeat row to risk_alerts to confirm table connectivity.
    Call once at startup (from risk_agent.py lifespan or first cycle).
    Returns True if write succeeded, False otherwise.
    """
    row = {
        "id": str(uuid.uuid4()),
        "ticker": None,
        "tier": 1,
        "severity": "WARN",
        "trigger": "SYSTEM heartbeat — risk monitor started, Supabase connectivity confirmed",
        "regime": "SYSTEM",
        "resolved": True,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resp = supabase_client.table("risk_alerts").insert(row).execute()
        logger.info(
            "risk_alerts heartbeat written OK (id=%s, rows_returned=%d)",
            row["id"], len(resp.data or []),
        )
        return True
    except Exception as exc:
        logger.error(
            "risk_alerts heartbeat FAILED — Supabase write error: %s "
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
    if not force and not is_market_open():
        logger.debug("market closed — skipping risk monitor cycle")
        return {"positions_checked": 0, "alerts_fired": 0, "critical_count": 0, "skipped": True}

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

    # ── 2. Fetch live prices and drop any position without a fresh quote ────────
    tickers = list({p["ticker"] for p in positions if p.get("ticker")})
    original_count = len(positions)
    positions = _refresh_prices(positions, tickers, supabase_client)
    live_count = len(positions)

    if live_count < original_count:
        logger.warning(
            "%d/%d position(s) excluded from stop checks — no live Polygon price available",
            original_count - live_count,
            original_count,
        )

    if not positions:
        logger.error(
            "no positions have live prices this cycle — stop checks skipped entirely "
            "(Polygon unavailable or all tickers returned no data)"
        )
        return {"positions_checked": 0, "alerts_fired": 0, "critical_count": 0, "skipped": False}

    # ── 3. Check stops ────────────────────────────────────────────────────────
    stop_events = check_stops(positions, regime)

    # ── 4. Check exposure drift ───────────────────────────────────────────────
    from backend.broker.ibkr import get_portfolio_value as _get_portfolio_value
    exposure_breaches = check_exposure_drift(positions, regime, _get_portfolio_value())

    # ── 5. Build alerts ───────────────────────────────────────────────────────
    alerts = build_alerts(stop_events, exposure_breaches, regime)

    # ── 6. Dispatch ───────────────────────────────────────────────────────────
    if alerts:
        logger.info("generated %d alert(s) — dispatching to Supabase", len(alerts))
        dispatch_alerts(alerts, supabase_client)
    else:
        logger.info(
            "risk cycle complete: %d positions checked, 0 alerts — all clear "
            "(stop checks ran, no thresholds breached, no approaching-stop warnings)",
            len(positions),
        )

    critical_count = sum(1 for a in alerts if a.tier == 3)
    logger.info(
        "risk cycle complete: %d positions, %d alerts (%d critical)",
        len(positions), len(alerts), critical_count,
    )

    return {
        "positions_checked": len(positions),
        "alerts_fired": len(alerts),
        "critical_count": critical_count,
        "skipped": False,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _refresh_prices(
    positions: list[dict], tickers: list[str], supabase_client=None
) -> list[dict]:
    """
    Batch-fetch live prices from Polygon for all tickers in a single call,
    update pnl_pct in memory, and persist current_price + pnl_pct to Supabase.

    Returns ONLY positions for which a live price was successfully obtained.
    Positions with no Polygon data are excluded — stop checks must never run
    against a stale DB value.

    On total Polygon failure (network error, bad key, non-200) returns [] so
    the caller can detect that no live data is available this cycle.

    Uses /v2/snapshot/locale/us/markets/stocks/tickers (batch, one call).
    Prefers lastTrade.p (real-time during market hours), falls back to day.c.
    """
    if not tickers:
        return positions

    polygon_key = os.getenv("POLYGON_API_KEY")
    if not polygon_key:
        logger.error(
            "POLYGON_API_KEY not set — cannot fetch live prices; "
            "all %d position(s) excluded from stop checks this cycle",
            len(positions),
        )
        return []

    logger.info("fetching live Polygon prices for: %s", ", ".join(sorted(tickers)))
    try:
        resp = requests.get(
            "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
            params={"tickers": ",".join(tickers), "apiKey": polygon_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            "Polygon snapshot OK: status=%s, tickers_returned=%d/%d",
            data.get("status"), len(data.get("tickers", [])), len(tickers),
        )
    except Exception as exc:
        logger.error(
            "Polygon price fetch FAILED — all %d position(s) excluded from stop checks: %s",
            len(positions), exc,
        )
        return []

    # ── Build price map from Polygon response ─────────────────────────────────
    price_map: dict[str, float] = {}
    for item in data.get("tickers", []):
        ticker = item.get("ticker")
        last_trade = (item.get("lastTrade") or {}).get("p")
        day_close = (item.get("day") or {}).get("c")
        live_price = last_trade or day_close
        if ticker and live_price:
            price_map[ticker] = float(live_price)
            logger.info(
                "live price: %s = $%.4f (source=%s)",
                ticker, float(live_price), "lastTrade" if last_trade else "day.close",
            )
        elif ticker:
            logger.warning(
                "Polygon returned %s but no lastTrade.p or day.c — "
                "excluding from stop checks this cycle",
                ticker,
            )

    # Warn for any requested ticker that wasn't in the Polygon response at all.
    missing = set(tickers) - set(price_map)
    for m in sorted(missing):
        logger.warning(
            "no Polygon data for %s — excluding from stop checks this cycle "
            "(stale DB price will NOT be used)",
            m,
        )

    # ── Build output: only positions with a fresh live price ──────────────────
    updated: list[dict] = []
    for pos in positions:
        ticker = pos.get("ticker")
        if not ticker or ticker not in price_map:
            continue  # excluded — logged above

        live_price = price_map[ticker]
        entry_price = pos.get("entry_price")
        pos = dict(pos)
        pos["current_price"] = live_price

        if entry_price:
            try:
                ep = float(entry_price)
                pnl = (live_price - ep) / ep if ep else 0.0
                pos["pnl_pct"] = pnl
                logger.info(
                    "pnl: %s entry=$%.4f live=$%.4f pnl_pct=%.2f%%",
                    ticker, ep, live_price, pnl * 100,
                )
            except (TypeError, ValueError) as exc:
                logger.warning("pnl_pct computation failed for %s: %s", ticker, exc)

        # Persist live price back to Supabase so the dashboard and other agents
        # see fresh data without needing their own Polygon call.
        if supabase_client:
            pos_id = pos.get("id")
            if pos_id:
                try:
                    supabase_client.table("positions").update({
                        "current_price": round(live_price, 4),
                        "pnl_pct": round(float(pos.get("pnl_pct") or 0), 6),
                    }).eq("id", pos_id).execute()
                except Exception as _persist_exc:
                    logger.warning(
                        "failed to persist live price for %s to Supabase: %s",
                        ticker, _persist_exc,
                    )
            else:
                logger.debug(
                    "position dict for %s missing 'id' — skipping Supabase persist", ticker
                )

        updated.append(pos)

    return updated
