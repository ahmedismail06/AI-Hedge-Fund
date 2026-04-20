"""
Pre-earnings estimate comparator.

Extrapolates an internal next-quarter EPS estimate via CAGR on the last 4
non-None reported_eps values from earnings_reactions data, then compares
against FMP/yfinance consensus to produce a SIZE_UP / HOLD / REDUCE signal.

Thresholds (from domain rules):
  SIZE_UP  — spread >= +10% AND conviction_score >= 7.0
  REDUCE   — spread <= -10%
  HOLD     — everything else (including spread unavailable)
"""
from __future__ import annotations

import logging
from typing import Optional

from backend.earnings_alpha.schemas import PreEarningsSizing

logger = logging.getLogger(__name__)

# Conviction minimum required before a SIZE_UP signal is issued
_CONVICTION_GATE = 7.0
# Spread thresholds (fraction, not percent)
_SPREAD_SIZE_UP = 0.10
_SPREAD_REDUCE = -0.10
# Minimum non-None quarters required to extrapolate
_MIN_QUARTERS = 3


def extrapolate_internal_eps(reactions: list[dict]) -> Optional[float]:
    """
    Derive a forward EPS estimate by applying the observed quarterly CAGR
    on the most recent 4 non-None reported_eps values.

    reactions — list of dicts as returned by get_earnings_reactions(), newest first.
    Returns None when fewer than _MIN_QUARTERS valid data points are available.
    """
    valid_eps: list[float] = [
        r["reported_eps"]
        for r in reactions
        if r.get("reported_eps") is not None
    ][:8]  # use at most 8 most-recent quarters

    if len(valid_eps) < _MIN_QUARTERS:
        logger.debug("extrapolate_internal_eps: only %d valid quarters, need %d", len(valid_eps), _MIN_QUARTERS)
        return None

    # Use up to 4 quarters for CAGR; oldest is valid_eps[-1], newest is valid_eps[0]
    window = valid_eps[:4]
    eps_latest = window[0]
    eps_oldest = window[-1]
    n_periods = len(window) - 1

    # Guard against sign changes or zero base (edge case for micro-caps near breakeven)
    if eps_oldest == 0 or eps_latest * eps_oldest < 0:
        logger.debug(
            "extrapolate_internal_eps: CAGR undefined (eps_latest=%.4f, eps_oldest=%.4f)",
            eps_latest, eps_oldest,
        )
        return None

    quarterly_growth = (eps_latest / eps_oldest) ** (1.0 / n_periods) - 1.0
    internal_estimate = eps_latest * (1.0 + quarterly_growth)
    logger.debug(
        "extrapolate_internal_eps: qtr_growth=%.4f, est=%.4f",
        quarterly_growth, internal_estimate,
    )
    return internal_estimate


def compute_signal(
    internal_est: Optional[float],
    consensus_eps: Optional[float],
    conviction_score: float,
) -> PreEarningsSizing:
    """
    Compare internal estimate against consensus and return a sizing signal.

    Unavailable estimates always yield HOLD (insufficient data to act).
    Consensus of 0 is treated as unavailable to avoid division-by-zero.
    """
    if internal_est is None or consensus_eps is None or consensus_eps == 0:
        return PreEarningsSizing(
            signal="HOLD",
            internal_eps_estimate=internal_est,
            consensus_eps=consensus_eps,
            spread_pct=None,
            conviction_gate_passed=False,
            rationale="Insufficient data to compute spread — defaulting to HOLD.",
        )

    spread_pct = (internal_est - consensus_eps) / abs(consensus_eps)
    conviction_gate = conviction_score >= _CONVICTION_GATE

    if spread_pct >= _SPREAD_SIZE_UP and conviction_gate:
        signal: str = "SIZE_UP"
        rationale = (
            f"Internal est ${internal_est:.2f} exceeds consensus ${consensus_eps:.2f} "
            f"by {spread_pct:+.1%} — conviction {conviction_score:.1f} >= {_CONVICTION_GATE}. "
            "Pre-earnings size-up authorised."
        )
    elif spread_pct >= _SPREAD_SIZE_UP and not conviction_gate:
        signal = "HOLD"
        rationale = (
            f"Spread {spread_pct:+.1%} meets size-up threshold but conviction "
            f"{conviction_score:.1f} < {_CONVICTION_GATE} — holding current size."
        )
    elif spread_pct <= _SPREAD_REDUCE:
        signal = "REDUCE"
        rationale = (
            f"Internal est ${internal_est:.2f} trails consensus ${consensus_eps:.2f} "
            f"by {spread_pct:+.1%} — risk of negative surprise; recommend trim."
        )
    else:
        signal = "HOLD"
        rationale = (
            f"Spread {spread_pct:+.1%} within ±10% band — no pre-earnings adjustment."
        )

    return PreEarningsSizing(
        signal=signal,  # type: ignore[arg-type]
        internal_eps_estimate=internal_est,
        consensus_eps=consensus_eps,
        spread_pct=spread_pct,
        conviction_gate_passed=conviction_gate,
        rationale=rationale,
    )
