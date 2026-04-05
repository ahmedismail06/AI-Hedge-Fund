"""
Portfolio Manager Agent (Orchestrator) — Component 8.

Coordinates the autonomous approval pass: polls PENDING_APPROVAL positions
every 5 minutes and auto-approves those with conviction_score >= 8.5 when
AUTONOMOUS mode is active, subject to:
  - Daily drawdown guard (>5% intraday loss → suspend autonomous mode for the day)
  - CRITICAL risk alert gate (blocks all approvals until resolved)

Does NOT replace individual agent schedulers (macro, screening, research,
portfolio, risk, execution). This is an additive coordination layer.

Entry points:
  run_orchestrator_cycle(portfolio_value)  — async, one approval pass
  create_orchestrator_scheduler()          — BackgroundScheduler, 5-min interval
"""

from dotenv import load_dotenv

load_dotenv()

import asyncio
import logging
import os
from datetime import date, datetime
from typing import Optional

from backend.memory.vector_store import _get_client

logger = logging.getLogger(__name__)

AUTONOMOUS_CONVICTION_THRESHOLD = 8.5
DAILY_DRAWDOWN_SUSPEND_PCT = 0.05   # 5% intraday drawdown triggers suspension


# ── Config helpers ────────────────────────────────────────────────────────────


def _get_config() -> dict:
    """Read the single orchestrator_config row. Returns SUPERVISED defaults if row missing."""
    try:
        resp = _get_client().table("orchestrator_config").select("*").limit(1).execute()
        if resp.data:
            return resp.data[0]
    except Exception as exc:
        logger.warning("_get_config: Supabase unavailable — %s", exc)
    return {"mode": "SUPERVISED", "suspended_until": None}


def _set_mode(mode: str) -> dict:
    """Upsert mode in orchestrator_config. Returns updated row."""
    try:
        client = _get_client()
        existing = _get_config()
        row_id = existing.get("id")
        now = datetime.utcnow().isoformat()

        if row_id:
            resp = (
                client.table("orchestrator_config")
                .update({"mode": mode, "updated_at": now})
                .eq("id", row_id)
                .execute()
            )
            return resp.data[0] if resp.data else {**existing, "mode": mode}
        else:
            resp = (
                client.table("orchestrator_config")
                .insert({"mode": mode, "updated_at": now})
                .execute()
            )
            return resp.data[0] if resp.data else {"mode": mode}
    except Exception as exc:
        logger.error("_set_mode: Supabase update failed — %s", exc)
        return {"mode": mode}


def _set_suspended_until(d: Optional[date]) -> None:
    """Set or clear suspended_until in orchestrator_config."""
    try:
        client = _get_client()
        existing = _get_config()
        row_id = existing.get("id")
        value = d.isoformat() if d else None
        now = datetime.utcnow().isoformat()

        if row_id:
            client.table("orchestrator_config").update(
                {"suspended_until": value, "updated_at": now}
            ).eq("id", row_id).execute()
        else:
            client.table("orchestrator_config").insert(
                {"mode": "SUPERVISED", "suspended_until": value, "updated_at": now}
            ).execute()
    except Exception as exc:
        logger.error("_set_suspended_until: failed — %s", exc)


def _is_suspended_today() -> bool:
    """Return True if suspended_until == date.today()."""
    config = _get_config()
    suspended_until = config.get("suspended_until")
    if not suspended_until:
        return False
    try:
        suspended_date = date.fromisoformat(str(suspended_until)[:10])
        return suspended_date == date.today()
    except (ValueError, TypeError):
        return False


# ── Audit log ─────────────────────────────────────────────────────────────────


def _log_event(
    event_type: str,
    *,
    agent: Optional[str] = None,
    position_id: Optional[str] = None,
    ticker: Optional[str] = None,
    conviction_score: Optional[float] = None,
    detail: Optional[str] = None,
    mode_snapshot: Optional[str] = None,
) -> None:
    """Insert one row into orchestrator_log. Never raises."""
    try:
        row = {
            "run_date": date.today().isoformat(),
            "event_type": event_type,
            "mode_snapshot": mode_snapshot,
        }
        if agent:
            row["agent"] = agent
        if position_id:
            row["position_id"] = position_id
        if ticker:
            row["ticker"] = ticker
        if conviction_score is not None:
            row["conviction_score"] = float(conviction_score)
        if detail:
            row["detail"] = detail

        _get_client().table("orchestrator_log").insert(row).execute()
    except Exception as exc:
        logger.warning("_log_event: failed to insert log row — %s", exc)


# ── Drawdown check ────────────────────────────────────────────────────────────


def _check_daily_drawdown(portfolio_value: float) -> tuple[bool, float]:
    """
    Compute intraday drawdown from OPEN positions (entry_price vs current_price).
    Positions with null current_price are excluded (freshly opened, no price yet).
    If drawdown > 5%, calls _set_suspended_until(today) and logs SUSPEND.
    Returns (breached: bool, drawdown_pct: float).
    """
    try:
        client = _get_client()
        resp = (
            client.table("positions")
            .select("entry_price,current_price,share_count")
            .eq("status", "OPEN")
            .execute()
        )
        positions = resp.data or []
    except Exception as exc:
        logger.warning("_check_daily_drawdown: positions read failed — %s", exc)
        return False, 0.0

    loss = 0.0
    for p in positions:
        entry = p.get("entry_price")
        current = p.get("current_price")
        shares = p.get("share_count")
        if entry is None or current is None or shares is None:
            continue
        try:
            loss += (float(entry) - float(current)) * float(shares)
        except (TypeError, ValueError):
            continue

    if portfolio_value <= 0:
        return False, 0.0

    drawdown_pct = loss / portfolio_value
    if drawdown_pct > DAILY_DRAWDOWN_SUSPEND_PCT:
        _set_suspended_until(date.today())
        _log_event(
            "SUSPEND",
            agent="orchestrator",
            detail=f"Intraday drawdown {drawdown_pct:.2%} exceeded {DAILY_DRAWDOWN_SUSPEND_PCT:.0%} threshold. Autonomous mode suspended for today.",
        )
        logger.warning(
            "Orchestrator: daily drawdown %.2f%% exceeded threshold — autonomous mode suspended for today",
            drawdown_pct * 100,
        )
        return True, drawdown_pct

    return False, drawdown_pct


# ── CRITICAL alert gate ───────────────────────────────────────────────────────


def _has_critical_alerts() -> bool:
    """Return True if any unresolved CRITICAL risk alerts exist in Supabase."""
    try:
        resp = (
            _get_client()
            .table("risk_alerts")
            .select("id")
            .eq("severity", "CRITICAL")
            .eq("resolved", False)
            .execute()
        )
        return bool(resp.data)
    except Exception as exc:
        logger.warning("_has_critical_alerts: check failed — %s", exc)
        return False


# ── Auto-approve helpers ──────────────────────────────────────────────────────


def _approve_position_direct(position_id: str) -> bool:
    """
    Direct Supabase update: PENDING_APPROVAL → APPROVED for one position.
    Replicates the DB write in api/portfolio.py — no HTTP round-trip.
    Returns True on success.
    """
    try:
        resp = (
            _get_client()
            .table("positions")
            .update({"status": "APPROVED"})
            .eq("id", position_id)
            .eq("status", "PENDING_APPROVAL")
            .execute()
        )
        return bool(resp.data)
    except Exception as exc:
        logger.error("_approve_position_direct(%s): failed — %s", position_id, exc)
        return False


async def _run_autonomous_approval_pass(portfolio_value: float) -> dict:
    """
    Scan all PENDING_APPROVAL positions with conviction_score >= 8.5.
    For each: check CRITICAL gate (abort all if blocked), then approve directly.
    Returns dict: {auto_approved: [position_ids], critical_blocked: bool}.
    """
    approved: list[str] = []
    critical_blocked = False

    try:
        client = _get_client()
        resp = (
            client.table("positions")
            .select("id,ticker,conviction_score")
            .eq("status", "PENDING_APPROVAL")
            .gte("conviction_score", AUTONOMOUS_CONVICTION_THRESHOLD)
            .execute()
        )
        candidates = resp.data or []
    except Exception as exc:
        logger.error("_run_autonomous_approval_pass: positions read failed — %s", exc)
        return {"auto_approved": approved, "critical_blocked": False}

    if not candidates:
        logger.debug("Orchestrator: no PENDING_APPROVAL candidates meet conviction threshold")
        return {"auto_approved": approved, "critical_blocked": False}

    config = _get_config()
    mode = config.get("mode", "SUPERVISED")

    for pos in candidates:
        position_id = pos["id"]
        ticker = pos.get("ticker", "UNKNOWN")
        conviction = pos.get("conviction_score")

        # Re-check CRITICAL gate before each approval
        if _has_critical_alerts():
            critical_blocked = True
            _log_event(
                "CRITICAL_BLOCK",
                agent="orchestrator",
                position_id=position_id,
                ticker=ticker,
                conviction_score=conviction,
                detail="Unresolved CRITICAL risk alert(s) block auto-approval.",
                mode_snapshot=mode,
            )
            logger.warning(
                "Orchestrator: CRITICAL alert blocks auto-approval of %s (conviction=%.2f)",
                ticker,
                conviction or 0,
            )
            break  # abort the entire pass

        success = _approve_position_direct(position_id)
        if success:
            approved.append(position_id)
            _log_event(
                "AUTO_APPROVE",
                agent="orchestrator",
                position_id=position_id,
                ticker=ticker,
                conviction_score=conviction,
                detail=f"Auto-approved: conviction {conviction:.2f} >= {AUTONOMOUS_CONVICTION_THRESHOLD}",
                mode_snapshot=mode,
            )
            logger.info(
                "Orchestrator: AUTO_APPROVE %s (id=%s, conviction=%.2f)",
                ticker,
                position_id,
                conviction or 0,
            )

    return {"auto_approved": approved, "critical_blocked": critical_blocked}


# ── Main cycle ────────────────────────────────────────────────────────────────


async def run_orchestrator_cycle(portfolio_value: Optional[float] = None) -> dict:
    """
    One orchestrator approval pass.

    Steps:
      1. Load config (mode, suspension state).
      2. Log CYCLE_START.
      3. Check daily drawdown — if breached in AUTONOMOUS mode, mark suspended.
      4. If AUTONOMOUS and not suspended: run autonomous approval pass.
      5. Log CYCLE_END.
      6. Return summary dict.
    """
    if portfolio_value is None:
        portfolio_value = float(os.getenv("PORTFOLIO_VALUE", "25000"))

    config = _get_config()
    mode = config.get("mode", "SUPERVISED")

    _log_event("CYCLE_START", agent="orchestrator", mode_snapshot=mode)
    logger.info("Orchestrator cycle start — mode=%s", mode)

    summary = {
        "mode": mode,
        "suspended": False,
        "drawdown_pct": 0.0,
        "auto_approved": [],
        "critical_blocked": False,
        "skipped_reason": None,
    }

    # Drawdown check (always runs regardless of mode)
    breached, drawdown_pct = _check_daily_drawdown(portfolio_value)
    summary["drawdown_pct"] = round(drawdown_pct, 4)

    if mode == "AUTONOMOUS":
        suspended = _is_suspended_today()
        summary["suspended"] = suspended

        if breached or suspended:
            summary["skipped_reason"] = "daily drawdown suspension"
            logger.info("Orchestrator: autonomous pass skipped — daily drawdown suspension")
        else:
            pass_result = await _run_autonomous_approval_pass(portfolio_value)
            summary["auto_approved"] = pass_result["auto_approved"]
            summary["critical_blocked"] = pass_result["critical_blocked"]
    else:
        summary["skipped_reason"] = "SUPERVISED mode — human approval required"

    _log_event(
        "CYCLE_END",
        agent="orchestrator",
        detail=(
            f"mode={mode} suspended={summary['suspended']} "
            f"auto_approved={len(summary['auto_approved'])} "
            f"critical_blocked={summary['critical_blocked']}"
        ),
        mode_snapshot=mode,
    )
    logger.info(
        "Orchestrator cycle end — auto_approved=%d critical_blocked=%s",
        len(summary["auto_approved"]),
        summary["critical_blocked"],
    )

    return summary


# ── Scheduler ─────────────────────────────────────────────────────────────────


def create_orchestrator_scheduler():
    """
    Return a configured (not yet started) BackgroundScheduler.
    Fires every 5 minutes — matches execution agent cadence so APPROVED
    positions are picked up by the next execution cycle within minutes.
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        lambda: asyncio.run(run_orchestrator_cycle()),
        trigger=IntervalTrigger(seconds=300),
        id="orchestrator_approval_poll",
        name="Orchestrator Approval Pass (5m)",
        replace_existing=True,
    )
    return scheduler
