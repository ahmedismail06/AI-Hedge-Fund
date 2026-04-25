"""
Post-earnings drift-hold lifecycle manager.

A drift-hold window is activated when a ticker beats consensus EPS by > 5%.
During this window the stop-loss module uses normal (non-tightened) thresholds
even in Risk-Off regime, allowing post-earnings momentum to run for 45 days.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from backend.earnings_alpha.schemas import DriftHoldState

logger = logging.getLogger(__name__)

_DRIFT_HOLD_DAYS = 45
_SURPRISE_THRESHOLD = 0.05  # 5% positive surprise required


from backend.db.utils import get_supabase_client


def _get_client():
    return get_supabase_client()


def get_active_drift_hold(ticker: str) -> DriftHoldState:
    """
    Return the current drift-hold state for a ticker.

    Queries earnings_events for the most recent row where drift_hold_active=True
    and drift_hold_until >= today.
    """
    today_str = date.today().isoformat()
    try:
        resp = (
            _get_client()
            .table("earnings_events")
            .select("drift_hold_active, drift_hold_until, surprise_pct")
            .eq("ticker", ticker.upper())
            .eq("drift_hold_active", True)
            .gte("drift_hold_until", today_str)
            .order("event_date", desc=True)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return DriftHoldState(active=False)

        row = resp.data[0]
        hold_until = row["drift_hold_until"]
        hold_until_date = date.fromisoformat(hold_until)
        days_remaining = (hold_until_date - date.today()).days

        return DriftHoldState(
            active=True,
            surprise_pct=row.get("surprise_pct"),
            hold_until=hold_until,
            hold_days_remaining=max(0, days_remaining),
        )
    except Exception as exc:
        logger.warning("get_active_drift_hold(%s): %s", ticker, exc)
        return DriftHoldState(active=False)


def activate_drift_hold(ticker: str, surprise_pct: float, event_date: str) -> Optional[str]:
    """
    Activate a 45-day drift-hold window if surprise_pct > 5%.

    Updates the earnings_events row for (ticker, event_date) and also writes
    drift_hold_until to the matching OPEN positions row.

    Returns the hold_until ISO date string, or None if threshold not met.
    """
    if surprise_pct <= _SURPRISE_THRESHOLD:
        logger.debug(
            "activate_drift_hold(%s): surprise %.2f%% below threshold %.0f%% — no hold",
            ticker, surprise_pct * 100, _SURPRISE_THRESHOLD * 100,
        )
        return None

    hold_until = (date.fromisoformat(event_date) + timedelta(days=_DRIFT_HOLD_DAYS)).isoformat()

    try:
        client = _get_client()

        client.table("earnings_events").update({
            "drift_hold_active": True,
            "drift_hold_until": hold_until,
        }).eq("ticker", ticker.upper()).eq("event_date", event_date).execute()

        # Propagate to open positions so stop_loss.py can read it directly
        client.table("positions").update({
            "drift_hold_until": hold_until,
        }).eq("ticker", ticker.upper()).eq("status", "OPEN").execute()

        logger.info(
            "activate_drift_hold(%s): surprise +%.1f%% → hold until %s",
            ticker, surprise_pct * 100, hold_until,
        )
    except Exception as exc:
        logger.error("activate_drift_hold(%s): DB update failed — %s", ticker, exc)

    return hold_until


def expire_stale_holds() -> None:
    """
    Clear drift_hold_active flag for any earnings_events rows where
    drift_hold_until < today, and reset drift_hold_until on positions.

    Safe to call on every research run.
    """
    today_str = date.today().isoformat()
    try:
        client = _get_client()

        # Find stale hold rows to get tickers before clearing
        stale = (
            client.table("earnings_events")
            .select("ticker")
            .eq("drift_hold_active", True)
            .lt("drift_hold_until", today_str)
            .execute()
        )
        stale_tickers = list({r["ticker"] for r in (stale.data or [])})

        if stale_tickers:
            client.table("earnings_events").update({
                "drift_hold_active": False,
            }).eq("drift_hold_active", True).lt("drift_hold_until", today_str).execute()

            # Clear on positions for expired tickers
            for ticker in stale_tickers:
                client.table("positions").update({
                    "drift_hold_until": None,
                }).eq("ticker", ticker).eq("status", "OPEN").lt(
                    "drift_hold_until", today_str
                ).execute()

            logger.info("expire_stale_holds: expired %d tickers: %s", len(stale_tickers), stale_tickers)
    except Exception as exc:
        logger.warning("expire_stale_holds: %s", exc)
