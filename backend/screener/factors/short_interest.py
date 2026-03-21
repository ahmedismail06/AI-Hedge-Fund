"""
Short Interest Factor — Phase 2 stub (inactive in Phase 1, long-only).

Will score short squeeze potential and crowded-short setups when shorting
is enabled at $50K+ capital. Planned signals:
  - Days-to-cover > 10 with rising price momentum → squeeze candidate
  - SI > 40% with positive catalyst → high-conviction short squeeze setup
  - Borrow cost (hard-to-borrow flag) → availability gate for short execution

Activation condition: ENV variable PHASE >= 2 (set when capital ≥ $50K).
Until then, this module returns an empty result and is not called by scorer.py.

Phase 2 implementation will:
  1. Add a `short_interest_score` sub-factor to ScreenerResult
  2. Add a PHASE-gated weight (10%) to the composite in scorer.py
  3. Call this module from screening_agent._batch_fetch_ticker_data()
"""


def score_short_interest(ticker: str, fmp_data: dict) -> dict:
    """
    Phase 2 stub — returns zero signal in Phase 1.

    Args:
        ticker: Stock ticker symbol.
        fmp_data: Output of fetch_fmp() (contains short_interest_pct, days_to_cover).

    Returns:
        {"ticker": str, "score": None, "active": False}
    """
    return {
        "ticker": ticker.upper(),
        "score":  None,
        "active": False,
    }
