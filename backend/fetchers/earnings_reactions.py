"""
Earnings reaction fetcher.

get_earnings_reactions(ticker, n_quarters) returns a list of historical
earnings events for a ticker with:
  - date               : earnings release date (YYYY-MM-DD)
  - reported_eps       : actual EPS reported
  - consensus_eps      : analyst consensus EPS estimate
  - surprise_pct       : (reported - consensus) / abs(consensus), or None
  - price_reaction_1d  : close_day_after / close_day_before - 1
  - price_reaction_5d  : close_5days_after / close_day_before - 1

Data sources:
  - Earnings history (dates, reported EPS, consensus EPS): yfinance earnings_history
    Both fields come from the same adjusted-EPS basis (press-release figures), so
    reported and consensus are directly comparable. Implausible surprises (>±150%)
    are nulled out to protect downstream signal computation.
  - Price data: Polygon /v2/aggs daily OHLCV (adjusted)
"""

import logging
import os
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Days of price history to fetch per ticker (covers ~2 years of quarters + buffer)
_PRICE_LOOKBACK_DAYS = 730
# Sanity cap: surprises beyond ±150% almost certainly indicate a stale or wrong-basis
# consensus estimate from yfinance — null them out rather than storing garbage.
_MAX_PLAUSIBLE_SURPRISE = 1.5
# Trading days to skip before earnings close is "settled" (report after close → next day)
_DAYS_AFTER_SHORT = 1
_DAYS_AFTER_LONG = 5


def _fetch_polygon_closes(
    ticker: str,
    start: date,
    end: date,
    polygon_key: str,
) -> Dict[str, float]:
    """
    Fetch adjusted daily closes from Polygon for a date range.
    Returns {YYYY-MM-DD: close_price}.
    """
    try:
        resp = requests.get(
            f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day"
            f"/{start.isoformat()}/{end.isoformat()}",
            params={"adjusted": "true", "sort": "asc", "limit": 1000, "apiKey": polygon_key},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return {
            date.fromtimestamp(r["t"] / 1000).isoformat(): r["c"]
            for r in results
        }
    except Exception as exc:
        logger.warning("earnings_reactions: Polygon price fetch failed for %s — %s", ticker, exc)
        return {}


def _nth_trading_close(
    closes: Dict[str, float],
    anchor: date,
    n: int,
) -> Optional[float]:
    """
    Return the close n trading days after anchor (anchor not included).
    Iterates forward up to 2*n calendar days to skip weekends/holidays.
    """
    sorted_dates = sorted(closes.keys())
    count = 0
    for d in sorted_dates:
        if d > anchor.isoformat():
            count += 1
            if count == n:
                return closes[d]
    return None


def _prev_trading_close(
    closes: Dict[str, float],
    anchor: date,
) -> Optional[float]:
    """Return the last available close strictly before anchor."""
    sorted_dates = sorted(closes.keys(), reverse=True)
    for d in sorted_dates:
        if d < anchor.isoformat():
            return closes[d]
    return None


def get_earnings_reactions(
    ticker: str,
    n_quarters: int = 8,
) -> List[Dict[str, Any]]:
    """
    Return up to n_quarters historical earnings reaction records for ticker.

    Each record:
      {
        "date": str,                 # earnings release date YYYY-MM-DD
        "reported_eps": float|None,
        "consensus_eps": float|None,
        "surprise_pct": float|None,  # (reported - consensus) / abs(consensus)
        "price_reaction_1d": float|None,   # next-close / prev-close - 1
        "price_reaction_5d": float|None,   # +5 trading days / prev-close - 1
      }

    Returns [] if Polygon key is missing or data is unavailable.
    """
    polygon_key = os.getenv("POLYGON_API_KEY")
    if not polygon_key:
        logger.warning("earnings_reactions: POLYGON_API_KEY not set — skipping")
        return []

    # ── Pull earnings history from yfinance ──────────────────────────────────
    # yfinance earnings_history returns reported + estimate on a consistent
    # adjusted basis. Implausible surprises (>±150%) are nulled later.
    raw_events: List[Dict[str, Any]] = []
    try:
        t = yf.Ticker(ticker)
        # earnings_history is a DataFrame indexed by date
        df = t.earnings_history
        if df is not None and not df.empty:
            for dt_idx, row in df.iterrows():
                # dt_idx is typically a Timestamp
                event_date = dt_idx.date() if hasattr(dt_idx, "date") else dt_idx
                reported = row.get("eps_actual")
                consensus = row.get("eps_estimate")
                
                try:
                    reported_val = float(reported) if reported is not None and reported == reported else None
                    consensus_val = float(consensus) if consensus is not None and consensus == consensus else None
                except (TypeError, ValueError):
                    reported_val = consensus_val = None
                
                if reported_val is not None:
                    raw_events.append({
                        "date": event_date,
                        "reported_eps": reported_val,
                        "consensus_eps": consensus_val,
                    })
    except Exception as exc:
        logger.warning("earnings_reactions: yfinance fetch failed for %s — %s", ticker, exc)

    if not raw_events:
        return []

    # Sort descending, take the most recent n_quarters past events
    today = date.today()
    past_events = sorted(
        [e for e in raw_events if e["date"] < today],
        key=lambda e: e["date"],
        reverse=True,
    )[:n_quarters]

    if not past_events:
        return []

    # ── Fetch price data covering the full event window ──────────────────────
    oldest_event = min(e["date"] for e in past_events)
    price_start = oldest_event - timedelta(days=10)  # buffer for prev-close lookup
    price_end = today
    closes = _fetch_polygon_closes(ticker, price_start, price_end, polygon_key)

    if not closes:
        return []

    # ── Build reaction records ────────────────────────────────────────────────
    reactions: List[Dict[str, Any]] = []
    for event in sorted(past_events, key=lambda e: e["date"]):
        event_date = event["date"]
        reported = event["reported_eps"]
        consensus = event["consensus_eps"]

        # EPS surprise
        surprise_pct: Optional[float] = None
        if reported is not None and consensus is not None and consensus != 0:
            raw_surprise = (reported - consensus) / abs(consensus)
            if abs(raw_surprise) <= _MAX_PLAUSIBLE_SURPRISE:
                surprise_pct = round(raw_surprise, 4)
            else:
                logger.warning(
                    "earnings_reactions: %s implausible surprise %.0f%% on %s — nulling out",
                    ticker, raw_surprise * 100, event_date,
                )

        # Price reactions
        prev_close = _prev_trading_close(closes, event_date)
        close_1d = _nth_trading_close(closes, event_date, _DAYS_AFTER_SHORT)
        close_5d = _nth_trading_close(closes, event_date, _DAYS_AFTER_LONG)

        reaction_1d: Optional[float] = None
        reaction_5d: Optional[float] = None
        if prev_close and close_1d:
            reaction_1d = round((close_1d - prev_close) / prev_close, 4)
        if prev_close and close_5d:
            reaction_5d = round((close_5d - prev_close) / prev_close, 4)

        reactions.append({
            "date": event_date.isoformat(),
            "reported_eps": reported,
            "consensus_eps": consensus,
            "surprise_pct": surprise_pct,
            "price_reaction_1d": reaction_1d,
            "price_reaction_5d": reaction_5d,
        })

    # Return most-recent first
    return list(reversed(reactions))
