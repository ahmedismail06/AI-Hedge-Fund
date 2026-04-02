"""
Exposure Monitor — watches for gross/net exposure drift between sizing events.

Reuses REGIME_CAPS and get_current_exposure() from backend.portfolio.exposure_tracker.
Emits ExposureBreach events:
  - WARN  if current exposure is within 10% of the cap (approaching limit)
  - BREACH if current exposure exceeds the cap
"""

from backend.portfolio.exposure_tracker import REGIME_CAPS, get_current_exposure
from backend.risk.schemas import ExposureBreach

# Warn threshold: fire a WARN when within this fraction of the cap
_WARN_BUFFER = 0.10  # 10%


def check_exposure_drift(
    positions: list[dict], regime: str, portfolio_value: float = 25_000.0
) -> list[ExposureBreach]:
    """
    Compare live exposure against regime-gated caps and return any breaches.

    Args:
        positions:       list of OPEN position dicts from the `positions` table.
                         Must have: dollar_size, direction, sector fields.
        regime:          current macro regime string.
        portfolio_value: total portfolio NAV in dollars (default $25K for Phase 1).

    Returns:
        List of ExposureBreach objects. Empty list = exposure within limits.
    """
    exposure = get_current_exposure(positions, portfolio_value=portfolio_value, regime=regime)
    current_gross: float = exposure.get("gross_exposure_pct", 0.0)
    current_net: float = exposure.get("net_exposure_pct", 0.0)
    max_gross: float = exposure.get("max_gross_pct", 1.5)
    max_net: float = exposure.get("max_net_long_pct", 0.5)

    breaches: list[ExposureBreach] = []

    # ── Gross exposure check ──────────────────────────────────────────────────
    if current_gross > max_gross:
        breaches.append(ExposureBreach(
            current_gross=current_gross,
            cap_gross=max_gross,
            current_net=current_net,
            cap_net=max_net,
            severity="BREACH",
            regime=regime,
        ))
    elif current_gross > max_gross * (1.0 - _WARN_BUFFER):
        breaches.append(ExposureBreach(
            current_gross=current_gross,
            cap_gross=max_gross,
            current_net=current_net,
            cap_net=max_net,
            severity="WARN",
            regime=regime,
        ))

    # ── Net exposure check (only if gross wasn't already breached) ────────────
    # Avoid double-firing when both gross and net are breached in the same cycle.
    if not breaches or breaches[0].severity == "WARN":
        if current_net > max_net:
            breaches.append(ExposureBreach(
                current_gross=current_gross,
                cap_gross=max_gross,
                current_net=current_net,
                cap_net=max_net,
                severity="BREACH",
                regime=regime,
            ))
        elif current_net > max_net * (1.0 - _WARN_BUFFER) and not breaches:
            breaches.append(ExposureBreach(
                current_gross=current_gross,
                cap_gross=max_gross,
                current_net=current_net,
                cap_net=max_net,
                severity="WARN",
                regime=regime,
            ))

    return breaches
