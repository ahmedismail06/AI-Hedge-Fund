"""
Macro Scorer — Quantitative regime classification engine.

Takes raw indicator data from fred_fetcher.py and market_fetcher.py and
produces dimensional scores + regime classification.

No LLM calls. No Supabase. Pure computation.

Dimensional scores range from -1.0 to +1.0 where:
  growth_score:    positive = expanding economy
  inflation_score: positive = more inflationary pressure
  fed_score:       positive = accommodative Fed
  stress_score:    positive = high market stress

Regime outputs: Risk-On | Risk-Off | Stagflation | Transitional

NOTE (post-LLM-refactor): score_indicators(), classify_regime(),
compute_regime_confidence(), compute_regime_score(), and the four
_score_*() helpers are NO LONGER CALLED by macro_agent.py.
The LLM now performs all dimension scoring and regime classification.
These functions are retained for reference and future use in the
Backtest Engine (Component 11). build_raw_indicators() and
build_indicator_scores() remain in active use.
"""

from dotenv import load_dotenv

load_dotenv()

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from backend.macro.indicators.fred_fetcher import FredBlock
from backend.macro.indicators.market_fetcher import MarketBlock
from backend.macro.inflation_engine import normalize
from backend.macro.inflation_engine import config as inflation_config
from backend.macro.inflation_engine.staleness import (
    confidence_from_meta,
    staleness_confidence,
)
from backend.macro.inflation_engine.types import FREQUENCY_DAYS, IndicatorMeta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------


@dataclass
class RawIndicators:
    """Assembled view of all macro indicators used by the scoring functions."""

    # Growth
    gdp_yoy: Optional[float] = None
    """% YoY change in real GDP (e.g. 2.8)."""

    ism_svc: Optional[float] = None
    """ISM Services PMI level (50-centered; >50 = expansion).
    Manufacturing PMI slot is empty — ISM removed NAPM from FRED; no valid proxy available."""

    jobless_claims: Optional[float] = None
    """Initial jobless claims — actual count, NOT thousands (e.g. 220000)."""

    payrolls_level: Optional[float] = None
    """Latest nonfarm payrolls level in thousands (e.g. 158000)."""

    payrolls_mom_pct: Optional[float] = None
    """Payrolls MoM % change (e.g. 0.13)."""

    # Inflation
    cpi_yoy: Optional[float] = None
    """CPI all-items YoY % change."""

    core_cpi_yoy: Optional[float] = None
    """CPI less food and energy YoY % change."""

    ppi_yoy: Optional[float] = None
    """PPI all-commodities YoY % change."""

    pce_yoy: Optional[float] = None
    """PCE deflator YoY % change."""

    breakeven_5y: Optional[float] = None
    """5-year breakeven inflation rate level (e.g. 2.35)."""

    inflation_release_dates: Optional[dict[str, datetime]] = None
    """Optional per-series last-release dates for the five inflation indicators.

    Keys: ``"cpi"``, ``"core_cpi"``, ``"ppi"``, ``"pce"``, ``"breakeven_5y"``.
    When provided, ``_score_inflation`` uses confidence-weighted aggregation
    (PR 2). When None (default), all series are treated as equally fresh and
    the aggregation falls back to the unweighted mean from PR 1.

    Populated by ``build_raw_indicators()`` from ``FredBlock.last_release_dates``.
    Existing callers that construct ``RawIndicators`` manually (tests, etc.)
    receive ``None`` by default — no code changes needed.
    """

    # Fed / Rates
    rate_direction: float = 0.0
    """-1.0 to +1.0; positive = accommodative (from fred_fetcher)."""

    yield_curve_spread: Optional[float] = None
    """10Y minus 2Y Treasury yield spread in basis points."""

    # Stress
    hy_spread: Optional[float] = None
    """ICE BofA HY OAS spread in basis points (e.g. 320)."""

    vix: Optional[float] = None
    """CBOE VIX index level (e.g. 17.5)."""

    dxy: Optional[float] = None
    """US Dollar Index (DXY) level (e.g. 103.5)."""

    spx_pct_above_sma: Optional[float] = None
    """S&P 500 % above or below its 200-day SMA."""


@dataclass
class DimensionalScores:
    """Full output of the macro scoring pipeline."""

    growth_score: float
    """Economic growth signal, -1.0 (contraction) to +1.0 (expansion)."""

    inflation_score: float
    """Inflationary pressure, -1.0 (disinflationary) to +1.0 (high inflation)."""

    fed_score: float
    """Fed policy stance, -1.0 (tightening) to +1.0 (accommodative)."""

    stress_score: float
    """Market stress level, -1.0 (calm) to +1.0 (high stress)."""

    regime: str
    """Classified macro regime: Risk-On | Constructive | Risk-Off | Stagflation | Transitional."""

    regime_score: float
    """Overall macro health score, 0–100 (higher = better environment)."""

    regime_confidence: float
    """How clearly signals agree with the classified regime, 0–10."""


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_raw_indicators(fred: FredBlock, market: MarketBlock) -> RawIndicators:
    """Assemble a RawIndicators dataclass from fetcher output blocks.

    All fields are Optional — missing data from either block maps to None,
    never to a default numeric value that could bias downstream scoring.

    Parameters
    ----------
    fred:
        Populated FredBlock from fetch_fred_block().
    market:
        Populated MarketBlock from fetch_market_block().

    Returns
    -------
    RawIndicators
        Fully assembled indicator snapshot ready for scoring.
    """
    # ICSA (Initial Claims) is reported by FRED in actual headcount, not thousands.
    jobless_raw = fred.raw_values.get("jobless")
    jobless_actual: Optional[float] = float(jobless_raw) if jobless_raw is not None else None

    # Build inflation release dates from FredBlock (PR 2: mixed-frequency handling).
    # Map from scorer key → FredBlock series cache key so confidence decay works.
    _fred_key_map = {
        "cpi":          "cpi",
        "core_cpi":     "core_cpi",
        "ppi":          "ppi",
        "pce":          "pce",
        "breakeven_5y": "breakeven_5y",
    }
    inflation_release_dates: Optional[dict[str, datetime]] = None
    if fred.last_release_dates:
        dates: dict[str, datetime] = {}
        for scorer_key, fred_key in _fred_key_map.items():
            dt = fred.last_release_dates.get(fred_key)
            if dt is not None:
                dates[scorer_key] = dt
        if dates:
            inflation_release_dates = dates

    return RawIndicators(
        # Growth
        gdp_yoy=fred.yoy_changes.get("gdp"),
        ism_svc=fred.raw_values.get("ism_svc"),
        jobless_claims=jobless_actual,
        payrolls_level=fred.raw_values.get("payrolls"),
        payrolls_mom_pct=fred.mom_changes.get("payrolls"),
        # Inflation
        cpi_yoy=fred.yoy_changes.get("cpi"),
        core_cpi_yoy=fred.yoy_changes.get("core_cpi"),
        ppi_yoy=fred.yoy_changes.get("ppi"),
        pce_yoy=fred.yoy_changes.get("pce"),
        breakeven_5y=fred.raw_values.get("breakeven_5y"),
        # Fed / Rates
        rate_direction=fred.rate_direction,
        yield_curve_spread=fred.yield_curve_spread_bps,
        # Stress
        # BAMLH0A0HYM2 is in percentage points (e.g. 3.0 = 300 bps); convert to bps.
        hy_spread=float(fred.raw_values["hy_spread"] * 100) if fred.raw_values.get("hy_spread") is not None else None,
        vix=market.vix,
        dxy=market.dxy,
        spx_pct_above_sma=market.spx_pct_above_sma,
        inflation_release_dates=inflation_release_dates,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_avg(values: list) -> float:
    """Return the average of a list of non-None floats. Returns 0.0 if empty."""
    valid = [v for v in values if v is not None]
    if not valid:
        return 0.0
    return sum(valid) / len(valid)


# ---------------------------------------------------------------------------
# Dimensional Scorers
# ---------------------------------------------------------------------------


def _score_growth(ind: RawIndicators) -> float:
    """Score economic growth conditions on a -1.0 to +1.0 scale.

    Uses step-function thresholds for GDP, ISM PMI composite, payrolls MoM
    absolute change, and jobless claims. Returns the average of all non-None
    signals. Returns 0.0 if all signals are unavailable.

    Parameters
    ----------
    ind:
        Assembled RawIndicators snapshot.

    Returns
    -------
    float
        Growth score in [-1.0, +1.0].
    """
    signals: list[Optional[float]] = []

    # GDP YoY %
    # Thresholds: >2.5% strong expansion; 1-2.5% moderate; 0-1% stall speed;
    # -1.5 to 0% mild contraction (not yet recession); < -1.5% severe contraction.
    if ind.gdp_yoy is not None:
        g = ind.gdp_yoy
        if g > 2.5:
            signals.append(1.0)
        elif g >= 1.0:
            signals.append(0.5)
        elif g >= 0.0:
            signals.append(0.0)
        elif g >= -1.5:
            signals.append(-0.5)   # mild contraction — one negative quarter ≠ recession
        else:
            signals.append(-1.0)   # severe / confirmed contraction
        logger.debug("growth/gdp_yoy=%.2f → signal=%.1f", g, signals[-1])
    else:
        signals.append(None)

    # ISM Services PMI (50-centered standard PMI scale).
    # Mfg PMI slot is empty — no valid source available.
    if ind.ism_svc is not None:
        svc = ind.ism_svc
        if svc > 55:
            svc_signal: Optional[float] = 1.0
        elif svc >= 52:
            svc_signal = 0.5
        elif svc >= 50:
            svc_signal = 0.0
        elif svc >= 48:
            svc_signal = -0.5
        else:
            svc_signal = -1.0
        signals.append(svc_signal)
        logger.debug("growth/ism_svc=%.1f → signal=%.1f", svc, svc_signal)
    else:
        signals.append(None)

    # Payrolls absolute MoM change (in thousands)
    # payrolls_level is in thousands; payrolls_mom_pct is MoM % change
    if ind.payrolls_level is not None and ind.payrolls_mom_pct is not None:
        payrolls_mom_abs = (ind.payrolls_mom_pct / 100.0) * ind.payrolls_level  # thousands
        if payrolls_mom_abs > 200:
            p_signal: Optional[float] = 1.0
        elif payrolls_mom_abs >= 100:
            p_signal = 0.5
        elif payrolls_mom_abs >= 50:
            p_signal = 0.0
        elif payrolls_mom_abs >= 0:
            p_signal = -0.5
        else:
            p_signal = -1.0
        signals.append(p_signal)
        logger.debug(
            "growth/payrolls_mom_abs=%.1fK → signal=%.1f", payrolls_mom_abs, p_signal
        )
    else:
        signals.append(None)

    # Jobless claims (inverted — lower = better).
    # Thresholds calibrated to 2020s labor market: <200K = historically tight;
    # 200-240K = solid; 240-280K = neutral; 280-320K = softening; >320K = deteriorating.
    if ind.jobless_claims is not None:
        jc = ind.jobless_claims
        if jc < 200_000:
            j_signal: Optional[float] = 1.0
        elif jc < 240_000:
            j_signal = 0.5
        elif jc <= 280_000:
            j_signal = 0.0
        elif jc <= 320_000:
            j_signal = -0.5
        else:
            j_signal = -1.0
        signals.append(j_signal)
        logger.debug("growth/jobless_claims=%.0f → signal=%.1f", jc, j_signal)
    else:
        signals.append(None)

    score = _safe_avg(signals)
    logger.debug("_score_growth → %.4f", score)
    return score


def _score_inflation(ind: RawIndicators) -> float:
    """Score inflationary pressure on a -1.0 to +1.0 scale.

    Positive scores indicate elevated inflation. Each CPI/Core CPI/PPI/PCE
    series and the 5Y breakeven are scored independently via ``tanh_norm``
    against an economically anchored center (Fed target) and a series-specific
    scale.

    **PR 2 — mixed-frequency handling:** when ``ind.inflation_release_dates``
    is populated, the aggregation uses confidence-weighted averaging so that
    fresh daily series (breakeven_5y) outweigh stale monthly series between
    FRED releases. When dates are absent, falls back to the unweighted mean
    from PR 1 — no change for existing callers.

    Signature is preserved exactly: ``(ind: RawIndicators) -> float``.

    Parameters
    ----------
    ind:
        Assembled RawIndicators snapshot.

    Returns
    -------
    float
        Inflation score in [-1.0, +1.0].
    """
    # ── 1. Per-series tanh-normed signals ────────────────────────────────────
    # (key, value, config_params, native_frequency)
    _INDICATOR_SPECS = [
        ("cpi",          ind.cpi_yoy,        inflation_config.CPI_YOY,      "monthly"),
        ("core_cpi",     ind.core_cpi_yoy,   inflation_config.CORE_CPI_YOY, "monthly"),
        ("ppi",          ind.ppi_yoy,        inflation_config.PPI_YOY,      "monthly"),
        ("pce",          ind.pce_yoy,        inflation_config.PCE_YOY,      "monthly"),
        ("breakeven_5y", ind.breakeven_5y,   inflation_config.BREAKEVEN_5Y, "daily"),
    ]

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # Build (signal, confidence) pairs for each present indicator.
    scored: list[tuple[float, float]] = []   # (tanh_signal, confidence)

    for key, value, params, native_freq in _INDICATOR_SPECS:
        if value is None:
            continue
        signal = normalize.tanh_norm(value, params.center, params.scale)
        logger.debug("inflation/%s=%.2f → tanh_signal=%.3f", key, value, signal)

        # Compute confidence from release date if available.
        confidence: float = 1.0  # default: treat as fully fresh
        if ind.inflation_release_dates is not None:
            release_dt = ind.inflation_release_dates.get(key)
            if release_dt is not None:
                staleness = max(0, (today - release_dt).days)
                meta = IndicatorMeta(
                    series_id=key,
                    native_frequency=native_freq,
                    last_release_dt=release_dt,
                    staleness_days=staleness,
                )
                confidence = confidence_from_meta(meta)
                logger.debug(
                    "inflation/%s staleness=%d days → confidence=%.3f",
                    key, staleness, confidence,
                )
            else:
                # Series is in the block but release date is missing: use a
                # conservative fallback (assume one full native period has elapsed).
                fallback_staleness = FREQUENCY_DAYS[native_freq]
                confidence = staleness_confidence(fallback_staleness, native_freq)
                logger.debug(
                    "inflation/%s no release_date → fallback staleness=%d → confidence=%.3f",
                    key, fallback_staleness, confidence,
                )

        scored.append((signal, confidence))

    if not scored:
        logger.debug("_score_inflation → 0.0 (no signals)")
        return 0.0

    # ── 2. Confidence-weighted aggregation ───────────────────────────────────
    # sum(signal_i × conf_i) / sum(conf_i)
    # Edge case: all confidences are 0 → fall back to unweighted mean so the
    # function always returns a finite value.
    total_conf = sum(c for _, c in scored)
    if total_conf == 0.0:
        score = sum(s for s, _ in scored) / len(scored)
        logger.debug(
            "_score_inflation → %.4f (all-zero confidence fallback, unweighted mean)",
            score,
        )
    else:
        score = sum(s * c for s, c in scored) / total_conf
        logger.debug(
            "_score_inflation → %.4f (confidence-weighted, Σconf=%.3f)",
            score, total_conf,
        )

    return score


def _score_fed(ind: RawIndicators, fed_tone: float = 0.0) -> float:
    """Score Federal Reserve policy stance on a -1.0 to +1.0 scale.

    Positive = accommodative. Combines the mechanical rate_direction from
    fred_fetcher, yield curve shape, and an optional qualitative fed_tone
    overlay (from LLM analysis).

    Parameters
    ----------
    ind:
        Assembled RawIndicators snapshot.
    fed_tone:
        Qualitative tone overlay in [-1.0, +1.0]. Default 0.0 (neutral).
        Positive values indicate dovish language; negative indicate hawkish.

    Returns
    -------
    float
        Fed score in [-1.0, +1.0].
    """
    signals: list[Optional[float]] = []

    # rate_direction: already -1 to +1, pass through
    signals.append(ind.rate_direction)
    logger.debug("fed/rate_direction=%.2f → signal=%.2f", ind.rate_direction, ind.rate_direction)

    # Yield curve spread (bps)
    if ind.yield_curve_spread is not None:
        yc = ind.yield_curve_spread
        if yc > 100:
            s: Optional[float] = 1.0
        elif yc >= 50:
            s = 0.5
        elif yc >= 0:
            s = 0.0
        elif yc >= -50:
            s = -0.5
        else:
            s = -1.0
        signals.append(s)
        logger.debug("fed/yield_curve_spread=%.1f bps → signal=%.1f", yc, s)
    else:
        signals.append(None)

    # fed_tone qualitative overlay — pass through as-is
    signals.append(float(fed_tone))
    logger.debug("fed/fed_tone=%.2f → signal=%.2f", fed_tone, fed_tone)

    score = _safe_avg(signals)
    logger.debug("_score_fed → %.4f", score)
    return score


def _score_stress(ind: RawIndicators) -> float:
    """Score market stress conditions on a -1.0 to +1.0 scale.

    Positive = high stress. Aggregates VIX, HY credit spreads, DXY strength,
    and SPX deviation from its 200-day SMA.

    Parameters
    ----------
    ind:
        Assembled RawIndicators snapshot.

    Returns
    -------
    float
        Stress score in [-1.0, +1.0].
    """
    vix_score: Optional[float] = None
    hy_score: Optional[float] = None
    dxy_score: Optional[float] = None
    spx_score: Optional[float] = None

    # VIX — primary stress indicator (weight 1.0)
    if ind.vix is not None:
        vix = ind.vix
        if vix > 30:
            s: Optional[float] = 1.0
        elif vix >= 20:
            s = 0.5
        elif vix >= 15:
            s = 0.0
        elif vix >= 12:
            s = -0.5   # calm
        else:
            s = -1.0   # extreme complacency (historically precedes vol spikes)
        vix_score = s
        logger.debug("stress/vix=%.2f → signal=%.1f", vix, s)

    # HY Spread (bps OAS — ICE BofA BAMLH0A0HYM2, already converted to bps in build_raw_indicators).
    # Thresholds: >600 crisis; 450-600 stressed; 300-450 normal; 200-300 tight; <200 boom-time.
    # Primary stress indicator (weight 1.0).
    if ind.hy_spread is not None:
        hy = ind.hy_spread
        if hy > 600:
            s = 1.0
        elif hy >= 450:
            s = 0.5
        elif hy >= 300:
            s = 0.0
        elif hy >= 200:
            s = -0.5   # tight credit — benign but historically precedes mean reversion
        else:
            s = -1.0   # extremely tight / boom-time credit
        hy_score = s
        logger.debug("stress/hy_spread=%.1f bps → signal=%.1f", hy, s)

    # DXY level — secondary indicator (weight 0.5); capped at ±0.5 by step function design.
    if ind.dxy is not None:
        dxy = ind.dxy
        if dxy > 108:
            s = 0.5
        elif dxy >= 100:
            s = 0.0
        else:
            s = -0.5
        dxy_score = s
        logger.debug("stress/dxy=%.2f → signal=%.1f", dxy, s)

    # SPX vs 200-day SMA — secondary indicator (weight 0.5).
    if ind.spx_pct_above_sma is not None:
        pct = ind.spx_pct_above_sma
        if pct < -5.0:
            s = 1.0
        elif pct < -2.0:
            s = 0.5
        elif pct <= 2.0:
            s = 0.0
        else:
            s = -0.5
        spx_score = s
        logger.debug("stress/spx_pct_above_sma=%.2f%% → signal=%.1f", pct, s)

    # Weighted aggregation: VIX and HY are primary (w=1.0); DXY and SPX are secondary (w=0.5).
    # Denominator is the sum of weights for present (non-None) indicators, so missing data
    # degrades gracefully without distorting the average.
    present = [
        (vix_score, 1.0),
        (hy_score, 1.0),
        (dxy_score, 0.5),
        (spx_score, 0.5),
    ]
    weighted_sum = sum(v * w for v, w in present if v is not None)
    weight_sum = sum(w for v, w in present if v is not None)
    score = weighted_sum / weight_sum if weight_sum > 0 else 0.0
    logger.debug("_score_stress → %.4f", score)
    return score


# ---------------------------------------------------------------------------
# Regime Classification
# ---------------------------------------------------------------------------


def classify_regime(growth: float, inflation: float, fed: float, stress: float) -> str:
    """Classify the macro regime from dimensional scores.

    Checks in strict priority order: Risk-On, Risk-Off, Stagflation, Constructive,
    Transitional. Risk-Off is evaluated before Stagflation because acute stress
    always overrides stagflationary concerns for positioning purposes.
    Constructive sits between Risk-On and Transitional — growth is positive and
    stress is contained, but inflation runs ≥0.5 (the only block to full Risk-On).

    Parameters
    ----------
    growth:
        Growth score from _score_growth().
    inflation:
        Inflation score from _score_inflation().
    fed:
        Fed policy score from _score_fed().
    stress:
        Market stress score from _score_stress().

    Returns
    -------
    str
        One of: "Risk-On", "Constructive", "Risk-Off", "Stagflation", "Transitional".
    """
    if growth > 0 and inflation < 0.5 and stress < 0.3:
        return "Risk-On"
    if stress > 0.4 or (growth < -0.3 and fed < 0):
        return "Risk-Off"
    if growth < 0 and inflation > 0.6:
        return "Stagflation"
    # Constructive: growth positive, stress contained, but inflation blocks Risk-On.
    # Distinguishes "OK environment with sticky inflation" from genuine signal uncertainty.
    if growth > 0 and stress < 0.3:
        return "Constructive"
    return "Transitional"


def compute_regime_confidence(
    growth: float,
    inflation: float,
    fed: float,
    stress: float,
    regime: str,
) -> float:
    """Measure how clearly the dimensional signals agree with the classified regime.

    Returns a score from 0 to 10. Higher values indicate unambiguous alignment
    between the data and the regime label. Clamped to [0.0, 10.0].

    Parameters
    ----------
    growth:
        Growth score from _score_growth().
    inflation:
        Inflation score from _score_inflation().
    fed:
        Fed policy score from _score_fed().
    stress:
        Stress score from _score_stress().
    regime:
        Classified regime string from classify_regime().

    Returns
    -------
    float
        Confidence score in [0.0, 10.0].
    """
    score = 0.0

    if regime == "Risk-On":
        if growth > 0.3:
            score += 2.5
        if inflation < 0.3:
            score += 2.5
        if stress < 0.2:
            score += 2.5
        if fed > 0:
            score += 2.5

    elif regime == "Risk-Off":
        if stress > 0.5:
            score += 4.0
        if growth < -0.3:
            score += 3.0
        if fed < 0:
            score += 3.0

    elif regime == "Stagflation":
        if growth < 0:
            score += 3.0
        if inflation > 0.6:
            score += 4.0
        if stress > 0.3:
            score += 3.0

    elif regime == "Constructive":
        if growth > 0.3:
            score += 3.0
        if stress < 0.2:
            score += 3.0
        if inflation < 0.7:
            score += 2.0
        if fed > -0.2:
            score += 2.0

    elif regime == "Transitional":
        # Fallback only — macro_agent overrides this with the LLM's signal-clarity assessment
        score = 5.0

    result = max(0.0, min(10.0, score))
    logger.debug("compute_regime_confidence(regime=%s) → %.2f", regime, result)
    return result


def compute_regime_score(
    growth: float,
    inflation: float,
    fed: float,
    stress: float,
    regime: str,  # noqa: ARG001 — reserved for future regime-specific adjustments
) -> float:
    """Compute a 0–100 macro health score where higher = better environment.

    Weights: Growth 35%, Low-inflation 30%, Accommodative Fed 20%, Low-stress 15%.
    Each dimension is normalized from [-1, +1] to [0, 1] before weighting.
    Result is clamped to [0.0, 100.0].

    Parameters
    ----------
    growth:
        Growth score from _score_growth().
    inflation:
        Inflation score from _score_inflation().
    fed:
        Fed policy score from _score_fed().
    stress:
        Stress score from _score_stress().
    regime:
        Classified regime string (reserved for future adjustments).

    Returns
    -------
    float
        Regime health score in [0.0, 100.0].
    """
    # Each dimension mapped from [-1, +1] to [0, 1]. All step functions must use the
    # full [-1, +1] range for this normalization to be correct:
    #   growth_contrib    = (growth + 1) / 2 * 0.35   — higher expansion = better
    #   inflation_contrib = (1 - inflation) / 2 * 0.30 — lower inflation = better
    #   fed_contrib       = (fed + 1) / 2 * 0.20      — more accommodative = better
    #   stress_contrib    = (1 - stress) / 2 * 0.15   — lower stress = better
    # Note: DXY [-0.5, +0.5] and SPX [-0.5, +1.0] intentionally don't reach ±1;
    # their avg contribution to stress_score keeps stress within roughly [-0.75, +0.875]
    # in practice, which is acceptable for a secondary-weight (15%) dimension.
    growth_contrib    = (growth + 1) / 2 * 0.35
    inflation_contrib = (1 - inflation) / 2 * 0.30
    fed_contrib       = (fed + 1) / 2 * 0.20
    stress_contrib    = (1 - stress) / 2 * 0.15
    health_raw = growth_contrib + inflation_contrib + fed_contrib + stress_contrib
    result = max(0.0, min(100.0, health_raw * 100))
    logger.debug(
        "regime_score: "
        "growth=%.3f→%+.1fpts(35%%) "
        "inflation=%.3f→%+.1fpts(30%%) "
        "fed=%.3f→%+.1fpts(20%%) "
        "stress=%.3f→%+.1fpts(15%%) "
        "raw_sum=%.4f → score=%.1f",
        growth,    growth_contrib * 100,
        inflation, inflation_contrib * 100,
        fed,       fed_contrib * 100,
        stress,    stress_contrib * 100,
        health_raw,
        result,
    )
    return result


# ---------------------------------------------------------------------------
# Indicator List Builder
# ---------------------------------------------------------------------------


def build_indicator_scores(ind: RawIndicators) -> list[dict]:
    """Build a human-readable list of per-indicator signal assessments.

    Returns one dict per available indicator with keys:
      - name  (str)
      - value (float)
      - signal ("bullish" | "neutral" | "bearish")
      - note  (str | None)

    Indicators with None value are omitted. The signal mapping uses the same
    step-function thresholds as the dimensional scoring functions.

    Parameters
    ----------
    ind:
        Assembled RawIndicators snapshot.

    Returns
    -------
    list[dict]
        One entry per non-None indicator, in a consistent display order.
    """
    result: list[dict] = []

    def _signal(score: float) -> str:
        """For growth and Fed indicators: high score = bullish."""
        if score > 0:
            return "bullish"
        elif score < 0:
            return "bearish"
        return "neutral"

    def _inv_signal(score: float) -> str:
        """For inflation and stress indicators: high score = bearish (bad for portfolio)."""
        if score > 0:
            return "bearish"
        elif score < 0:
            return "bullish"
        return "neutral"

    # GDP YoY
    if ind.gdp_yoy is not None:
        g = ind.gdp_yoy
        if g > 2.5:
            s = 1.0
        elif g >= 1.0:
            s = 0.5
        elif g >= 0.0:
            s = 0.0
        elif g >= -1.5:
            s = -0.5
        else:
            s = -1.0
        result.append({
            "name": "GDP YoY",
            "value": g,
            "signal": _signal(s),
            "note": f"{g:.1f}% annual growth",
        })

    # ISM Services PMI (50-centered)
    if ind.ism_svc is not None:
        v = ind.ism_svc
        if v > 55:
            s = 1.0
        elif v >= 52:
            s = 0.5
        elif v >= 50:
            s = 0.0
        elif v >= 48:
            s = -0.5
        else:
            s = -1.0
        result.append({
            "name": "ISM Svc PMI",
            "value": v,
            "signal": _signal(s),
            "note": "above 50 = expansion" if v >= 50 else "below 50 = contraction",
        })

    # Jobless Claims
    if ind.jobless_claims is not None:
        jc = ind.jobless_claims
        if jc < 200_000:
            s = 1.0
        elif jc < 240_000:
            s = 0.5
        elif jc <= 280_000:
            s = 0.0
        elif jc <= 320_000:
            s = -0.5
        else:
            s = -1.0
        result.append({
            "name": "Jobless Claims",
            "value": jc,
            "signal": _signal(s),
            "note": f"{int(jc):,} weekly initial claims",
        })

    # Payrolls MoM absolute change
    if ind.payrolls_level is not None and ind.payrolls_mom_pct is not None:
        payrolls_mom_abs = (ind.payrolls_mom_pct / 100.0) * ind.payrolls_level
        if payrolls_mom_abs > 200:
            s = 1.0
        elif payrolls_mom_abs >= 100:
            s = 0.5
        elif payrolls_mom_abs >= 50:
            s = 0.0
        elif payrolls_mom_abs >= 0:
            s = -0.5
        else:
            s = -1.0
        result.append({
            "name": "Payrolls MoM",
            "value": round(payrolls_mom_abs, 1),
            "signal": _signal(s),
            "note": f"{payrolls_mom_abs:+.0f}K jobs added",
        })

    # CPI YoY — high inflation = bearish for portfolio
    if ind.cpi_yoy is not None:
        v = ind.cpi_yoy
        if v > 5:
            s = 1.0
        elif v >= 3:
            s = 0.5
        elif v >= 2:
            s = 0.0
        elif v >= 1:
            s = -0.5
        else:
            s = -1.0
        result.append({
            "name": "CPI YoY",
            "value": v,
            "signal": _inv_signal(s),
            "note": f"{v:.1f}% vs 2% Fed target",
        })

    # Core CPI YoY — high inflation = bearish
    if ind.core_cpi_yoy is not None:
        v = ind.core_cpi_yoy
        if v > 5:
            s = 1.0
        elif v >= 3:
            s = 0.5
        elif v >= 2:
            s = 0.0
        elif v >= 1:
            s = -0.5
        else:
            s = -1.0
        result.append({
            "name": "Core CPI YoY",
            "value": v,
            "signal": _inv_signal(s),
            "note": f"{v:.1f}% ex-food & energy",
        })

    # PPI YoY — high inflation = bearish
    if ind.ppi_yoy is not None:
        v = ind.ppi_yoy
        if v > 6:
            s = 1.0
        elif v >= 3:
            s = 0.5
        elif v >= 1:
            s = 0.0
        elif v >= 0:
            s = -0.5
        else:
            s = -1.0
        result.append({
            "name": "PPI YoY",
            "value": v,
            "signal": _inv_signal(s),
            "note": f"{v:.1f}% producer price inflation",
        })

    # PCE YoY — high inflation = bearish
    if ind.pce_yoy is not None:
        v = ind.pce_yoy
        if v > 4:
            s = 1.0
        elif v >= 2.5:
            s = 0.5
        elif v >= 2.0:
            s = 0.0
        elif v >= 1.5:
            s = -0.5
        else:
            s = -1.0
        result.append({
            "name": "PCE YoY",
            "value": v,
            "signal": _inv_signal(s),
            "note": f"{v:.1f}% Fed's preferred inflation gauge",
        })

    # 5Y Breakeven — elevated expectations = bearish
    if ind.breakeven_5y is not None:
        v = ind.breakeven_5y
        if v > 3.0:
            s = 1.0
        elif v >= 2.5:
            s = 0.5
        elif v >= 2.0:
            s = 0.0
        elif v >= 1.5:
            s = -0.5
        else:
            s = -1.0
        result.append({
            "name": "5Y Breakeven",
            "value": v,
            "signal": _inv_signal(s),
            "note": f"{v:.2f}% market inflation expectation",
        })

    # VIX — high stress = bearish
    if ind.vix is not None:
        v = ind.vix
        if v > 30:
            s = 1.0
        elif v >= 20:
            s = 0.5
        elif v >= 15:
            s = 0.0
        elif v >= 12:
            s = -0.5
        else:
            s = -1.0
        result.append({
            "name": "VIX",
            "value": v,
            "signal": _inv_signal(s),
            "note": f"{v:.1f} — {'fear elevated' if v > 20 else 'extreme complacency' if v < 12 else 'complacent' if v < 15 else 'normal range'}",
        })

    # HY Spread — wide spread = bearish
    if ind.hy_spread is not None:
        v = ind.hy_spread
        if v > 600:
            s = 1.0
        elif v >= 450:
            s = 0.5
        elif v >= 300:
            s = 0.0
        elif v >= 200:
            s = -0.5
        else:
            s = -1.0
        result.append({
            "name": "HY Spread",
            "value": v,
            "signal": _inv_signal(s),
            "note": f"{v:.0f} bps OAS",
        })

    # DXY — strong dollar = mild stress = bearish
    if ind.dxy is not None:
        v = ind.dxy
        if v > 108:
            s = 0.5
        elif v >= 100:
            s = 0.0
        else:
            s = -0.5
        result.append({
            "name": "DXY",
            "value": v,
            "signal": _inv_signal(s),
            "note": f"{v:.1f} — {'strong dollar' if v > 104 else 'weak dollar' if v < 100 else 'neutral'}",
        })

    # Yield Curve Spread
    if ind.yield_curve_spread is not None:
        v = ind.yield_curve_spread
        if v > 100:
            s = 1.0
        elif v >= 50:
            s = 0.5
        elif v >= 0:
            s = 0.0
        elif v >= -50:
            s = -0.5
        else:
            s = -1.0
        result.append({
            "name": "Yield Curve Spread",
            "value": v,
            "signal": _signal(s),
            "note": f"{v:+.0f} bps (10Y-2Y){'  — INVERTED' if v < 0 else ''}",
        })

    return result


# ---------------------------------------------------------------------------
# Top-Level Entry Point
# ---------------------------------------------------------------------------


def score_indicators(ind: RawIndicators, fed_tone: float = 0.0) -> DimensionalScores:
    """Score all macro indicators and produce a complete DimensionalScores output.

    This is the primary entry point for the Macro Agent. Calls each dimensional
    scorer, classifies the regime, and computes confidence and health scores.

    Parameters
    ----------
    ind:
        Assembled RawIndicators snapshot (use build_raw_indicators() to create).
    fed_tone:
        Optional qualitative Fed tone overlay in [-1.0, +1.0], typically
        provided by an LLM analysis of FOMC minutes or Fed speeches.
        Defaults to 0.0 (neutral) when no qualitative analysis is available.

    Returns
    -------
    DimensionalScores
        Fully populated dimensional scores, regime classification, health score,
        and confidence level.
    """
    growth = _score_growth(ind)
    inflation = _score_inflation(ind)
    fed = _score_fed(ind, fed_tone=fed_tone)
    stress = _score_stress(ind)

    regime = classify_regime(growth, inflation, fed, stress)
    confidence = compute_regime_confidence(growth, inflation, fed, stress, regime)
    regime_score = compute_regime_score(growth, inflation, fed, stress, regime)

    logger.info(
        "score_indicators: growth=%.3f inflation=%.3f fed=%.3f stress=%.3f "
        "→ regime=%s score=%.1f confidence=%.1f",
        growth, inflation, fed, stress, regime, regime_score, confidence,
    )

    return DimensionalScores(
        growth_score=growth,
        inflation_score=inflation,
        fed_score=fed,
        stress_score=stress,
        regime=regime,
        regime_score=regime_score,
        regime_confidence=confidence,
    )
