"""
Composite Scorer — combines factor scores into a single composite (0–10).

Pipeline:
  1. Filter EXCLUDED tickers (Beneish hard gate) — set score to 0.0, keep for audit
  2. Normalize raw factor values to 0–10 via percentile rank
     - Quality + Momentum: universe-wide normalization
     - Value: sector-relative normalization (within SaaS / Healthcare / Industrials)
  3. Compute weighted sub-scores per factor
  4. Compute composite = Quality×w + Value×w + Momentum×w (regime-adjusted weights)
  5. Apply discrete adjustments: FLAGGED penalty, insider bonus, regime caps
  6. Sort descending, assign rank
  7. Return all ScreenerResult objects (caller filters ≥ 6.5)

Regime weights:
  Risk-On:      Quality 50%, Value 30%, Momentum 20%
  Transitional: Quality 55%, Value 30%, Momentum 15%
  Risk-Off:     Quality 60%, Value 30%, Momentum 10%
  Stagflation:  Quality 55%, Value 35%, Momentum 10%
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from backend.screener.universe import UniverseCandidate

logger = logging.getLogger(__name__)

# ── Regime weight tables ──────────────────────────────────────────────────────

_REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    "Risk-On":      {"quality": 0.50, "value": 0.30, "momentum": 0.20},
    "Transitional": {"quality": 0.55, "value": 0.30, "momentum": 0.15},
    "Risk-Off":     {"quality": 0.60, "value": 0.30, "momentum": 0.10},
    "Stagflation":  {"quality": 0.55, "value": 0.35, "momentum": 0.10},
}
_DEFAULT_REGIME = "Risk-On"

# Quality sub-metric weights (must sum to 1.0)
# eps_beat_rate removed; weight redistributed to remaining metrics
_QUALITY_SUB_WEIGHTS = {
    "gross_margin":       0.275,
    "revenue_growth_yoy": 0.25,
    "roe":                0.225,
    "debt_to_equity":     0.25,  # inverted (lower = better)
    # eps_beat_rate retained in raw_values output but excluded from score computation
}

# Value sub-metric weights (must sum to 1.0)
_VALUE_SUB_WEIGHTS = {
    "ev_multiple": 0.40,  # inverted
    "p_fcf":       0.30,  # inverted
    "price_book":  0.30,  # inverted
}

# Momentum sub-metric weights (must sum to 1.0)
_MOMENTUM_SUB_WEIGHTS = {
    "price_12_1":   0.35,
    "price_6_1":    0.35,
    "eps_revision": 0.30,
}


@dataclass
class ScreenerResult:
    ticker: str
    composite_score: float
    quality_score: float
    value_score: float
    momentum_score: float
    rank: int = 0
    sector: Optional[str] = None
    market_cap_m: Optional[float] = None
    adv_k: Optional[float] = None
    beneish_m_score: Optional[float] = None
    beneish_flag: Optional[str] = None
    insider_signal: bool = False
    raw_factors: dict = field(default_factory=dict)
    excluded: bool = False  # True for Beneish EXCLUDED; still written to watchlist for audit
    queued_for_research: bool = False


# ── Normalization ─────────────────────────────────────────────────────────────

def _normalize_universe(
    values: dict[str, Optional[float]],
    higher_is_better: bool = True,
) -> dict[str, Optional[float]]:
    """
    Percentile-rank values to 0–10 using average-rank normalization for ties.
    None values → None (excluded from average in _compute_factor_score).
    All identical → 5.0.

    Tied raw values receive the same normalized score (average of their ranks),
    which is the standard statistical percentile-rank approach.

    Args:
        values: {ticker: raw_value | None}
        higher_is_better: If False, lower raw values get higher normalized scores.

    Returns:
        {ticker: normalized_score_0_to_10 | None}
    """
    import math
    valid = {t: v for t, v in values.items() if v is not None and not math.isnan(v)}

    if not valid:
        return {t: None for t in values}

    if len(valid) < 2 or len(set(valid.values())) == 1:
        # Not enough variation to rank → neutral 5.0 for those with data
        return {t: (5.0 if t in valid else None) for t in values}

    sorted_vals = sorted(valid.values())
    n = len(sorted_vals)

    # Map each unique value → average rank of all its occurrences → score 0–10
    val_to_score: dict[float, float] = {}
    for unique_val in set(sorted_vals):
        positions = [i for i, v in enumerate(sorted_vals) if v == unique_val]
        avg_rank = sum(positions) / len(positions)
        # Scale to 0-10
        if n > 1:
            pct = avg_rank / (n - 1)
        else:
            pct = 0.5
        score = pct * 10.0
        if not higher_is_better:
            score = 10.0 - score
        val_to_score[unique_val] = round(score, 3)

    return {
        t: val_to_score[valid[t]] if t in valid else None
        for t in values
    }


def _compute_factor_score(
    normalized_sub: dict[str, float],  # {sub_metric: normalized_0_to_10}
    weights: dict[str, float],
) -> float:
    """Weighted average of normalized sub-scores. Excludes metrics with no data."""
    total_w = 0.0
    total   = 0.0
    for metric, w in weights.items():
        score = normalized_sub.get(metric)
        if score is not None:
            total   += score * w
            total_w += w
    
    # If no data at all for this factor group, return neutral 5.0
    if total_w == 0:
        return 5.0
        
    return round(total / total_w, 3)


# ── Main composite function ───────────────────────────────────────────────────

def compute_composite(
    universe: list[UniverseCandidate],
    raw_factor_results: dict[str, dict],
    regime: str,
) -> list[ScreenerResult]:
    """
    Compute composite scores for the full universe.

    Args:
        universe: List of UniverseCandidate (from build_universe()).
        raw_factor_results: {ticker → {quality, value, momentum, beneish, form4}}
            - quality:  output of score_quality()
            - value:    output of score_value()
            - momentum: output of score_momentum()
            - beneish:  output of compute_beneish()
            - form4:    {"insider_buy": bool}  (optional, default False)
        regime: One of "Risk-On", "Transitional", "Risk-Off", "Stagflation".

    Returns:
        List of ScreenerResult, sorted by composite_score descending, with rank assigned.
        EXCLUDED tickers are included with composite_score=0.0 and excluded=True for audit.
    """
    weights = _REGIME_WEIGHTS.get(regime, _REGIME_WEIGHTS[_DEFAULT_REGIME])
    if regime not in _REGIME_WEIGHTS:
        logger.warning("Unknown regime '%s' — falling back to Risk-On weights", regime)

    tickers = [c.ticker for c in universe]
    ticker_to_cand = {c.ticker: c for c in universe}

    # ── Step 1: Identify EXCLUDED tickers (Beneish hard gate + pre-revenue) ──
    excluded_set: set[str] = set()
    excluded: list[dict] = []
    for ticker in tickers:
        beneish = raw_factor_results.get(ticker, {}).get("beneish", {})
        if beneish.get("gate_result") == "EXCLUDED":
            excluded_set.add(ticker)
            logger.debug("%s: Beneish EXCLUDED — removed from scoring pool", ticker)
            continue
        quality_metrics = raw_factor_results.get(ticker, {}).get("quality", {})
        if quality_metrics.get("pre_revenue_flag"):
            excluded_set.add(ticker)
            excluded.append({"ticker": ticker, "reason": "PRE_REVENUE"})
            logger.info("%s: PRE_REVENUE — excluded from scoring pool", ticker)

    eligible = [t for t in tickers if t not in excluded_set]

    # ── Step 2: Extract raw sub-metric values across eligible tickers ─────────

    # Quality sub-metrics
    quality_raw: dict[str, dict[str, Optional[float]]] = {
        sub: {} for sub in _QUALITY_SUB_WEIGHTS
    }
    for ticker in eligible:
        q = raw_factor_results.get(ticker, {}).get("quality", {})
        rv = q.get("raw_values", {})
        for sub in _QUALITY_SUB_WEIGHTS:
            quality_raw[sub][ticker] = rv.get(sub)

    # Value sub-metrics (sector-relative normalization)
    sectors = {t: (ticker_to_cand[t].sector if t in ticker_to_cand else None) for t in eligible}
    value_raw: dict[str, dict[str, Optional[float]]] = {
        sub: {} for sub in _VALUE_SUB_WEIGHTS
    }
    for ticker in eligible:
        v = raw_factor_results.get(ticker, {}).get("value", {})
        rv = v.get("raw_values", {})
        for sub in _VALUE_SUB_WEIGHTS:
            value_raw[sub][ticker] = rv.get(sub)

    # Momentum sub-metrics
    momentum_raw: dict[str, dict[str, Optional[float]]] = {
        sub: {} for sub in _MOMENTUM_SUB_WEIGHTS
    }
    for ticker in eligible:
        m = raw_factor_results.get(ticker, {}).get("momentum", {})
        rv = m.get("raw_values", {})
        for sub in _MOMENTUM_SUB_WEIGHTS:
            momentum_raw[sub][ticker] = rv.get(sub)

    # ── Step 3: Normalize — Quality (universe-wide) ───────────────────────────
    quality_normalized: dict[str, dict[str, float]] = {}  # {ticker: {sub: score}}
    for sub in _QUALITY_SUB_WEIGHTS:
        higher_is_better = (sub != "debt_to_equity")  # D/E inverted
        norm = _normalize_universe(quality_raw[sub], higher_is_better=higher_is_better)
        for ticker, score in norm.items():
            quality_normalized.setdefault(ticker, {})[sub] = score

    # Pre-revenue penalty: Healthcare tickers with no revenue get 2.0 on
    # gross_margin and revenue_growth_yoy instead of the neutral-fill 5.0.
    # This correctly penalises pre-revenue biotech rather than treating them
    # as median-quality businesses.
    _PRE_REVENUE_SCORE = 2.0
    for ticker in eligible:
        q = raw_factor_results.get(ticker, {}).get("quality", {})
        if not q.get("pre_revenue_flag"):
            continue
        ticker_sector = (
            sectors.get(ticker)
            or (ticker_to_cand[ticker].sector if ticker in ticker_to_cand else None)
        )
        if ticker_sector == "Healthcare":
            rv = q.get("raw_values", {})
            if rv.get("gross_margin") is None:
                quality_normalized.setdefault(ticker, {})["gross_margin"] = _PRE_REVENUE_SCORE
            if rv.get("revenue_growth_yoy") is None:
                quality_normalized.setdefault(ticker, {})["revenue_growth_yoy"] = _PRE_REVENUE_SCORE
            logger.debug(
                "%s: pre-revenue Healthcare penalty → gm=%.1f rev_growth=%.1f",
                ticker, _PRE_REVENUE_SCORE, _PRE_REVENUE_SCORE,
            )

    # ── Step 4: Normalize — Value (sector-relative) ────────────────────────────
    value_normalized: dict[str, dict[str, float]] = {}
    for sub in _VALUE_SUB_WEIGHTS:
        # Group by sector
        sector_groups: dict[str, list[str]] = {}
        for ticker in eligible:
            s = sectors.get(ticker) or "Unknown"
            sector_groups.setdefault(s, []).append(ticker)

        for sector_tickers in sector_groups.values():
            sub_values = {t: value_raw[sub].get(t) for t in sector_tickers}
            norm = _normalize_universe(sub_values, higher_is_better=False)  # all value metrics: lower = better
            for ticker, score in norm.items():
                value_normalized.setdefault(ticker, {})[sub] = score

    # ── Step 5: Normalize — Momentum (universe-wide) ──────────────────────────
    momentum_normalized: dict[str, dict[str, float]] = {}
    for sub in _MOMENTUM_SUB_WEIGHTS:
        norm = _normalize_universe(momentum_raw[sub], higher_is_better=True)
        for ticker, score in norm.items():
            momentum_normalized.setdefault(ticker, {})[sub] = score

    # ── Step 6: Compute factor scores ─────────────────────────────────────────
    results: list[ScreenerResult] = []

    for ticker in eligible:
        cand = ticker_to_cand.get(ticker)
        beneish    = raw_factor_results.get(ticker, {}).get("beneish", {})
        form4      = raw_factor_results.get(ticker, {}).get("form4", {})
        momentum_r = raw_factor_results.get(ticker, {}).get("momentum", {})

        q_score = _compute_factor_score(quality_normalized.get(ticker, {}),  _QUALITY_SUB_WEIGHTS)
        v_score = _compute_factor_score(value_normalized.get(ticker, {}),    _VALUE_SUB_WEIGHTS)
        m_score = _compute_factor_score(momentum_normalized.get(ticker, {}), _MOMENTUM_SUB_WEIGHTS)

        # Composite
        composite = (
            q_score * weights["quality"]
            + v_score * weights["value"]
            + m_score * weights["momentum"]
        )

        # ── Discrete adjustments ─────────────────────────────────────────────
        # Beneish FLAGGED penalty
        if beneish.get("gate_result") == "FLAGGED":
            composite -= 0.5
            logger.debug("%s: Beneish FLAGGED − 0.5 penalty applied", ticker)

        # Insider buying bonus
        insider_signal = bool(form4.get("insider_buy", False))
        if insider_signal:
            composite += 0.3

        # Short interest bonus (from momentum result)
        si_bonus = momentum_r.get("short_interest_bonus", 0.0)
        if regime == "Risk-On":
            si_bonus *= 2  # doubled in Risk-On per domain rules
        composite = min(10.0, composite + si_bonus)

        # Regime-specific caps
        debt_level_high = _is_high_debt(raw_factor_results.get(ticker, {}))
        cash_runway     = raw_factor_results.get(ticker, {}).get("fmp", {}).get("cash_runway_months")

        if regime == "Risk-Off":
            if debt_level_high:
                composite = min(composite, 6.5)
            if cash_runway is not None and cash_runway < 18:
                composite = min(composite, 5.0)

        if regime == "Stagflation":
            gross_margin = raw_factor_results.get(ticker, {}).get("quality", {}).get("raw_values", {}).get("gross_margin")
            if gross_margin is not None and gross_margin < 0.40:
                composite -= 0.5
            cand_sector = cand.sector if cand else None
            if cand_sector == "Industrials":
                # book-to-bill proxy not available in Phase 1; skip
                pass

        composite = max(0.0, min(10.0, round(composite, 3)))

        sr = ScreenerResult(
            ticker=ticker,
            composite_score=composite,
            quality_score=round(q_score, 3),
            value_score=round(v_score, 3),
            momentum_score=round(m_score, 3),
            sector=cand.sector if cand else None,
            market_cap_m=cand.market_cap_m if cand else None,
            adv_k=cand.adv_k if cand else None,
            beneish_m_score=beneish.get("m_score"),
            beneish_flag=beneish.get("gate_result"),
            insider_signal=insider_signal,
            raw_factors={
                "quality":  raw_factor_results.get(ticker, {}).get("quality", {}).get("raw_values", {}),
                "value":    raw_factor_results.get(ticker, {}).get("value", {}).get("raw_values", {}),
                "momentum": raw_factor_results.get(ticker, {}).get("momentum", {}).get("raw_values", {}),
                "beneish":  beneish,
            },
            excluded=False,
        )
        results.append(sr)

    # ── Step 7: Audit rows for EXCLUDED tickers ───────────────────────────────
    for ticker in excluded_set:
        cand    = ticker_to_cand.get(ticker)
        beneish = raw_factor_results.get(ticker, {}).get("beneish", {})
        sr = ScreenerResult(
            ticker=ticker,
            composite_score=0.0,
            quality_score=0.0,
            value_score=0.0,
            momentum_score=0.0,
            sector=cand.sector if cand else None,
            market_cap_m=cand.market_cap_m if cand else None,
            adv_k=cand.adv_k if cand else None,
            beneish_m_score=beneish.get("m_score"),
            beneish_flag="EXCLUDED",
            excluded=True,
        )
        results.append(sr)

    # ── Step 8: Sort + rank ────────────────────────────────────────────────────
    # Sort by composite descending; EXCLUDED naturally float to bottom (score 0.0)
    results.sort(key=lambda r: r.composite_score, reverse=True)
    eligible_rank = 1
    for r in results:
        if not r.excluded:
            r.rank = eligible_rank
            eligible_rank += 1
        else:
            r.rank = eligible_rank + 10_000  # sentinel for EXCLUDED rows

    logger.info(
        "Scoring complete: %d eligible, %d excluded. Regime=%s",
        len(eligible), len(excluded_set), regime,
    )
    return results


def _is_high_debt(ticker_factors: dict) -> bool:
    """
    Heuristic: debt-to-equity > 2.0 or explicit 'high' debt flag.
    Used for Risk-Off cap.
    """
    d2e = ticker_factors.get("quality", {}).get("raw_values", {}).get("debt_to_equity")
    if d2e is not None and d2e > 2.0:
        return True
    return False
