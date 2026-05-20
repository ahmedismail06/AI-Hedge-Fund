"""
Market data API — proxies Polygon price history for the frontend charts.

Endpoints:
  GET /market/bars/{ticker}?range=1D|1W|1M|3M|1Y
    Returns OHLCV bars for the given ticker and time range.
    Response: list of {t, o, h, l, c, v} where t is ISO date string.
"""

from dotenv import load_dotenv
load_dotenv()

import os
import logging
from datetime import date, timedelta
from fastapi import APIRouter, HTTPException, Query
import requests

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/market", tags=["market"])

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")

RANGE_DAYS = {
    "1D": 1,
    "1W": 7,
    "1M": 30,
    "3M": 90,
    "1Y": 365,
}

RANGE_MULTIPLIER = {
    "1D": ("minute", 5),
    "1W": ("hour",   1),
    "1M": ("day",    1),
    "3M": ("day",    1),
    "1Y": ("day",    1),
}


@router.get("/bars/{ticker}")
def get_ticker_bars(
    ticker: str,
    range: str = Query("1M", pattern="^(1D|1W|1M|3M|1Y)$"),
):
    """Returns price bars for a ticker for the frontend Robinhood-style chart."""
    if not POLYGON_API_KEY:
        raise HTTPException(status_code=503, detail="Polygon API key not configured")

    days = RANGE_DAYS.get(range, 30)
    timespan, mult = RANGE_MULTIPLIER.get(range, ("day", 1))
    end = date.today()
    start = end - timedelta(days=days)

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker.upper()}/range"
        f"/{mult}/{timespan}/{start.isoformat()}/{end.isoformat()}"
    )
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 5000,
        "apiKey": POLYGON_API_KEY,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.error("Polygon bars error for %s: %s", ticker, exc)
        raise HTTPException(status_code=502, detail=f"Polygon API error: {exc}")

    results = data.get("results") or []
    bars = []
    for r in results:
        ts = r.get("t")
        if ts is None:
            continue
        try:
            dt = date.fromtimestamp(ts / 1000).isoformat()
        except Exception:
            dt = str(ts)
        bars.append({
            "t": dt,
            "o": r.get("o"),
            "h": r.get("h"),
            "l": r.get("l"),
            "c": r.get("c"),
            "v": r.get("v"),
        })

    return bars
