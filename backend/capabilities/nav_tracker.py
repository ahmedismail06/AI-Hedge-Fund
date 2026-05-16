"""
Trailing 30-day NAV tracker.

Computes the trailing 30-day average portfolio NAV from account_snapshots.
Result is cached for 1 hour to avoid re-querying on every PM cycle.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_MIN_DAYS = 5   # fall back to current NAV if fewer than this many daily samples exist
_CACHE_TTL = 3600  # seconds

_cache_value: float | None = None
_cache_time: float = 0.0


def _get_client():
    from supabase import create_client
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY not set")
    return create_client(url, key)


def get_trailing_nav(supabase_client=None) -> float:
    """
    Compute trailing 30-day average NAV from account_snapshots.

    Groups snapshots by calendar date (UTC), takes the last net_liquidation
    per day, and averages over up to 30 days. If fewer than _MIN_DAYS of
    history exist, returns the current live NAV from IBKR instead.
    """
    client = supabase_client or _get_client()

    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=30)).isoformat()

    try:
        resp = (
            client.table("account_snapshots")
            .select("net_liquidation,captured_at")
            .gte("captured_at", cutoff)
            .order("captured_at", desc=False)
            .execute()
        )
        rows = resp.data or []
    except Exception as exc:
        logger.warning("get_trailing_nav: Supabase query failed — %s", exc)
        rows = []

    if not rows:
        return _current_nav_fallback()

    # Group by calendar date (UTC), keep last snapshot per day
    daily: dict[str, float] = {}
    for row in rows:
        captured_at = row.get("captured_at", "")
        nav_val = row.get("net_liquidation")
        if not captured_at or nav_val is None:
            continue
        date_key = captured_at[:10]  # "YYYY-MM-DD"
        daily[date_key] = float(nav_val)

    if len(daily) < _MIN_DAYS:
        logger.info(
            "get_trailing_nav: only %d days of history — using current NAV", len(daily)
        )
        return _current_nav_fallback()

    avg = sum(daily.values()) / len(daily)
    logger.debug(
        "get_trailing_nav: %d days averaged → trailing_30d_nav=%.2f", len(daily), avg
    )
    return avg


def _current_nav_fallback() -> float:
    """Return live NAV from IBKR (or account_snapshots fallback inside get_portfolio_value)."""
    try:
        from backend.broker.ibkr import get_portfolio_value
        return get_portfolio_value()
    except Exception as exc:
        logger.warning("get_trailing_nav: IBKR fallback also failed — %s", exc)
        raise RuntimeError("Cannot determine NAV: no historical snapshots and IBKR unreachable") from exc


def get_trailing_nav_cached() -> float:
    """
    Return trailing 30-day average NAV, refreshing at most once per hour.
    Uses a fresh Supabase client per call (project convention).
    """
    global _cache_value, _cache_time

    now = time.monotonic()
    if _cache_value is not None and (now - _cache_time) < _CACHE_TTL:
        return _cache_value

    nav = get_trailing_nav(_get_client())
    _cache_value = nav
    _cache_time = now
    return nav


def invalidate_nav_cache() -> None:
    """Force next call to get_trailing_nav_cached() to recompute."""
    global _cache_value, _cache_time
    _cache_value = None
    _cache_time = 0.0
