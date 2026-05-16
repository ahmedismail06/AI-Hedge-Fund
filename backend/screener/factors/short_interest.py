"""
Short Interest Factor — scores short squeeze potential and crowded-short setups.

Activation condition: capabilities.short_factor_active (NAV ≥ $50K trailing average).
Returns an inactive stub result when the capability gate is not met.

Planned signals when active:
  - Days-to-cover > 10 with rising price momentum → squeeze candidate
  - SI > 40% with positive catalyst → high-conviction short squeeze setup
  - Borrow cost (hard-to-borrow flag) → availability gate for short execution
"""

from __future__ import annotations

from typing import Optional


def score_short_interest(ticker: str, fmp_data: dict, capabilities=None) -> dict:
    """
    Score short interest dynamics for the given ticker.

    Args:
        ticker:       Stock ticker symbol.
        fmp_data:     Output of fetch_fmp() — expects short_interest_pct, days_to_cover.
        capabilities: Capabilities object. If None, resolved from current NAV automatically.

    Returns:
        {"ticker": str, "score": float | None, "active": bool}
        score is None and active is False when the capability gate is not met.
    """
    if capabilities is None:
        from backend.capabilities import get_capabilities
        capabilities = get_capabilities()

    if not capabilities.short_factor_active:
        return {"ticker": ticker.upper(), "score": None, "active": False}

    # ── Active scoring ────────────────────────────────────────────────────────
    si_pct = fmp_data.get("short_interest_pct") or 0.0
    days_to_cover = fmp_data.get("days_to_cover") or 0.0

    score: Optional[float] = None

    if si_pct > 0:
        # Base score: higher SI → higher squeeze potential
        score = min(10.0, si_pct * 10)

        # Bonus: days-to-cover > 10 indicates a crowded short that is slow to unwind
        if days_to_cover > 10:
            score = min(10.0, score + 1.5)

        # Penalty: very low SI is noise, not signal
        if si_pct < 0.05:
            score = max(0.0, score - 2.0)

    return {
        "ticker": ticker.upper(),
        "score": round(score, 2) if score is not None else None,
        "active": True,
    }
