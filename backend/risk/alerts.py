"""
Alert Generator — builds structured RiskAlert objects from stop events and exposure breaches.

Severity mapping:
  Tier 1 stop or WARN exposure  → WARN
  Tier 2 stop or BREACH exposure → BREACH
  Tier 3 stop                    → CRITICAL

Also exposes autonomous_should_suspend() which the orchestrator calls to decide
whether to block new trade approvals.
"""

import uuid
from datetime import datetime, timezone

from backend.models.risk import RiskAlert
from backend.risk.schemas import ExposureBreach, StopEvent

# Daily drawdown threshold that auto-suspends autonomous mode
_DAILY_DRAWDOWN_SUSPEND = -0.05  # -5%


def build_alerts(
    stop_events: list[StopEvent],
    exposure_breaches: list[ExposureBreach],
    regime: str,
) -> list[RiskAlert]:
    """
    Convert stop events and exposure breaches into RiskAlert objects.

    Args:
        stop_events:       from backend.risk.stop_loss.check_stops()
        exposure_breaches: from backend.risk.exposure_monitor.check_exposure_drift()
        regime:            current macro regime string.

    Returns:
        List of RiskAlert objects ready for Supabase upsert + notification.
    """
    alerts: list[RiskAlert] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── Stop events → alerts ──────────────────────────────────────────────────
    for event in stop_events:
        severity = _severity_for_tier(event.tier)
        trigger = _stop_trigger_text(event)

        alerts.append(RiskAlert(
            alert_id=str(uuid.uuid4()),
            timestamp=now_iso,
            ticker=event.ticker,
            tier=event.tier,
            trigger=trigger,
            regime=regime,
            resolved=False,
        ))

    # ── Exposure breaches → alerts ────────────────────────────────────────────
    for breach in exposure_breaches:
        tier = 2 if breach.severity == "BREACH" else 1
        trigger = _exposure_trigger_text(breach)

        alerts.append(RiskAlert(
            alert_id=str(uuid.uuid4()),
            timestamp=now_iso,
            ticker=None,
            tier=tier,
            trigger=trigger,
            regime=regime,
            resolved=False,
        ))

    return alerts


def autonomous_should_suspend(alerts: list[RiskAlert], portfolio_pnl_pct: float = 0.0) -> bool:
    """
    Return True if autonomous mode should be suspended.

    Suspension conditions (per domain rules):
      1. Any CRITICAL alert is active (Tier 3 stop hit).
      2. Daily portfolio drawdown exceeds 5%.
    """
    for alert in alerts:
        if alert.tier == 3:
            return True

    if portfolio_pnl_pct <= _DAILY_DRAWDOWN_SUSPEND:
        return True

    return False


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _severity_for_tier(tier: int) -> str:
    if tier == 1:
        return "WARN"
    if tier == 2:
        return "BREACH"
    return "CRITICAL"


def _stop_trigger_text(event: StopEvent) -> str:
    tier_labels = {1: "Position", 2: "Sector", 3: "Portfolio"}
    label = tier_labels.get(event.tier, "Unknown")
    pct = f"{event.pct_move * 100:.1f}%"

    if event.tier == 1 and event.ticker:
        if getattr(event, "approaching", False):
            stop_str = f"${event.stop_price:.2f}" if event.stop_price else "stop"
            curr_str = f"${event.current_price:.2f}" if event.current_price else "current"
            dist_pct = (
                f"{(event.current_price - event.stop_price) / event.stop_price * 100:.1f}%"
                if event.stop_price and event.current_price else "?"
            )
            return (
                f"Approaching stop — {event.ticker}: price {curr_str} is {dist_pct} above "
                f"stop {stop_str} (P&L {pct})"
            )
        return f"Tier 1 {label} stop triggered: {event.ticker} at {pct} P&L"
    if event.tier == 2 and event.sector:
        return f"Tier 2 {label} stop triggered: {event.sector} sector at {pct} avg P&L"
    return f"Tier {event.tier} {label} stop triggered: portfolio at {pct} P&L"


def _exposure_trigger_text(breach: ExposureBreach) -> str:
    gross_pct = f"{breach.current_gross * 100:.1f}%"
    cap_pct = f"{breach.cap_gross * 100:.1f}%"
    return (
        f"Exposure {breach.severity}: gross {gross_pct} vs cap {cap_pct} "
        f"in {breach.regime} regime"
    )
