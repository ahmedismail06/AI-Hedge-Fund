"""
Short-candidate bear-factor scorer.

Scores tickers as short candidates using data already stored in
watchlist.raw_factors — zero new API calls for the primary universe.

Sub-factors (baseline Risk-On/Transitional weights):
  deterioration  35%  Inverted quality signals (low ROIC, declining revenue/margins)
  overvaluation  30%  High EV multiples (expensive + deteriorating = best short)
  accruals       20%  Beneish M-score elevation + low FCF conversion
  cash_burn      15%  Short cash runway + high debt-to-equity

Normalization: universe-wide percentile rank (NOT sector-relative).
We want the most deteriorated/expensive stocks across all sectors.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Regime weight tables ──────────────────────────────────────────────────────

_WEIGHTS: dict[str, dict[str, float]] = {
    "Risk-On":      {"deterioration": 0.35, "overvaluation": 0.30, "accruals": 0.20, "cash_burn": 0.15},
    "Transitional": {"deterioration": 0.35, "overvaluation": 0.30, "accruals": 0.20, "cash_burn": 0.15},
    "Risk-Off":     {"deterioration": 0.25, "overvaluation": 0.30, "accruals": 0.20, "cash_burn": 0.25},
    "Stagflation":  {"deterioration": 0.30, "overvaluation": 0.35, "accruals": 0.20, "cash_burn": 0.15},
}

_DEFAULT_WEIGHTS = _WEIGHTS["Transitional"]

# ── Beneish score mapping ─────────────────────────────────────────────────────
# Higher M-score = more suspicious = stronger short signal
_BENEISH_EXCLUDED_SCORE  = 9.5   # M > -1.78 (hard-gated from longs)
_BENEISH_FLAGGED_SCORE   = 6.5   # -2.22 < M ≤ -1.78
_BENEISH_CLEAN_SCORE     = 2.0   # M ≤ -2.22 (clean)
_BENEISH_UNKNOWN_SCORE   = 5.0   # INSUFFICIENT_DATA or missing

# Flat bonus for Beneish EXCLUDED rows (strongest bear signal in universe)
_EXCLUDED_ACCRUALS_BOOST = 0.5


@dataclass
class ShortScorerResult:
    ticker: str
    short_score: float
    deterioration_score: float
    overvaluation_score: float
    accruals_score: float
    cash_burn_score: float
    market_cap_m: Optional[float] = None
    sector: Optional[str] = None
    adv_k: Optional[float] = None
    beneish_m_score: Optional[float] = None
    beneish_flag: Optional[str] = None
    raw_factors: dict = field(default_factory=dict)
    source: str = "watchlist"


# ── Internal normalization (mirrors scorer.py _normalize_universe) ────────────

def _normalize(
    values: dict[str, Optional[float]],
    higher_is_better: bool = True,
) -> dict[str, Optional[float]]:
    """Percentile-rank values to 0–10 using average-rank normalization for ties."""
    valid = {t: v for t, v in values.items() if v is not None and not math.isnan(v)}

    if not valid:
        return {t: None for t in values}

    if len(valid) < 2 or len(set(valid.values())) == 1:
        return {t: (5.0 if t in valid else None) for t in values}

    sorted_vals = sorted(valid.values())
    n = len(sorted_vals)

    val_to_score: dict[float, float] = {}
    for unique_val in set(sorted_vals):
        indices = [i for i, v in enumerate(sorted_vals) if v == unique_val]
        avg_rank = sum(indices) / len(indices)
        score = (avg_rank / (n - 1)) * 10.0
        if not higher_is_better:
            score = 10.0 - score
        val_to_score[unique_val] = round(score, 4)

    return {t: (val_to_score[v] if t in valid else None) for t, v in values.items()}


def _weighted_mean(scores: dict[str, Optional[float]], weights: dict[str, float]) -> float:
    total_w = 0.0
    total_s = 0.0
    for key, w in weights.items():
        s = scores.get(key)
        if s is not None:
            total_s += s * w
            total_w += w
    if total_w == 0.0:
        return 5.0
    return round(total_s / total_w, 4)


# ── Sub-factor raw extractors ─────────────────────────────────────────────────

def _extract_deterioration_raw(rf: dict) -> dict[str, Optional[float]]:
    """Extract quality sub-metrics, to be INVERTED (lower quality = higher deterioration).

    watchlist.raw_factors stores the raw_values dict directly under the 'quality' key,
    so there is no extra .get('raw_values') nesting.
    """
    q = rf.get("quality") or {}
    return {
        "roic":               q.get("roic"),
        "fcf_conversion":     q.get("fcf_conversion"),
        "gross_margin":       q.get("gross_margin"),
        "revenue_growth_yoy": q.get("revenue_growth_yoy"),
    }


def _extract_overvaluation_raw(rf: dict) -> dict[str, Optional[float]]:
    """Extract valuation multiples. Used AS-IS — high = expensive = good short signal.
    Missing ev_multiple → 8.0 sentinel (not the long screener's 5.0 neutral).

    watchlist.raw_factors stores value metrics flat under 'value', no raw_values nesting.
    """
    v = rf.get("value") or {}
    ev = v.get("ev_multiple")
    p_fcf = v.get("p_fcf")
    price_book = v.get("price_book")
    # For a non-profitable company with no FCF, p_fcf is often None or negative.
    # A negative p_fcf (burning cash) is actually a STRONG bear signal.
    # Cap p_fcf contribution: treat None as sentinel 8.0, treat negative as 10.0.
    if p_fcf is not None and p_fcf < 0:
        p_fcf = 10.0  # negative FCF = pay floor price for a money-loser
    return {
        "ev_multiple":  ev,      # None → sentinel applied in scoring phase
        "p_fcf":        p_fcf,
        "price_book":   price_book,
    }


def _extract_accruals_raw(rf: dict, beneish_flag: Optional[str]) -> tuple[float, Optional[float]]:
    """Return (beneish_component_score, fcf_conversion_raw).
    Beneish is a categorical → direct score lookup, not normalized."""
    flag = beneish_flag or (rf.get("beneish") or {}).get("gate_result")
    if flag == "EXCLUDED":
        b_score = _BENEISH_EXCLUDED_SCORE
    elif flag == "FLAGGED":
        b_score = _BENEISH_FLAGGED_SCORE
    elif flag == "CLEAN":
        b_score = _BENEISH_CLEAN_SCORE
    else:
        b_score = _BENEISH_UNKNOWN_SCORE

    fcf_conv = (rf.get("quality") or {}).get("fcf_conversion")
    return b_score, fcf_conv


def _extract_cash_burn_raw(rf: dict) -> dict[str, Optional[float]]:
    # cash_runway_months is computed in the live screener pipeline but not persisted
    # to watchlist.raw_factors, so it will always be None here (→ neutral 5.0 score).
    q = rf.get("quality") or {}
    de = q.get("debt_to_equity")
    return {"cash_runway_months": None, "debt_to_equity": de}


# ── Cash burn direct scorer (non-percentile; categorical thresholds) ──────────

def _score_cash_runway(months: Optional[float]) -> float:
    if months is None:
        return 5.0
    if months < 6:
        return 10.0
    if months < 12:
        return 9.0
    if months < 18:
        return 7.0
    if months < 24:
        return 4.0
    return 1.0


# ── Main scoring function ─────────────────────────────────────────────────────

def compute_short_scores(
    candidates: list[dict],
    regime: str = "Transitional",
) -> list[ShortScorerResult]:
    """
    Score each candidate as a short using bear factors.

    Each candidate dict must have at minimum:
        ticker, raw_factors (dict), beneish_flag (str|None)
    Optional: market_cap_m, sector, adv_k, beneish_m_score, source
    """
    weights = _WEIGHTS.get(regime, _DEFAULT_WEIGHTS)

    # ── Pass 1: collect raw values for universe-wide normalization ────────────
    # deterioration: 4 sub-metrics, each normalized separately then averaged
    det_raw: dict[str, dict[str, Optional[float]]] = {
        "roic": {}, "fcf_conversion": {}, "gross_margin": {}, "revenue_growth_yoy": {}
    }
    # overvaluation: 3 sub-metrics
    ov_raw: dict[str, dict[str, Optional[float]]] = {
        "ev_multiple": {}, "p_fcf": {}, "price_book": {}
    }
    # accruals fcf_conversion for normalization
    acc_fcf_raw: dict[str, Optional[float]] = {}
    # cash burn: runway direct-scored; debt_to_equity normalized
    de_raw: dict[str, Optional[float]] = {}

    for c in candidates:
        ticker = c["ticker"]
        rf = c.get("raw_factors") or {}
        beneish_flag = c.get("beneish_flag")

        d = _extract_deterioration_raw(rf)
        for sub in det_raw:
            det_raw[sub][ticker] = d.get(sub)

        ov = _extract_overvaluation_raw(rf)
        for sub in ov_raw:
            ov_raw[sub][ticker] = ov.get(sub)

        _, fcf_conv = _extract_accruals_raw(rf, beneish_flag)
        acc_fcf_raw[ticker] = fcf_conv

        cb = _extract_cash_burn_raw(rf)
        de_raw[ticker] = cb.get("debt_to_equity")

    # ── Pass 2: normalize ─────────────────────────────────────────────────────
    det_normed = {sub: _normalize(det_raw[sub], higher_is_better=False) for sub in det_raw}
    ov_normed = {
        "ev_multiple": _normalize(ov_raw["ev_multiple"], higher_is_better=True),
        "p_fcf":       _normalize(ov_raw["p_fcf"],       higher_is_better=True),
        "price_book":  _normalize(ov_raw["price_book"],  higher_is_better=True),
    }
    acc_fcf_normed = _normalize(acc_fcf_raw, higher_is_better=False)
    de_normed = _normalize(de_raw, higher_is_better=True)

    # ── Pass 3: compose final scores per ticker ───────────────────────────────
    results: list[ShortScorerResult] = []

    for c in candidates:
        ticker = c["ticker"]
        rf = c.get("raw_factors") or {}
        beneish_flag = c.get("beneish_flag")
        is_excluded = beneish_flag == "EXCLUDED"

        # Deterioration (4 sub-metrics, equal weight within the sub-factor)
        det_subs: dict[str, Optional[float]] = {
            sub: det_normed[sub].get(ticker) for sub in det_normed
        }
        det_score: float
        if is_excluded and not any(v is not None for v in det_subs.values()):
            # EXCLUDED row with no quality data — treat as neutral 5.0
            det_score = 5.0
        else:
            det_score = _weighted_mean(det_subs, {s: 0.25 for s in det_subs})

        # Overvaluation (3 sub-metrics, equal weight within the sub-factor)
        ov_subs: dict[str, Optional[float]] = {}
        for sub in ov_normed:
            normed_val = ov_normed[sub].get(ticker)
            # Apply sentinel for missing EV multiple (longs use 5.0; shorts use 8.0)
            if normed_val is None and sub == "ev_multiple":
                normed_val = 8.0
            ov_subs[sub] = normed_val
        ov_score = _weighted_mean(ov_subs, {s: 1/3 for s in ov_subs})

        # Accruals (60% Beneish categorical + 40% FCF conversion normalized)
        b_score, _ = _extract_accruals_raw(rf, beneish_flag)
        fcf_norm = acc_fcf_normed.get(ticker)
        if fcf_norm is not None:
            accruals_score = round(0.60 * b_score + 0.40 * fcf_norm, 4)
        else:
            accruals_score = b_score  # Beneish only if FCF missing

        if is_excluded:
            accruals_score = min(10.0, accruals_score + _EXCLUDED_ACCRUALS_BOOST)

        # Cash burn (50% direct runway + 50% D/E normalized)
        cb = _extract_cash_burn_raw(rf)
        runway_score = _score_cash_runway(cb.get("cash_runway_months"))
        de_norm = de_normed.get(ticker)
        if de_norm is not None:
            cash_burn_score = round(0.50 * runway_score + 0.50 * de_norm, 4)
        else:
            cash_burn_score = runway_score

        # Composite
        short_score = round(
            weights["deterioration"] * det_score
            + weights["overvaluation"] * ov_score
            + weights["accruals"]     * accruals_score
            + weights["cash_burn"]    * cash_burn_score,
            3,
        )
        short_score = max(0.0, min(10.0, short_score))

        results.append(ShortScorerResult(
            ticker=ticker,
            short_score=short_score,
            deterioration_score=round(det_score, 3),
            overvaluation_score=round(ov_score, 3),
            accruals_score=round(accruals_score, 3),
            cash_burn_score=round(cash_burn_score, 3),
            market_cap_m=c.get("market_cap_m"),
            sector=c.get("sector"),
            adv_k=c.get("adv_k"),
            beneish_m_score=c.get("beneish_m_score"),
            beneish_flag=beneish_flag,
            raw_factors=rf,
            source=c.get("source", "watchlist"),
        ))

    return results


def select_short_candidates(
    scored: list[ShortScorerResult],
    max_candidates: int = 5,
    min_short_score: float = 6.0,
) -> list[ShortScorerResult]:
    """
    Select the top short candidates with diversity and quality gates.

    Rules applied in order:
    1. Filter: short_score >= min_short_score
    2. Sort: descending short_score
    3. Diversity gate: max 2 tickers per sector in the final set
    4. Cap: return at most max_candidates
    """
    eligible = [r for r in scored if r.short_score >= min_short_score]
    eligible.sort(key=lambda r: r.short_score, reverse=True)

    selected: list[ShortScorerResult] = []
    sector_count: dict[str, int] = {}

    for r in eligible:
        if len(selected) >= max_candidates:
            break
        sector = r.sector or "Unknown"
        if sector_count.get(sector, 0) >= 2:
            continue
        selected.append(r)
        sector_count[sector] = sector_count.get(sector, 0) + 1

    return selected
