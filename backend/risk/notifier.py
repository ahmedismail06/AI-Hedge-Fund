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
    for row in rows:
        logger.info(
            "writing alert → id=%s ticker=%s severity=%s tier=%d trigger=%r",
            row["id"], row.get("ticker", "portfolio"), row["severity"], row["tier"], row["trigger"],
        )
    try:
        db_resp = supabase_client.table("risk_alerts").upsert(rows, on_conflict="id").execute()
        logger.info(
            "Supabase upsert OK — %d alert(s) written, rows_returned=%d",
            len(rows), len(db_resp.data or []),
        )
    except Exception as exc:
        logger.error("Supabase upsert FAILED for risk_alerts: %s", exc, exc_info=True)
        raise

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

    # ── 3. Reactive PM cycle for CRITICAL alerts ──────────────────────────────
    # Trigger an immediate PM decision cycle so CRISIS decisions aren't delayed
    # by up to 5 minutes waiting for the next scheduled poll.
    critical_alerts = [a for a in alerts if _severity(a) == "CRITICAL"]
    for a in critical_alerts:
        alert_id = str(a.alert_id) if hasattr(a, "alert_id") else None
        if not alert_id:
            continue
        try:
            import asyncio as _asyncio
            from backend.agents.orchestrator import handle_critical_alert
            try:
                loop = _asyncio.get_running_loop()
                # We're inside an event loop — schedule as a fire-and-forget task
                loop.create_task(handle_critical_alert(alert_id))
            except RuntimeError:
                # No running loop (e.g. BackgroundScheduler thread) — use run_coroutine_threadsafe
                try:
                    loop = _asyncio.get_event_loop()
                    _asyncio.run_coroutine_threadsafe(handle_critical_alert(alert_id), loop)
                except Exception:
                    # Last resort: blocking call in current thread
                    _asyncio.run(handle_critical_alert(alert_id))
            logger.warning(
                "CRITICAL alert %s (%s) — PM reactive cycle triggered", alert_id, a.trigger
            )
        except Exception as exc:
            logger.warning(
                "CRITICAL alert: handle_critical_alert failed — %s "
                "(PM will catch it on next 5-min poll)",
                exc,
            )

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


