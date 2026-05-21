"""Polygon ingestion for commodity ETF prices used by the market-expectations layer.

PR 3 of the inflation engine redesign (docs/inflation_engine_design.md §2.4).

Fetches 5 commodity ETF snapshots in a single Polygon bulk-snapshot call:
    USO  — WTI crude oil proxy
    BNO  — Brent crude oil proxy
    UGA  — Gasoline (RBOB) proxy
    CPER — Copper proxy
    DBC  — Broad commodity composite

Design choices:
- One bulk call per scoring cycle — well under the 300 req/min budget.
- Graceful degradation: any Polygon error returns a MarketCommoditiesBlock
  with all-None prices so the engine continues with reduced confidence.
- No caching or retry logic — that is a separate concern (ops layer).
- Follows the established pattern from backend/macro/indicators/market_fetcher.py:
  same load_dotenv() placement, same logging style, same error handling.

Environment
-----------
POLYGON_API_KEY  must be set in .env (or environment).
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Commodity ETF tickers — confirmed on user's Polygon plan (see design doc §6.2)
# ---------------------------------------------------------------------------

_COMMODITY_TICKERS = ["USO", "BNO", "UGA", "CPER", "DBC"]


# ---------------------------------------------------------------------------
# Data structure
# ---------------------------------------------------------------------------


@dataclass
class MarketCommoditiesBlock:
    """Latest prices for commodity ETF proxies.

    All fields are Optional — individual tickers may fail without breaking
    the engine (layers degrade confidence proportionally for missing inputs).

    ``fetched_at`` is always populated (UTC wall-clock at fetch time).
    """

    wti_oil_price: Optional[float]
    """USO ETF close/prevDay price (WTI crude oil proxy)."""

    brent_oil_price: Optional[float]
    """BNO ETF close/prevDay price (Brent crude proxy)."""

    gasoline_price: Optional[float]
    """UGA ETF close/prevDay price (Gasoline/RBOB proxy)."""

    copper_price: Optional[float]
    """CPER ETF close/prevDay price (Copper proxy)."""

    commodity_composite: Optional[float]
    """DBC ETF close/prevDay price (broad commodity composite)."""

    fetched_at: datetime = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.fetched_at is None:
            self.fetched_at = datetime.now(timezone.utc)

    @classmethod
    def all_none(cls) -> "MarketCommoditiesBlock":
        """Return a block with all prices None (degraded mode)."""
        return cls(
            wti_oil_price=None,
            brent_oil_price=None,
            gasoline_price=None,
            copper_price=None,
            commodity_composite=None,
            fetched_at=datetime.now(timezone.utc),
        )


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _extract_price(ticker_data: dict) -> Optional[float]:
    """Extract a usable price from a Polygon snapshot ticker dict.

    Polygon's bulk-snapshot response nests data under ``day`` (intraday latest)
    or ``prevDay`` (previous session close).  We prefer ``day.c`` (latest close)
    and fall back to ``prevDay.c`` for after-hours or weekend runs.

    Parameters
    ----------
    ticker_data:
        A single ticker entry from the Polygon bulk-snapshot response.

    Returns
    -------
    float | None
        Price as a float, or None if no usable value is available.
    """
    # Try current day close first
    day = ticker_data.get("day") or {}
    price = day.get("c")
    if price and float(price) > 0:
        return float(price)

    # Fall back to previous day close
    prev = ticker_data.get("prevDay") or {}
    price = prev.get("c")
    if price and float(price) > 0:
        return float(price)

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_commodity_block() -> MarketCommoditiesBlock:
    """Fetch all 5 commodity ETF prices in one Polygon bulk-snapshot call.

    Uses the ``/v2/snapshot/locale/us/markets/stocks/tickers`` endpoint to
    retrieve all tickers in a single HTTP request.

    Returns
    -------
    MarketCommoditiesBlock
        Populated with latest prices where available; None fields for any
        individual ticker that is missing or errored.

    Notes
    -----
    On any Polygon error the function logs a WARNING and returns an all-None
    block rather than raising — the scoring engine should still run with
    degraded confidence for the market-expectations layer.
    """
    api_key = os.getenv("POLYGON_API_KEY")
    if not api_key:
        logger.warning(
            "fetch_commodity_block: POLYGON_API_KEY not set — returning all-None block"
        )
        return MarketCommoditiesBlock.all_none()

    tickers_param = ",".join(_COMMODITY_TICKERS)
    url = (
        f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
        f"?tickers={tickers_param}&apiKey={api_key}"
    )

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logger.warning(
            "fetch_commodity_block: Polygon request failed — %s. Returning all-None block.",
            exc,
        )
        return MarketCommoditiesBlock.all_none()

    # Parse the tickers list from the response.
    tickers_list = payload.get("tickers") or []
    price_map: dict[str, Optional[float]] = {t: None for t in _COMMODITY_TICKERS}

    for item in tickers_list:
        ticker = item.get("ticker")
        if ticker not in price_map:
            continue
        price = _extract_price(item)
        if price is not None:
            price_map[ticker] = price
            logger.debug("fetch_commodity_block: %s → %.4f", ticker, price)
        else:
            logger.debug("fetch_commodity_block: %s → no usable price in snapshot", ticker)

    # Log summary
    present = [t for t, p in price_map.items() if p is not None]
    missing = [t for t, p in price_map.items() if p is None]
    if missing:
        logger.warning(
            "fetch_commodity_block: prices unavailable for %s (present: %s)",
            missing,
            present,
        )
    else:
        logger.debug("fetch_commodity_block: all 5 commodity prices fetched successfully")

    return MarketCommoditiesBlock(
        wti_oil_price=price_map["USO"],
        brent_oil_price=price_map["BNO"],
        gasoline_price=price_map["UGA"],
        copper_price=price_map["CPER"],
        commodity_composite=price_map["DBC"],
    )
