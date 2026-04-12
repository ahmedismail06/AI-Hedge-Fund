"""
Notifier — routes alerts to Supabase (always) and optional push channels.

Routing rules:
  WARN    → Supabase only
  BREACH  → Supabase + Slack (if SLACK_WEBHOOK_URL is set)
  CRITICAL → Supabase + Slack (if SLACK_WEBHOOK_URL is set)

Email (ALERT_EMAIL) is deferred — a warning is logged if the env var is set.
Slack delivery is handled by backend.notifications.events.notify_event().
"""

import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from backend.models.risk import RiskAlert
from backend.notifications.events import notify_event

load_dotenv()

logger = logging.getLogger(__name__)

_ALERT_EMAIL = os.getenv("ALERT_EMAIL")

_PUSH_SEVERITIES = {"BREACH", "CRITICAL"}


def dispatch_alerts(alerts: list[RiskAlert], supabase_client) -> None:
    """
    Persist all alerts to Supabase and push BREACH/CRITICAL alerts to Slack.

    Args:
        alerts:          list of RiskAlert objects produced by alerts.build_alerts()
        supabase_client: initialised supabase-py client
    """
    if not alerts:
        return

    # ── 1. Upsert every alert to risk_alerts table ────────────────────────────
    rows = [_alert_to_row(a) for a in alerts]
    supabase_client.table("risk_alerts").upsert(rows, on_conflict="id").execute()
    logger.info("dispatched %d alert(s) to Supabase", len(alerts))

    # ── 2. Push BREACH / CRITICAL to Slack ────────────────────────────────────
    push_alerts = [a for a in alerts if _severity(a) in _PUSH_SEVERITIES]
    for a in push_alerts:
        sev = _severity(a)
        event_type = "RISK_CRITICAL" if sev == "CRITICAL" else "RISK_BREACH"
        notify_event(event_type, {
            "ticker": a.ticker,
            "trigger": a.trigger,
            "regime": a.regime,
        })

    # ── 3. Email stub ─────────────────────────────────────────────────────────
    if _ALERT_EMAIL:
        logger.warning(
            "ALERT_EMAIL is set (%s) but SMTP is not yet implemented — "
            "email notifications deferred.",
            _ALERT_EMAIL,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _alert_to_row(alert: RiskAlert) -> dict:
    """Convert a RiskAlert to a Supabase-compatible dict."""
    tier_severity = {1: "WARN", 2: "BREACH", 3: "CRITICAL"}
    return {
        "id": alert.alert_id,
        "ticker": alert.ticker,
        "tier": alert.tier,
        "severity": tier_severity.get(alert.tier, "WARN"),
        "trigger": alert.trigger,
        "regime": alert.regime,
        "resolved": alert.resolved,
        "resolved_at": None,
        "created_at": alert.timestamp,
    }


def _severity(alert: RiskAlert) -> str:
    tier_severity = {1: "WARN", 2: "BREACH", 3: "CRITICAL"}
    return tier_severity.get(alert.tier, "WARN")


