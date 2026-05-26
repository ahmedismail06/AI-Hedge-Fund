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
        portfolio_value: total portfolio NAV in dollars.

    Returns:
        List of ExposureBreach objects. Empty list = exposure within limits.
    """
    exposure = get_current_exposure(positions, portfolio_value=portfolio_value, regime=regime)
    current_gross: float = exposure.get("gross_exposure_pct", 0.0)
    current_net: float = exposure.get("net_exposure_pct", 0.0)
    current_gross_short: float = exposure.get("gross_short_pct", 0.0)
    max_gross: float = exposure.get("max_gross_pct", 1.5)
    max_net_long: float = exposure.get("max_net_long_pct", 0.5)
    max_net_short: float = exposure.get("max_net_short_pct", 0.0)   # negative floor or 0
    max_gross_short: float = exposure.get("max_gross_short_pct", 0.6)

    breaches: list[ExposureBreach] = []

    # ── Gross exposure check ──────────────────────────────────────────────────
    if current_gross > max_gross:
        breaches.append(ExposureBreach(
            current_gross=current_gross,
            cap_gross=max_gross,
            current_net=current_net,
            cap_net=max_net_long,
            severity="BREACH",
            regime=regime,
            breach_type="gross",
        ))
    elif current_gross > max_gross * (1.0 - _WARN_BUFFER):
        breaches.append(ExposureBreach(
            current_gross=current_gross,
            cap_gross=max_gross,
            current_net=current_net,
            cap_net=max_net_long,
            severity="WARN",
            regime=regime,
            breach_type="gross",
        ))

    # ── Gross short exposure check ────────────────────────────────────────────
    # Separate cap on total short notional; tighter than gross because shorts
    # carry asymmetric loss (loss on a short is theoretically unbounded).
    if not breaches or breaches[-1].severity == "WARN":
        if current_gross_short > max_gross_short:
            breaches.append(ExposureBreach(
                current_gross=current_gross_short,
                cap_gross=max_gross_short,
                current_net=current_net,
                cap_net=max_net_short,
                severity="BREACH",
                regime=regime,
                breach_type="gross_short",
            ))
        elif current_gross_short > max_gross_short * (1.0 - _WARN_BUFFER) and not breaches:
            breaches.append(ExposureBreach(
                current_gross=current_gross_short,
                cap_gross=max_gross_short,
                current_net=current_net,
                cap_net=max_net_short,
                severity="WARN",
                regime=regime,
                breach_type="gross_short",
            ))

    # ── Net long cap and net short floor check ────────────────────────────────
    # Avoid double-firing when gross was already breached.
    if not breaches or breaches[-1].severity == "WARN":
        if current_net > max_net_long:
            breaches.append(ExposureBreach(
                current_gross=current_gross,
                cap_gross=max_gross,
                current_net=current_net,
                cap_net=max_net_long,
                severity="BREACH",
                regime=regime,
                breach_type="net_long",
            ))
        elif current_net > max_net_long * (1.0 - _WARN_BUFFER) and not breaches:
            breaches.append(ExposureBreach(
                current_gross=current_gross,
                cap_gross=max_gross,
                current_net=current_net,
                cap_net=max_net_long,
                severity="WARN",
                regime=regime,
                breach_type="net_long",
            ))
        elif current_net < max_net_short - 1e-6:
            # Net short floor breached (net is more negative than the regime floor)
            breaches.append(ExposureBreach(
                current_gross=current_gross,
                cap_gross=max_gross,
                current_net=current_net,
                cap_net=max_net_short,
                severity="BREACH",
                regime=regime,
                breach_type="net_short",
            ))
        elif max_net_short < 0 and current_net < max_net_short * (1.0 - _WARN_BUFFER) and not breaches:
            # Approaching net-short floor; skip WARN when floor is 0 (no buffer exists)
            breaches.append(ExposureBreach(
                current_gross=current_gross,
                cap_gross=max_gross,
                current_net=current_net,
                cap_net=max_net_short,
                severity="WARN",
                regime=regime,
                breach_type="net_short",
            ))

    return breaches
