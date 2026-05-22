"""
FRED Fetcher — macroeconomic indicator data from the FRED API.

Fetches GDP, CPI, PPI, PCE, payrolls, jobless claims, PMI (ISM),
yield curve, HY spread, and Fed funds rate. Returns a FredBlock
dataclass consumed by the Macro Agent.

No LLM calls. Per-indicator failures are logged at WARNING level and
stored as None — the pipeline never crashes from a single missing series.
"""

from dotenv import load_dotenv

load_dotenv()

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd
try:
    from fredapi import Fred
except ModuleNotFoundError:  # pragma: no cover - depends on local env
    Fred = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FRED_SERIES: dict[str, str] = {
    "gdp":          "GDPC1",         # quarterly real GDP (billions)
    "cpi":          "CPIAUCSL",      # CPI all items monthly
    "core_cpi":     "CPILFESL",      # CPI less food and energy
    "ppi":          "WPSFD49207",    # PPI Final Demand (BLS headline, SA) — replaced PPIACO (all commodities, too volatile)
    "pce":          "PCEPI",         # PCE deflator
    "breakeven_5y": "T5YIE",         # 5Y breakeven inflation rate (daily)
    # PR 3 additions: structural layer (monthly) + market-expectations layer (daily)
    "sticky_cpi":        "STICKCPIM157SFRBATL",  # Atlanta Fed sticky-price CPI, monthly
    "trimmed_mean_pce":  "PCETRIM12M159SFRBDAL",  # Dallas Fed 12m trimmed-mean PCE, monthly
    "five_y_five_y_fwd": "T5YIFR",               # 5-year, 5-year forward inflation rate, daily
    # ISM Mfg PMI (NAPM) was removed from FRED by ISM due to licensing restrictions.
    # Philadelphia Fed proxy also dropped — adding 50 to a 0-centered diffusion index
    # produces garbage PMI-shaped numbers. Mfg PMI slot is empty until a real source is found.
    # NMFBAI (ISM Services PMI) — will degrade gracefully to None if ISM restricts access.
    "ism_svc_raw":  "NMFBAI",                 # ISM Non-Manufacturing Business Activity Index (Services PMI)
    "jobless":      "ICSA",          # Initial jobless claims (weekly, actual headcount — NOT thousands)
    "payrolls":     "PAYEMS",        # Nonfarm payrolls (monthly, thousands)
    "fed_funds":    "FEDFUNDS",      # Federal funds rate (monthly avg)
    "yield_2y":     "DGS2",          # 2Y Treasury yield (daily)
    "yield_10y":    "DGS10",         # 10Y Treasury yield (daily)
    "hy_spread":    "BAMLH0A0HYM2",  # ICE BofA HY OAS spread (daily, bps)
}

# Series for which we compute year-over-year changes and the number of
# periods per year for each.
_YOY_SERIES: dict[str, int] = {
    "gdp":              4,   # quarterly
    "cpi":              12,  # monthly
    "core_cpi":         12,
    "ppi":              12,
    "pce":              12,
    "payrolls":         12,
    # PR 3 additions — monthly structural layer series
    "sticky_cpi":       12,  # Atlanta Fed sticky-price CPI, monthly
    "trimmed_mean_pce": 12,  # Dallas Fed trimmed-mean PCE, monthly
    # Note: five_y_five_y_fwd (T5YIFR) is a rate (not a level), already
    # expressed as a % — no YoY computation needed; raw value is used directly.
}

# Series for which we compute month-over-month changes.
_MOM_SERIES: list[str] = ["cpi", "core_cpi", "payrolls"]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class FredFetchError(Exception):
    """Raised when a FRED series fetch fails completely."""
    pass


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FredBlock:
    """Structured snapshot of FRED macro indicators for the Macro Agent."""

    raw_values: dict[str, Optional[float]] = field(default_factory=dict)
    """Latest raw value for each series key in FRED_SERIES."""

    yoy_changes: dict[str, Optional[float]] = field(default_factory=dict)
    """Year-over-year % change for: gdp, cpi, core_cpi, ppi, pce, payrolls."""

    mom_changes: dict[str, Optional[float]] = field(default_factory=dict)
    """Month-over-month % change for: cpi, core_cpi, payrolls."""

    yield_curve_spread_bps: Optional[float] = None
    """10Y minus 2Y Treasury yield spread in basis points."""

    rate_direction: float = 0.0
    """Scalar in {-1.0, -0.5, 0.0, 0.5, +1.0}.
    Negative = Fed tightening (restrictive). Positive = Fed easing (accommodative).
    """

    last_release_dates: dict[str, "datetime"] = field(default_factory=dict)
    """Estimated release timestamp per series key.

    Derived from the FRED observation index plus a per-series publication-lag
    estimate. Keys match the short keys in FRED_SERIES (e.g. ``"cpi"``,
    ``"breakeven_5y"``). Missing when a series fetch failed.

    Added in PR 2 (mixed-frequency handling). Callers that pre-date PR 2
    will receive an empty dict (default_factory), so no existing code breaks.
    """

    series_history: dict[str, list[float]] = field(default_factory=dict)
    """Recent numeric history per series key.

    Used by the layered inflation engine to derive momentum transforms from
    the same fetched FRED data without re-querying the API.
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_fred_client() -> Fred:
    """Instantiate a fredapi.Fred client using FRED_API_KEY from the environment."""
    if Fred is None:
        raise RuntimeError(
            "fredapi is not installed; fetch_fred_block() cannot query FRED in this environment."
        )
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        logger.warning("FRED_API_KEY is not set; FRED requests will likely be rejected.")
    return Fred(api_key=api_key)


_RELEASE_OFFSETS: dict[str, tuple[int, int]] = {
    # (months_forward, days_offset) from the observation period start.
    "cpi": (1, 12),
    "core_cpi": (1, 12),
    "ppi": (1, 13),
    "pce": (1, 29),
    "sticky_cpi": (1, 12),
    "trimmed_mean_pce": (1, 29),
    "payrolls": (1, 4),
    "fed_funds": (1, 29),
    "gdp": (4, 0),
}


def _estimate_release_datetime(key: str, observation_dt: datetime) -> datetime:
    """Estimate the publication timestamp for a FRED observation.

    FRED series indexes usually represent the period being measured, not the
    date the value was published. For mixed-frequency confidence weighting we
    want a release-timestamp proxy that tracks when the market actually learned
    the value, not when the underlying period started.
    """
    months_forward, days_offset = _RELEASE_OFFSETS.get(key, (0, 0))
    ts = pd.Timestamp(observation_dt)
    if months_forward:
        ts = ts + pd.DateOffset(months=months_forward)
    if days_offset:
        ts = ts + pd.Timedelta(days=days_offset)
    dt = ts.to_pydatetime()
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def fetch_series_latest(series_id: str, lookback: int = 15) -> pd.Series:
    """Fetch a FRED series and return the last *lookback* non-null observations.

    Parameters
    ----------
    series_id:
        The FRED series identifier (e.g. ``"GDPC1"``).
    lookback:
        Number of trailing non-null observations to keep.

    Returns
    -------
    pd.Series
        A pandas Series indexed by date, length ≤ ``lookback``.

    Raises
    ------
    FredFetchError
        If the API call fails or the series is entirely empty after dropping NaNs.
    """
    fred = _get_fred_client()
    try:
        raw: pd.Series = fred.get_series(series_id)
    except Exception as exc:
        # Retry with a shorter history window to reduce payload size and
        # avoid occasional upstream hiccups on large/long series.
        try:
            start = (pd.Timestamp.today() - pd.DateOffset(years=10)).date()
            logger.warning(
                "Retrying FRED series '%s' with observation_start=%s after error: %s",
                series_id,
                start,
                exc,
            )
            raw = fred.get_series(series_id, observation_start=start)
        except Exception as exc2:
            raise FredFetchError(
                f"Failed to fetch FRED series '{series_id}': {exc2}"
            ) from exc2

    if raw is None or raw.empty:
        raise FredFetchError(f"FRED series '{series_id}' returned an empty result.")

    cleaned = raw.dropna()
    if cleaned.empty:
        raise FredFetchError(
            f"FRED series '{series_id}' contained no non-null observations."
        )

    return cleaned.iloc[-lookback:]


def compute_yoy_change(series: pd.Series, periods_per_year: int) -> Optional[float]:
    """Compute year-over-year percentage change from a pandas Series.

    Parameters
    ----------
    series:
        A time-indexed Series of numeric values (non-null).
    periods_per_year:
        Number of observations per year (4 = quarterly, 12 = monthly, 52 = weekly).

    Returns
    -------
    float | None
        ``(latest / value_N_periods_ago - 1) * 100`` as a percentage,
        or ``None`` if the series is too short.
    """
    if len(series) <= periods_per_year:
        return None

    latest = series.iloc[-1]
    prior = series.iloc[-(periods_per_year + 1)]

    if prior == 0:
        return None

    return float((latest / prior - 1.0) * 100.0)


def compute_mom_change(series: pd.Series) -> Optional[float]:
    """Compute month-over-month percentage change from the last two observations.

    Parameters
    ----------
    series:
        A time-indexed Series of numeric values (non-null).

    Returns
    -------
    float | None
        ``(latest / second_latest - 1) * 100`` as a percentage,
        or ``None`` if fewer than 2 observations are present.
    """
    if len(series) < 2:
        return None

    latest = series.iloc[-1]
    prior = series.iloc[-2]

    if prior == 0:
        return None

    return float((latest / prior - 1.0) * 100.0)


def get_yield_curve_spread() -> Optional[float]:
    """Fetch the 10Y and 2Y Treasury yields and return the spread in basis points.

    Returns
    -------
    float | None
        ``(10Y_yield - 2Y_yield) * 100`` in basis points.
        A negative value indicates yield-curve inversion.
        Returns ``None`` if either series cannot be fetched.
    """
    try:
        series_10y = fetch_series_latest("DGS10", lookback=5)
        latest_10y = float(series_10y.iloc[-1])
    except (FredFetchError, Exception) as exc:
        logger.warning("Could not fetch DGS10 for yield curve spread: %s", exc)
        return None

    try:
        series_2y = fetch_series_latest("DGS2", lookback=5)
        latest_2y = float(series_2y.iloc[-1])
    except (FredFetchError, Exception) as exc:
        logger.warning("Could not fetch DGS2 for yield curve spread: %s", exc)
        return None

    spread_bps = (latest_10y - latest_2y) * 100.0
    return float(spread_bps)


def get_rate_direction() -> float:
    """Estimate the Fed's rate direction from the last 4 FEDFUNDS monthly observations.

    Compares the most-recent monthly average against the oldest in the window
    to classify the direction and magnitude of Fed policy movement.

    Returns
    -------
    float
        One of ``{-1.0, -0.5, 0.0, +0.5, +1.0}``.

        * ``+1.0``  — large cut cycle (accommodative)
        * ``+0.5``  — small cut (mildly accommodative)
        * ``0.0``   — no change (neutral)
        * ``-0.5``  — small hike (mildly restrictive)
        * ``-1.0``  — large hike cycle (restrictive)
    """
    try:
        series = fetch_series_latest("FEDFUNDS", lookback=4)
    except FredFetchError as exc:
        logger.warning("Could not fetch FEDFUNDS for rate direction: %s", exc)
        return 0.0

    if len(series) < 2:
        logger.warning("Insufficient FEDFUNDS observations to determine rate direction.")
        return 0.0

    latest = float(series.iloc[-1])
    oldest = float(series.iloc[0])
    delta = latest - oldest  # positive = hikes, negative = cuts

    if delta == 0.0:
        return 0.0

    # Threshold: 50 bps (0.5 pp) separates "small" from "large" moves
    if delta > 0:
        # Fed raised rates → restrictive
        return -1.0 if delta >= 0.5 else -0.5
    else:
        # Fed cut rates → accommodative
        return 1.0 if abs(delta) >= 0.5 else 0.5


def fetch_fred_block() -> FredBlock:
    """Master function: fetch all FRED series and assemble a FredBlock.

    Called by the Macro Agent as the primary entry point for this module.
    Per-series failures are caught, logged at WARNING level, and stored as
    ``None`` in the block — the overall call never raises.

    Returns
    -------
    FredBlock
        Populated with the latest raw values, YoY/MoM changes, yield curve
        spread, and rate direction. Fields that could not be computed are None.
    """
    raw_values: dict[str, Optional[float]] = {}
    series_cache: dict[str, Optional[pd.Series]] = {}

    # ── 1. Fetch every series ────────────────────────────────────────────────
    for key, series_id in FRED_SERIES.items():
        try:
            s = fetch_series_latest(series_id, lookback=30)
            series_cache[key] = s
            raw_values[key] = float(s.iloc[-1])
        except (FredFetchError, Exception) as exc:
            logger.warning(
                "FRED fetch failed for '%s' (%s): %s — storing None.",
                key, series_id, exc,
            )
            series_cache[key] = None
            raw_values[key] = None

    # ── 2. Year-over-year changes ────────────────────────────────────────────
    yoy_changes: dict[str, Optional[float]] = {}
    for key, periods in _YOY_SERIES.items():
        s = series_cache.get(key)
        if s is None:
            yoy_changes[key] = None
            continue
        try:
            yoy_changes[key] = compute_yoy_change(s, periods)
        except Exception as exc:
            logger.warning("YoY computation failed for '%s': %s", key, exc)
            yoy_changes[key] = None

    # ── 3. Month-over-month changes ──────────────────────────────────────────
    mom_changes: dict[str, Optional[float]] = {}
    for key in _MOM_SERIES:
        s = series_cache.get(key)
        if s is None:
            mom_changes[key] = None
            continue
        try:
            mom_changes[key] = compute_mom_change(s)
        except Exception as exc:
            logger.warning("MoM computation failed for '%s': %s", key, exc)
            mom_changes[key] = None

    # ── 4. Yield curve spread ────────────────────────────────────────────────
    try:
        spread = get_yield_curve_spread()
    except Exception as exc:
        logger.warning("Yield curve spread computation failed: %s", exc)
        spread = None

    # ── 5. Rate direction ────────────────────────────────────────────────────
    try:
        direction = get_rate_direction()
    except Exception as exc:
        logger.warning("Rate direction computation failed: %s", exc)
        direction = 0.0

    # ── 6. PMI alias ─────────────────────────────────────────────────────────
    # Services PMI: alias ism_svc_raw → ism_svc for downstream consumers.
    raw_values["ism_svc"] = raw_values.get("ism_svc_raw")

    # ── 7. Release dates + recent history for downstream inflation layers ────
    last_release_dates: dict[str, datetime] = {}
    series_history: dict[str, list[float]] = {}
    for key, s in series_cache.items():
        if s is None or s.empty:
            continue
        try:
            series_history[key] = [float(v) for v in s.dropna().tolist()]
            last_idx = s.index[-1]
            if hasattr(last_idx, "to_pydatetime"):
                dt = last_idx.to_pydatetime()
            else:
                dt = datetime(last_idx.year, last_idx.month, last_idx.day)
            if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            last_release_dates[key] = _estimate_release_datetime(key, dt)
        except Exception as exc:
            logger.warning(
                "Could not extract release metadata for series '%s': %s", key, exc
            )

    return FredBlock(
        raw_values=raw_values,
        yoy_changes=yoy_changes,
        mom_changes=mom_changes,
        yield_curve_spread_bps=spread,
        rate_direction=direction,
        last_release_dates=last_release_dates,
        series_history=series_history,
    )
