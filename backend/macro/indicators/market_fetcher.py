"""
Market Stress Indicator Fetcher

Fetches VIX, DXY, and S&P 500 vs 200-day SMA using yfinance.
Called by the Macro Agent as part of the regime classification pipeline.

Treasury yields are NOT fetched here — those come from fred_fetcher.py.
"""

from dotenv import load_dotenv

load_dotenv()

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# ── Data Structures ───────────────────────────────────────────────────────────


@dataclass
class MarketBlock:
    vix: Optional[float]               # VIX index level
    dxy: Optional[float]               # US Dollar Index level
    spx_price: Optional[float]         # S&P 500 latest close
    spx_sma_200: Optional[float]       # S&P 500 200-day simple moving average
    spx_pct_above_sma: Optional[float] # (spx_price - spx_sma_200) / spx_sma_200 * 100


# ── Individual Fetchers ───────────────────────────────────────────────────────


def fetch_vix() -> Optional[float]:
    """
    Fetch the most recent non-null VIX close from the last 5 trading days.

    Returns the latest available float value, or None on any error.
    """
    try:
        df = yf.download("^VIX", period="5d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            logger.warning("fetch_vix: empty DataFrame returned for ^VIX")
            return None
        raw = df["Close"]
        close = (raw.iloc[:, 0] if isinstance(raw, pd.DataFrame) else raw).dropna()
        if close.empty:
            logger.warning("fetch_vix: no non-null Close values for ^VIX")
            return None
        value = float(close.iloc[-1])
        logger.debug("fetch_vix: %.4f", value)
        return value
    except Exception as exc:
        logger.warning("fetch_vix: failed — %s", exc)
        return None


def fetch_dxy() -> Optional[float]:
    """
    Fetch the most recent non-null DXY (US Dollar Index) close from the last 5 trading days.

    Returns the latest available float value, or None on any error.
    """
    try:
        df = yf.download("DX-Y.NYB", period="5d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            logger.warning("fetch_dxy: empty DataFrame returned for DX-Y.NYB")
            return None
        raw = df["Close"]
        close = (raw.iloc[:, 0] if isinstance(raw, pd.DataFrame) else raw).dropna()
        if close.empty:
            logger.warning("fetch_dxy: no non-null Close values for DX-Y.NYB")
            return None
        value = float(close.iloc[-1])
        logger.debug("fetch_dxy: %.4f", value)
        return value
    except Exception as exc:
        logger.warning("fetch_dxy: failed — %s", exc)
        return None


def fetch_spx_vs_200sma() -> dict:
    """
    Fetch S&P 500 price and compute its 200-day simple moving average.

    Downloads 1 year of daily closes for ^GSPC (~252 trading days), which
    guarantees enough history to compute a 200-day rolling mean.

    Returns a dict with keys:
        price         (float | None) — most recent close
        sma_200       (float | None) — 200-day SMA at the most recent date
        pct_above_sma (float | None) — (price - sma_200) / sma_200 * 100

    Returns all-None values on any error; never raises.
    """
    _empty: dict = {"price": None, "sma_200": None, "pct_above_sma": None}

    try:
        df = yf.download("^GSPC", period="1y", progress=False, auto_adjust=True)
        if df is None or df.empty:
            logger.warning("fetch_spx_vs_200sma: empty DataFrame returned for ^GSPC")
            return _empty

        raw = df["Close"]
        close = (raw.iloc[:, 0] if isinstance(raw, pd.DataFrame) else raw).dropna()
        if len(close) < 200:
            logger.warning(
                "fetch_spx_vs_200sma: only %d rows available, need 200 for SMA",
                len(close),
            )
            return _empty

        sma_series = close.rolling(window=200).mean().dropna()
        if sma_series.empty:
            logger.warning("fetch_spx_vs_200sma: 200-day SMA produced no values")
            return _empty

        price = float(close.iloc[-1])
        sma_200 = float(sma_series.iloc[-1])
        pct_above_sma = (price - sma_200) / sma_200 * 100

        logger.debug(
            "fetch_spx_vs_200sma: price=%.2f  sma_200=%.2f  pct_above=%.2f%%",
            price,
            sma_200,
            pct_above_sma,
        )

        return {
            "price": price,
            "sma_200": sma_200,
            "pct_above_sma": pct_above_sma,
        }

    except Exception as exc:
        logger.warning("fetch_spx_vs_200sma: failed — %s", exc)
        return _empty


# ── Master Function ───────────────────────────────────────────────────────────


def fetch_market_block() -> MarketBlock:
    """
    Aggregate all market stress indicators into a single MarketBlock.

    Called by macro_agent.py as part of the regime classification pipeline.
    Individual fetch failures degrade gracefully — the corresponding field is
    set to None and the pipeline continues with the remaining data.

    Returns a MarketBlock dataclass instance.
    """
    vix = fetch_vix()
    if vix is not None:
        logger.debug("fetch_market_block: VIX fetched successfully")
    else:
        logger.warning("fetch_market_block: VIX unavailable")

    dxy = fetch_dxy()
    if dxy is not None:
        logger.debug("fetch_market_block: DXY fetched successfully")
    else:
        logger.warning("fetch_market_block: DXY unavailable")

    spx = fetch_spx_vs_200sma()
    if spx["price"] is not None:
        logger.debug("fetch_market_block: SPX vs 200SMA fetched successfully")
    else:
        logger.warning("fetch_market_block: SPX vs 200SMA unavailable")

    return MarketBlock(
        vix=vix,
        dxy=dxy,
        spx_price=spx["price"],
        spx_sma_200=spx["sma_200"],
        spx_pct_above_sma=spx["pct_above_sma"],
    )
