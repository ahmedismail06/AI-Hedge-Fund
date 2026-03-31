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
"""

from dotenv import load_dotenv

load_dotenv()

import logging
from dataclasses import dataclass
from typing import Optional

from backend.macro.indicators.fred_fetcher import FredBlock
from backend.macro.indicators.market_fetcher import MarketBlock

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

    ism_mfg: Optional[float] = None
    """ISM Manufacturing PMI level (e.g. 52.3)."""

    ism_svc: Optional[float] = None
    """ISM Services PMI level (e.g. 54.1)."""

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
    """Classified macro regime: Risk-On | Risk-Off | Stagflation | Transitional."""

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

    return RawIndicators(
        # Growth
        gdp_yoy=fred.yoy_changes.get("gdp"),
        ism_mfg=fred.raw_values.get("ism_mfg"),
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
    if ind.gdp_yoy is not None:
        g = ind.gdp_yoy
        if g > 2.5:
            signals.append(1.0)
        elif g >= 1.0:
            signals.append(0.5)
        elif g >= 0.0:
            signals.append(0.0)
        else:
            signals.append(-1.0)
        logger.debug("growth/gdp_yoy=%.2f → signal=%.1f", g, signals[-1])
    else:
        signals.append(None)

    # ISM composite (avg of mfg + svc if both present, else whichever exists)
    ism_values = [v for v in [ind.ism_mfg, ind.ism_svc] if v is not None]
    if ism_values:
        ism_avg = sum(ism_values) / len(ism_values)
        if ism_avg > 55:
            ism_signal: Optional[float] = 1.0
        elif ism_avg >= 52:
            ism_signal = 0.5
        elif ism_avg >= 50:
            ism_signal = 0.0
        elif ism_avg >= 48:
            ism_signal = -0.5
        else:
            ism_signal = -1.0
        signals.append(ism_signal)
        logger.debug("growth/ism_avg=%.2f → signal=%.1f", ism_avg, ism_signal)
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

    # Jobless claims (inverted — lower = better)
    if ind.jobless_claims is not None:
        jc = ind.jobless_claims
        if jc < 220_000:
            j_signal: Optional[float] = 1.0
        elif jc <= 260_000:
            j_signal = 0.0
        elif jc <= 300_000:
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

    Positive scores indicate elevated inflation. Each CPI/PPI/PCE series and the
    5Y breakeven are scored independently then averaged over non-None signals.

    Parameters
    ----------
    ind:
        Assembled RawIndicators snapshot.

    Returns
    -------
    float
        Inflation score in [-1.0, +1.0].
    """
    signals: list[Optional[float]] = []

    def _cpi_like(value: float) -> float:
        """Step function for CPI and Core CPI YoY %."""
        if value > 5:
            return 1.0
        elif value >= 3:
            return 0.5
        elif value >= 2:
            return 0.0
        else:
            return -0.5

    # CPI YoY
    if ind.cpi_yoy is not None:
        s = _cpi_like(ind.cpi_yoy)
        signals.append(s)
        logger.debug("inflation/cpi_yoy=%.2f → signal=%.1f", ind.cpi_yoy, s)
    else:
        signals.append(None)

    # Core CPI YoY
    if ind.core_cpi_yoy is not None:
        s = _cpi_like(ind.core_cpi_yoy)
        signals.append(s)
        logger.debug("inflation/core_cpi_yoy=%.2f → signal=%.1f", ind.core_cpi_yoy, s)
    else:
        signals.append(None)

    # PPI YoY
    if ind.ppi_yoy is not None:
        ppi = ind.ppi_yoy
        if ppi > 6:
            s = 1.0
        elif ppi >= 3:
            s = 0.5
        elif ppi >= 1:
            s = 0.0
        else:
            s = -0.5
        signals.append(s)
        logger.debug("inflation/ppi_yoy=%.2f → signal=%.1f", ppi, s)
    else:
        signals.append(None)

    # PCE YoY
    if ind.pce_yoy is not None:
        pce = ind.pce_yoy
        if pce > 4:
            s = 1.0
        elif pce >= 2.5:
            s = 0.5
        elif pce >= 2.0:
            s = 0.0
        else:
            s = -0.5
        signals.append(s)
        logger.debug("inflation/pce_yoy=%.2f → signal=%.1f", pce, s)
    else:
        signals.append(None)

    # 5Y Breakeven level
    if ind.breakeven_5y is not None:
        be = ind.breakeven_5y
        if be > 3.0:
            s = 1.0
        elif be >= 2.5:
            s = 0.5
        elif be >= 2.0:
            s = 0.0
        else:
            s = -0.5
        signals.append(s)
        logger.debug("inflation/breakeven_5y=%.2f → signal=%.1f", be, s)
    else:
        signals.append(None)

    score = _safe_avg(signals)
    logger.debug("_score_inflation → %.4f", score)
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
    signals: list[Optional[float]] = []

    # VIX
    if ind.vix is not None:
        vix = ind.vix
        if vix > 30:
            s: Optional[float] = 1.0
        elif vix >= 20:
            s = 0.5
        elif vix >= 15:
            s = 0.0
        else:
            s = -0.5
        signals.append(s)
        logger.debug("stress/vix=%.2f → signal=%.1f", vix, s)
    else:
        signals.append(None)

    # HY Spread (bps OAS)
    if ind.hy_spread is not None:
        hy = ind.hy_spread
        if hy > 600:
            s = 1.0
        elif hy >= 400:
            s = 0.5
        elif hy >= 250:
            s = 0.0
        else:
            s = -0.5
        signals.append(s)
        logger.debug("stress/hy_spread=%.1f bps → signal=%.1f", hy, s)
    else:
        signals.append(None)

    # DXY level
    if ind.dxy is not None:
        dxy = ind.dxy
        if dxy > 108:
            s = 0.5
        elif dxy >= 100:
            s = 0.0
        else:
            s = -0.5
        signals.append(s)
        logger.debug("stress/dxy=%.2f → signal=%.1f", dxy, s)
    else:
        signals.append(None)

    # SPX vs 200-day SMA
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
        signals.append(s)
        logger.debug("stress/spx_pct_above_sma=%.2f%% → signal=%.1f", pct, s)
    else:
        signals.append(None)

    score = _safe_avg(signals)
    logger.debug("_score_stress → %.4f", score)
    return score


# ---------------------------------------------------------------------------
# Regime Classification
# ---------------------------------------------------------------------------


def classify_regime(growth: float, inflation: float, fed: float, stress: float) -> str:
    """Classify the macro regime from dimensional scores.

    Checks in strict priority order: Risk-On, Risk-Off, Stagflation, Transitional.
    Risk-Off is evaluated before Stagflation because acute stress always overrides
    stagflationary concerns for positioning purposes.

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
        One of: "Risk-On", "Risk-Off", "Stagflation", "Transitional".
    """
    if growth > 0 and inflation < 0.5 and stress < 0.3:
        return "Risk-On"
    if stress > 0.6 or (growth < -0.3 and fed < 0):
        return "Risk-Off"
    if growth < 0 and inflation > 0.6:
        return "Stagflation"
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

    elif regime == "Transitional":
        # Always uncertain by definition
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
    health_raw = (
        (growth + 1) / 2 * 0.35        # growth: positive = good
        + (1 - inflation) / 2 * 0.30   # low inflation = good
        + (fed + 1) / 2 * 0.20         # accommodative = good
        + (1 - stress) / 2 * 0.15      # low stress = good
    )
    result = max(0.0, min(100.0, health_raw * 100))
    logger.debug("compute_regime_score → %.2f", result)
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
        if score > 0:
            return "bullish"
        elif score < 0:
            return "bearish"
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
        else:
            s = -1.0
        result.append({
            "name": "GDP YoY",
            "value": g,
            "signal": _signal(s),
            "note": f"{g:.1f}% annual growth",
        })

    # ISM Mfg PMI
    if ind.ism_mfg is not None:
        v = ind.ism_mfg
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
            "name": "ISM Mfg PMI (Philly proxy)",
            "value": v,
            "signal": _signal(s),
            "note": "above 50 = expansion" if v >= 50 else "below 50 = contraction",
        })

    # ISM Svc PMI
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
        if jc < 220_000:
            s = 1.0
        elif jc <= 260_000:
            s = 0.0
        elif jc <= 300_000:
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

    # CPI YoY
    if ind.cpi_yoy is not None:
        v = ind.cpi_yoy
        if v > 5:
            s = 1.0
        elif v >= 3:
            s = 0.5
        elif v >= 2:
            s = 0.0
        else:
            s = -0.5
        # For inflation indicators, bullish/bearish is inverted from the inflation score
        # (high inflation score = bearish for equities). We reflect the inflation signal
        # directly so the dashboard shows economic signal, not equity signal.
        result.append({
            "name": "CPI YoY",
            "value": v,
            "signal": _signal(s),
            "note": f"{v:.1f}% vs 2% Fed target",
        })

    # Core CPI YoY
    if ind.core_cpi_yoy is not None:
        v = ind.core_cpi_yoy
        if v > 5:
            s = 1.0
        elif v >= 3:
            s = 0.5
        elif v >= 2:
            s = 0.0
        else:
            s = -0.5
        result.append({
            "name": "Core CPI YoY",
            "value": v,
            "signal": _signal(s),
            "note": f"{v:.1f}% ex-food & energy",
        })

    # PPI YoY
    if ind.ppi_yoy is not None:
        v = ind.ppi_yoy
        if v > 6:
            s = 1.0
        elif v >= 3:
            s = 0.5
        elif v >= 1:
            s = 0.0
        else:
            s = -0.5
        result.append({
            "name": "PPI YoY",
            "value": v,
            "signal": _signal(s),
            "note": f"{v:.1f}% producer price inflation",
        })

    # PCE YoY
    if ind.pce_yoy is not None:
        v = ind.pce_yoy
        if v > 4:
            s = 1.0
        elif v >= 2.5:
            s = 0.5
        elif v >= 2.0:
            s = 0.0
        else:
            s = -0.5
        result.append({
            "name": "PCE YoY",
            "value": v,
            "signal": _signal(s),
            "note": f"{v:.1f}% Fed's preferred inflation gauge",
        })

    # 5Y Breakeven
    if ind.breakeven_5y is not None:
        v = ind.breakeven_5y
        if v > 3.0:
            s = 1.0
        elif v >= 2.5:
            s = 0.5
        elif v >= 2.0:
            s = 0.0
        else:
            s = -0.5
        result.append({
            "name": "5Y Breakeven",
            "value": v,
            "signal": _signal(s),
            "note": f"{v:.2f}% market inflation expectation",
        })

    # VIX
    if ind.vix is not None:
        v = ind.vix
        if v > 30:
            s = 1.0
        elif v >= 20:
            s = 0.5
        elif v >= 15:
            s = 0.0
        else:
            s = -0.5
        result.append({
            "name": "VIX",
            "value": v,
            "signal": _signal(s),
            "note": f"{v:.1f} — {'fear elevated' if v > 20 else 'complacent' if v < 15 else 'normal range'}",
        })

    # HY Spread
    if ind.hy_spread is not None:
        v = ind.hy_spread
        if v > 600:
            s = 1.0
        elif v >= 400:
            s = 0.5
        elif v >= 250:
            s = 0.0
        else:
            s = -0.5
        result.append({
            "name": "HY Spread",
            "value": v,
            "signal": _signal(s),
            "note": f"{v:.0f} bps OAS",
        })

    # DXY
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
            "signal": _signal(s),
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
