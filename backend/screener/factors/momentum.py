"""
Momentum Factor Scorer (20% weight in composite).

Returns raw values for each sub-metric. Normalization deferred to scorer.py.

Sub-components:
  price_12_1    35%  — 12-month to 1-month price return (standard academic momentum)
  price_6_1     35%  — 6-month to 1-month price return
  eps_revision  30%  — EPS estimate revision trend

Short interest bonus (additive to composite, not a sub-component):
  SI > 30%  → +1.0 to composite (capped at 10)
  SI > 20%  → +0.5 to composite (capped at 10)
  In Risk-On regime these bonuses are doubled (applied in scorer.py).
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _price_momentum(price_history: list[dict], start_months_ago: int, end_months_ago: int) -> Optional[float]:
    """
    Compute price return from start_months_ago to end_months_ago.

    price_history: list of daily OHLCV dicts [{date, close, ...}] sorted oldest→newest.
    Returns fractional return (e.g. 0.25 = 25%), or None if insufficient data.
    """
    if not price_history:
        return None

    total_bars = len(price_history)
    # Approximate: 21 trading days ≈ 1 month
    start_idx = total_bars - (start_months_ago * 21)
    end_idx   = total_bars - (end_months_ago  * 21)

    if start_idx < 0 or end_idx < 0 or start_idx >= end_idx:
        return None
    if start_idx >= total_bars or end_idx >= total_bars:
        return None

    try:
        start_price = float(price_history[start_idx]["close"])
        end_price   = float(price_history[end_idx]["close"])
    except (KeyError, ValueError, TypeError):
        return None

    if start_price <= 0:
        return None
    return (end_price - start_price) / start_price


def _eps_revision(fmp_data: dict) -> Optional[float]:
    """
    Compute EPS revision trend using yfinance consensus estimates.

    fmp_data["consensus_eps_current_year"] vs a proxy for 90-days-ago estimate.
    yfinance doesn't provide 90-days-ago by default; we use the earningsEstimate
    'low' vs 'avg' spread as a proxy for revision magnitude, or return None.

    Note: A more robust implementation would store estimates historically.
    For now: positive revision = current_year EPS > next_year EPS × 0.95
    (i.e., growth expected), negative = shrinkage.
    """
    eps_cy = fmp_data.get("consensus_eps_current_year")
    eps_ny = fmp_data.get("consensus_eps_next_year")

    if eps_cy is None:
        return None

    # Positive revision signal: analysts expect growth (next year > current year)
    if eps_ny is not None and eps_cy != 0:
        val = (eps_ny - eps_cy) / abs(eps_cy)
        return float(val)  # cast numpy scalar → plain Python float for JSON serialisation

    return None


def score_momentum(ticker: str, price_history: list[dict], fmp_data: dict) -> dict:
    """
    Compute raw momentum sub-metrics for a ticker.

    Args:
        ticker: Stock ticker symbol.
        price_history: List of daily OHLCV dicts sorted oldest→newest.
                       Minimum ~13 months of history for 12-1 momentum.
                       Each dict must have a 'close' key.
        fmp_data: Output of fetch_fmp() — contains consensus EPS estimates
                  and short_interest_pct.

    Returns:
        {
            "ticker": str,
            "raw_values": {
                "price_12_1":    float | None,  # 12-1 month return
                "price_6_1":     float | None,  # 6-1 month return
                "eps_revision":  float | None,  # fractional EPS estimate change
            },
            "short_interest_bonus": float,      # additive bonus for composite; 0 | 0.5 | 1.0
        }
    """
    price_12_1 = _price_momentum(price_history, start_months_ago=12, end_months_ago=1)
    price_6_1  = _price_momentum(price_history, start_months_ago=6,  end_months_ago=1)
    eps_rev    = _eps_revision(fmp_data)

    # Short interest bonus (additive to composite in scorer.py)
    si_pct = fmp_data.get("short_interest_pct")
    si_bonus = 0.0
    if si_pct is not None:
        if si_pct > 30:
            si_bonus = 1.0
        elif si_pct > 20:
            si_bonus = 0.5

    raw_values = {
        "price_12_1":   price_12_1,
        "price_6_1":    price_6_1,
        "eps_revision": eps_rev,
    }

    logger.debug(
        "%s momentum raw: 12-1=%.2f 6-1=%.2f eps_rev=%.2f si_bonus=%.1f",
        ticker,
        price_12_1 or 0,
        price_6_1 or 0,
        eps_rev or 0,
        si_bonus,
    )

    return {
        "ticker":               ticker.upper(),
        "raw_values":           raw_values,
        "short_interest_bonus": si_bonus,
    }
