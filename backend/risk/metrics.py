"""
Performance Metrics — nightly computation.

Pulls all CLOSED positions from Supabase and computes:
  Sharpe ratio, Sortino ratio, max drawdown, VaR 95%/99% (historical simulation,
  no normality assumption), beta (vs SPY), Calmar ratio.

Also reads OPEN positions to append current gross/net exposure.
Results are upserted to portfolio_metrics (unique on date).

Graceful fallback: returns a PortfolioMetrics with all None fields if < 5
closed positions exist (insufficient data for meaningful statistics).
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import os

import numpy as np
import requests
from dotenv import load_dotenv

from backend.models.risk import PortfolioMetrics
from backend.portfolio.exposure_tracker import get_current_exposure

load_dotenv()

logger = logging.getLogger(__name__)

_MIN_POSITIONS = 5        # minimum closed positions for stats to be meaningful
_TRADING_DAYS = 252       # annualisation factor


def compute_nightly_metrics(supabase_client) -> Optional[PortfolioMetrics]:
    """
    Compute tonight's PortfolioMetrics and upsert to Supabase.

    Returns the PortfolioMetrics object (with None fields if insufficient data).
    """
    today = date.today().isoformat()

    # ── 1. Fetch all CLOSED positions ─────────────────────────────────────────
    resp = (
        supabase_client
        .table("positions")
        .select("ticker,entry_price,current_price,pnl_pct,pct_of_portfolio,opened_at,closed_at")
        .eq("status", "CLOSED")
        .execute()
    )
    closed = resp.data or []

    # ── 2. Fetch OPEN positions for live exposure ─────────────────────────────
    resp_open = (
        supabase_client
        .table("positions")
        .select("ticker,dollar_size,pct_of_portfolio,direction,current_price,entry_price,sector")
        .eq("status", "OPEN")
        .execute()
    )
    open_positions = resp_open.data or []

    # ── 3. Build returns series from closed positions ─────────────────────────
    returns = _build_returns(closed)

    # ── 4. Compute exposure metrics ───────────────────────────────────────────
    from backend.broker.ibkr import get_portfolio_value as _get_portfolio_value
    gross_exp = None
    net_exp = None
    try:
        _pv = _get_portfolio_value()
        if open_positions and _pv and _pv > 0:
            exposure = get_current_exposure(open_positions, portfolio_value=_pv)
            gross_exp = exposure.get("gross_exposure_pct")
            net_exp = exposure.get("net_exposure_pct")
    except Exception as _pv_exc:
        logger.warning("get_portfolio_value failed — exposure will be None: %s", _pv_exc)

    if len(returns) < _MIN_POSITIONS:
        logger.warning(
            "compute_nightly_metrics: only %d closed positions, skipping stats.", len(returns)
        )
        metrics = PortfolioMetrics(
            date=today,
            gross_exposure=gross_exp,
            net_exposure=net_exp,
        )
    else:
        r = np.array(returns, dtype=float)

        # Sharpe
        sharpe = _sharpe(r)

        # Sortino
        sortino = _sortino(r)

        # Max drawdown
        max_dd = _max_drawdown(r)

        # VaR (historical simulation — no normality assumption)
        var_95 = float(np.percentile(r, 5))
        var_99 = float(np.percentile(r, 1))

        # Beta vs SPY
        beta = _compute_beta(closed)

        # Calmar
        calmar = _calmar(r, max_dd)

        metrics = PortfolioMetrics(
            date=today,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown=max_dd,
            var_95=var_95,
            var_99=var_99,
            beta=beta,
            calmar_ratio=calmar,
            gross_exposure=gross_exp,
            net_exposure=net_exp,
        )

    # ── 5. Upsert to Supabase ─────────────────────────────────────────────────
    row = metrics.model_dump()
    row = {k: (float(v) if isinstance(v, (int, float, np.floating)) and v is not None else v)
           for k, v in row.items()}

    supabase_client.table("portfolio_metrics").upsert(
        row, on_conflict="date"
    ).execute()

    logger.info("portfolio_metrics upserted for %s", today)
    return metrics


# ──────────────────────────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────────────────────────

def _build_returns(closed: list[dict]) -> list[float]:
    """Extract pnl_pct from each closed position as a per-trade return."""
    result = []
    for pos in closed:
        val = pos.get("pnl_pct")
        if val is not None:
            try:
                result.append(float(val))
            except (TypeError, ValueError):
                pass
    return result


def _sharpe(r: np.ndarray) -> Optional[float]:
    if r.std() == 0:
        return None
    return float(r.mean() / r.std() * np.sqrt(_TRADING_DAYS))


def _sortino(r: np.ndarray) -> Optional[float]:
    downside = r[r < 0]
    if len(downside) == 0 or downside.std() == 0:
        return None
    return float(r.mean() / downside.std() * np.sqrt(_TRADING_DAYS))


def _max_drawdown(r: np.ndarray) -> Optional[float]:
    """Compute max peak-to-trough drawdown on the cumulative equity curve."""
    equity = np.cumprod(1 + r)
    peak = np.maximum.accumulate(equity)
    drawdowns = (equity - peak) / peak
    return float(drawdowns.min()) if len(drawdowns) > 0 else None


def _calmar(r: np.ndarray, max_dd: Optional[float]) -> Optional[float]:
    if max_dd is None or max_dd == 0:
        return None
    annualized = float(r.mean() * _TRADING_DAYS)
    return float(annualized / abs(max_dd))


def _compute_beta(closed: list[dict]) -> Optional[float]:
    """
    Estimate beta vs SPY using the date range spanned by closed positions.
    Fetches SPY daily closes from Polygon /v2/aggs. Falls back to None on
    any error or missing POLYGON_API_KEY.
    """
    try:
        polygon_key = os.getenv("POLYGON_API_KEY")
        if not polygon_key:
            logger.warning("POLYGON_API_KEY not set — beta not computed")
            return None

        dates = []
        for pos in closed:
            for field in ("opened_at", "closed_at"):
                raw = pos.get(field)
                if raw:
                    try:
                        dates.append(datetime.fromisoformat(raw[:10]).date())
                    except ValueError:
                        pass

        if not dates:
            return None

        start = min(dates) - timedelta(days=1)
        end = max(dates) + timedelta(days=1)

        resp = requests.get(
            f"https://api.polygon.io/v2/aggs/ticker/SPY/range/1/day"
            f"/{start.isoformat()}/{end.isoformat()}",
            params={"adjusted": "true", "sort": "asc", "limit": 500, "apiKey": polygon_key},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if len(results) < 2:
            return None

        spy_closes = np.array([r["c"] for r in results], dtype=float)
        spy_returns = np.diff(spy_closes) / spy_closes[:-1]

        port_returns = np.array(_build_returns(closed), dtype=float)
        n = min(len(spy_returns), len(port_returns))
        if n < 2:
            return None

        spy_r = spy_returns[-n:]
        port_r = port_returns[-n:]

        cov_matrix = np.cov(port_r, spy_r)
        beta = cov_matrix[0, 1] / np.var(spy_r)
        return float(beta)
    except Exception as exc:
        logger.warning("beta computation failed: %s", exc)
        return None
