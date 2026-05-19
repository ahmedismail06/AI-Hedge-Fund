"""
Short trigger-count scorer.

Replaces the legacy percentile-rank `compute_short_scores`. Shorts work on
regime-break signals (binary triggers), not on smoothly-normalized factor
exposure. Counting triggers preserves the discreteness of the bear thesis
and avoids the "everything clusters at 5.0" problem.

Seven binary triggers (all flag bearish conditions):

  T1 revenue_deceleration_3q   most recent 3 quarters: ≥2 sequential YoY-quarter declines
  T2 gross_margin_compression  GM YoY change ≤ -300 bps
  T3 fcf_burn_short_runway     fcf_ttm < 0 AND cash_runway_months < 18
  T4 beneish_flagged           m_score > -2.22 (FLAGGED or EXCLUDED)
  T5 debt_to_ebitda_elevated   debt/EBITDA TTM > 4×
  T6 ev_sales_premium          EV/Sales TTM > 1.5× sector median (computed across input set)
  T7 dilution_running          weighted diluted shares YoY growth > 8%

Qualification: trigger_count ≥ MIN_TRIGGERS (default 3).

Missing data: a trigger evaluates to False when its inputs are missing. Tickers
with fewer than MIN_EVALUABLE evaluable triggers (default 4) are rejected
outright — we don't want trigger_count=3 on a row where only 3 triggers had
any data at all.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
GM_COMPRESSION_BPS         = -0.03   # ≤ -300bps (-3 pct points) YoY
FCF_BURN_RUNWAY_MONTHS     = 18.0
BENEISH_FLAG_THRESHOLD     = -2.22   # Above this is FLAGGED or EXCLUDED
DEBT_EBITDA_THRESHOLD      = 4.0
EV_SALES_SECTOR_MULTIPLIER = 1.5
DILUTION_YOY_THRESHOLD     = 0.08    # 8% YoY shares-out growth

MIN_TRIGGERS               = 3
MIN_EVALUABLE              = 4       # at least this many triggers must have inputs


@dataclass
class TriggerResult:
    ticker: str
    sector: Optional[str]
    market_cap_m: Optional[float]
    adv_k: Optional[float]
    trigger_count: int
    triggers_fired: list[str]
    triggers_evaluated: list[str]    # ones we had data for (fired or not)
    evidence: dict = field(default_factory=dict)
    short_interest_pct: Optional[float] = None
    beneish_m_score: Optional[float] = None
    beneish_flag: Optional[str] = None


# ── Trigger predicates ────────────────────────────────────────────────────────
def _t1_revenue_deceleration(revenue_q: list[Optional[float]]) -> tuple[Optional[bool], dict]:
    """At least 2 of the most recent 3 sequential quarter-over-quarter changes are negative."""
    vals = [v for v in revenue_q[:4] if v is not None]
    if len(vals) < 3:
        return None, {}
    # revenue_q[0] = most recent. Compare 0 vs 1, 1 vs 2, 2 vs 3.
    diffs = []
    for i in range(min(3, len(vals) - 1)):
        diffs.append(vals[i] - vals[i + 1])  # current minus prior
    declines = sum(1 for d in diffs if d < 0)
    evidence = {"q_diffs": [round(d, 2) for d in diffs], "declines": declines}
    return declines >= 2, evidence


def _t2_gm_compression(gm_q: list[Optional[float]]) -> tuple[Optional[bool], dict]:
    """Gross margin YoY change ≤ -300bp. Compares gm_q[0] to gm_q[3]."""
    if len(gm_q) < 4 or gm_q[0] is None or gm_q[3] is None:
        return None, {}
    delta = gm_q[0] - gm_q[3]
    return delta <= GM_COMPRESSION_BPS, {"gm_yoy_delta": round(delta, 4),
                                          "gm_recent": gm_q[0], "gm_yoy_prior": gm_q[3]}


def _t3_fcf_burn(fcf_ttm: Optional[float], runway_months: Optional[float]) -> tuple[Optional[bool], dict]:
    if fcf_ttm is None or runway_months is None:
        return None, {}
    fired = fcf_ttm < 0 and runway_months < FCF_BURN_RUNWAY_MONTHS
    return fired, {"fcf_ttm": fcf_ttm, "runway_months": runway_months}


def _t4_beneish(m_score: Optional[float], flag: Optional[str]) -> tuple[Optional[bool], dict]:
    # Prefer flag when available; fall back to score threshold.
    if flag is not None:
        if flag in ("FLAGGED", "EXCLUDED"):
            return True, {"beneish_flag": flag, "m_score": m_score}
        if flag == "CLEAN":
            return False, {"beneish_flag": flag, "m_score": m_score}
        # INSUFFICIENT_DATA — try score
    if m_score is None:
        return None, {}
    fired = m_score > BENEISH_FLAG_THRESHOLD
    return fired, {"m_score": m_score, "beneish_flag": flag}


def _t5_debt_ebitda(debt_to_ebitda: Optional[float]) -> tuple[Optional[bool], dict]:
    if debt_to_ebitda is None:
        return None, {}
    return debt_to_ebitda > DEBT_EBITDA_THRESHOLD, {"debt_to_ebitda": debt_to_ebitda}


def _t6_ev_sales_premium(ev_sales: Optional[float], sector_median: Optional[float]) -> tuple[Optional[bool], dict]:
    if ev_sales is None or sector_median is None or sector_median <= 0:
        return None, {}
    threshold = sector_median * EV_SALES_SECTOR_MULTIPLIER
    return ev_sales > threshold, {"ev_sales": ev_sales,
                                   "sector_median_ev_sales": round(sector_median, 3),
                                   "threshold": round(threshold, 3)}


def _t7_dilution(shares_q: list[Optional[float]]) -> tuple[Optional[bool], dict]:
    """Diluted shares YoY growth > 8%. Needs shares_q[0] and shares_q[3]."""
    if len(shares_q) < 4 or shares_q[0] is None or shares_q[3] is None or shares_q[3] == 0:
        return None, {}
    growth = (shares_q[0] - shares_q[3]) / shares_q[3]
    return growth > DILUTION_YOY_THRESHOLD, {"shares_yoy_growth": round(growth, 4),
                                              "shares_recent": shares_q[0],
                                              "shares_yoy_prior": shares_q[3]}


# ── Sector-median helper for T6 ──────────────────────────────────────────────
def _compute_sector_ev_sales_medians(universe: list[dict]) -> dict[str, float]:
    """Compute median EV/Sales TTM per sector across the input universe."""
    by_sector: dict[str, list[float]] = {}
    for row in universe:
        sector = row.get("sector")
        ev = row.get("ev_to_sales_ttm")
        if sector and ev is not None and ev > 0:
            by_sector.setdefault(sector, []).append(ev)
    return {s: statistics.median(vals) for s, vals in by_sector.items() if len(vals) >= 3}


# ── Main scorer ───────────────────────────────────────────────────────────────
def score_triggers(
    universe: list[dict],
    beneish_lookup: Optional[dict[str, dict]] = None,
) -> list[TriggerResult]:
    """
    Evaluate triggers for every row in `universe`.

    Args:
        universe:        list of ShortUniverseRow dicts (output of build_short_universe).
        beneish_lookup:  optional {ticker: {"m_score": float, "gate_result": str}}
                         from the long screener's authoritative Beneish run.
                         When missing, T4 evaluates as None (skipped) for that ticker.

    Returns:
        list[TriggerResult] for every input row (qualified and not). Caller
        applies the MIN_TRIGGERS / MIN_EVALUABLE gates via `select_qualifying`.
    """
    sector_medians = _compute_sector_ev_sales_medians(universe)
    results: list[TriggerResult] = []

    for row in universe:
        ticker = row["ticker"]
        sector = row.get("sector")

        # Beneish — look up from external source (long screener writes to watchlist)
        beneish_entry = (beneish_lookup or {}).get(ticker, {})
        m_score = beneish_entry.get("m_score")
        flag = beneish_entry.get("gate_result")

        triggers: dict[str, tuple[Optional[bool], dict]] = {
            "T1_revenue_deceleration":   _t1_revenue_deceleration(row.get("revenue_q") or []),
            "T2_gm_compression":         _t2_gm_compression(row.get("gross_margin_q") or []),
            "T3_fcf_burn_short_runway":  _t3_fcf_burn(row.get("fcf_ttm"), row.get("cash_runway_months")),
            "T4_beneish_flagged":        _t4_beneish(m_score, flag),
            "T5_debt_to_ebitda":         _t5_debt_ebitda(row.get("debt_to_ebitda_ttm")),
            "T6_ev_sales_premium":       _t6_ev_sales_premium(
                row.get("ev_to_sales_ttm"), sector_medians.get(sector) if sector else None
            ),
            "T7_dilution":               _t7_dilution(row.get("shares_diluted_q") or []),
        }

        fired = [k for k, (v, _) in triggers.items() if v is True]
        evaluated = [k for k, (v, _) in triggers.items() if v is not None]
        evidence = {k: e for k, (v, e) in triggers.items() if v is not None}

        results.append(TriggerResult(
            ticker=ticker,
            sector=sector,
            market_cap_m=row.get("market_cap_m"),
            adv_k=row.get("adv_k"),
            trigger_count=len(fired),
            triggers_fired=fired,
            triggers_evaluated=evaluated,
            evidence=evidence,
            short_interest_pct=row.get("short_interest_pct"),
            beneish_m_score=m_score,
            beneish_flag=flag,
        ))

    return results


def select_qualifying(
    results: list[TriggerResult],
    min_triggers: int = MIN_TRIGGERS,
    min_evaluable: int = MIN_EVALUABLE,
    max_per_sector: int = 2,
    max_total: int = 5,
) -> list[TriggerResult]:
    """
    Filter trigger results to high-quality short candidates.

    Rules in order:
      1. trigger_count ≥ min_triggers
      2. len(triggers_evaluated) ≥ min_evaluable  (avoid sparse-data illusions)
      3. sort by (trigger_count desc, market_cap_m desc) — bigger names first when tied
      4. cap at max_per_sector per sector
      5. cap at max_total overall
    """
    eligible = [
        r for r in results
        if r.trigger_count >= min_triggers and len(r.triggers_evaluated) >= min_evaluable
    ]
    eligible.sort(key=lambda r: (r.trigger_count, r.market_cap_m or 0.0), reverse=True)

    selected: list[TriggerResult] = []
    sector_count: dict[str, int] = {}
    for r in eligible:
        if len(selected) >= max_total:
            break
        sector = r.sector or "Unknown"
        if sector_count.get(sector, 0) >= max_per_sector:
            continue
        selected.append(r)
        sector_count[sector] = sector_count.get(sector, 0) + 1
    return selected
