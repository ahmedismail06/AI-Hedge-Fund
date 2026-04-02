"""
Risk Monitor — orchestrates one 60-second monitoring cycle.

Called by the APScheduler interval job in backend/main.py every 60 seconds.
The market-hours guard is handled here so the scheduler fires continuously
but cycles exit immediately when the market is closed.

Each cycle:
  1. Check market hours (9:30–16:00 ET, Mon–Fri) — return early if closed
  2. Fetch all OPEN positions from Supabase
  3. Refresh current prices via yfinance batch call
  4. Compute pnl_pct for each position against current price
  5. Run check_stops() → StopEvent list
  6. Run check_exposure_drift() → ExposureBreach list
  7. Build RiskAlert objects via build_alerts()
  8. Dispatch via dispatch_alerts() (Supabase + optional Slack)
  9. Return summary dict
"""

import logging
from datetime import datetime, time

import pytz
import yfinance as yf
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

    # ── 2. Refresh current prices ─────────────────────────────────────────────
    tickers = list({p["ticker"] for p in positions if p.get("ticker")})
    positions = _refresh_prices(positions, tickers)

    # ── 3. Check stops ────────────────────────────────────────────────────────
    stop_events = check_stops(positions, regime)

    # ── 4. Check exposure drift ───────────────────────────────────────────────
    exposure_breaches = check_exposure_drift(positions, regime)

    # ── 5. Build alerts ───────────────────────────────────────────────────────
    alerts = build_alerts(stop_events, exposure_breaches, regime)

    # ── 6. Dispatch ───────────────────────────────────────────────────────────
    if alerts:
        dispatch_alerts(alerts, supabase_client)

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

def _refresh_prices(positions: list[dict], tickers: list[str]) -> list[dict]:
    """
    Batch-fetch latest prices from yfinance and update pnl_pct on each position.
    Positions with no yfinance data are left unchanged (stale price).
    """
    if not tickers:
        return positions

    try:
        data = yf.download(
            tickers, period="1d", interval="1m",
            group_by="ticker", progress=False, auto_adjust=True
        )
    except Exception as exc:
        logger.warning("yfinance price refresh failed: %s", exc)
        return positions

    price_map: dict[str, float] = {}

    if len(tickers) == 1:
        ticker = tickers[0]
        try:
            close_col = data["Close"] if "Close" in data.columns else data.iloc[:, 0]
            last_price = float(close_col.dropna().iloc[-1])
            price_map[ticker] = last_price
        except Exception:
            pass
    else:
        for ticker in tickers:
            try:
                last_price = float(data[ticker]["Close"].dropna().iloc[-1])
                price_map[ticker] = last_price
            except Exception:
                pass

    updated = []
    for pos in positions:
        ticker = pos.get("ticker")
        if ticker and ticker in price_map:
            current_price = price_map[ticker]
            entry_price = pos.get("entry_price")
            pos = dict(pos)
            pos["current_price"] = current_price
            if entry_price:
                try:
                    ep = float(entry_price)
                    pos["pnl_pct"] = (current_price - ep) / ep if ep else 0.0
                except (TypeError, ValueError):
                    pass
        updated.append(pos)

    return updated
