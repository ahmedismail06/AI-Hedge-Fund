"""
AI Portfolio Manager Agent — Component 8 v2.

Replaces the deterministic approval-pass orchestrator with a Claude-powered
reasoning agent that makes all portfolio decisions autonomously. Every
material decision (entry, exit, rebalance, crisis, pre-earnings) is routed
through a Claude extended-thinking call with a category-specific prompt.

Architecture:
  - APScheduler runs run_pm_cycle() every 5 minutes (market hours + after-hours for cleanup)
    - Macro (7 AM), Screener (4 PM), Research queue (5:00 PM) crons are preserved unchanged
  - Hard blocks (15% position cap, 200% gross ceiling, -10% daily loss halt) are enforced
    in Python BEFORE Claude is called — Claude cannot override them
  - Every decision is logged to pm_decisions before any execution action
  - Reactive handlers allow the Risk Agent and Macro Agent to trigger immediate cycles

Entry points:
  run_pm_cycle(cycle_type)          — synchronous, one full decision cycle
  handle_critical_alert(alert_id)   — async reactive handler for CRITICAL alerts
  handle_regime_change(new_regime)  — async reactive handler for regime shifts
  create_orchestrator_scheduler()   — BackgroundScheduler (same export name as v1)
"""

from dotenv import load_dotenv

load_dotenv()

import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import anthropic

from backend.memory.vector_store import _get_client
from backend.notifications.events import notify_event

logger = logging.getLogger(__name__)

def _clean_float(value: Any, default: float = 0.0) -> float:
    """Strip currency symbols, commas, and percentages from LLM numeric outputs."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").replace("%", "").strip()
        try:
            # If Claude returned "20" for 20%, convert it to 0.20
            val = float(cleaned)
            if "%" in str(value) and val > 1.0:
                val = val / 100.0
            return val
        except ValueError:
            return default
    return default

# ── Hard-block thresholds (code-enforced, never passed to Claude) ─────────────
_MAX_POSITION_PCT = 0.15          # 15% single-position hard cap
_GROSS_EXPOSURE_CEILING = 2.00    # 200% gross exposure absolute max
_DAILY_LOSS_HALT_PCT = 0.10       # -10% intraday drawdown → halt all trading
_STOP_PROXIMITY_TRIGGER = 0.03    # Trigger EXIT_TRIM review when within 3% of stop
_EARNINGS_LOOKAHEAD_DAYS = 14     # Pre-earnings review window
_DEPLOY_CASH_MIN_SCORE = 6.5      # Minimum composite score required to queue research for DEPLOY_CASH

# ── Cycle deduplication state (Change 2) ─────────────────────────────────────
_FINGERPRINT_TTL_SECONDS = 1800   # 30 minutes before forced re-evaluation
_last_cycle_state: Dict[str, Any] = {
    "fingerprint": None,
    "timestamp": None,
}

# ── DEPLOY_CASH cooldown — prevents re-triggering the pipeline every 30 min ──
_DEPLOY_CASH_COOLDOWN_SECONDS = 14400  # 4 hours
_deploy_cash_triggered_at: Optional[datetime] = None

# ── Event scan throttle — prevents excessive Polygon API calls ───────────────
_last_event_scan: Dict[str, datetime] = {}


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _get_pm_config() -> Dict[str, Any]:
    """Read pm_config row. Returns autonomous defaults if unavailable."""
    try:
        resp = _get_client().table("pm_config").select("*").limit(1).execute()
        if resp.data:
            return resp.data[0]
    except Exception as exc:
        logger.warning("_get_pm_config: Supabase unavailable — %s", exc)
    return {
        "id": 1,
        "mode": "autonomous",
        "cycle_interval_seconds": 300,
        "daily_loss_halt_triggered": False,
        "halted_until": None,
    }
def _acquire_pm_lock() -> bool:
    """Try to acquire the global PM lock. Returns True if acquired, False if already locked.
    Includes a 2-hour 'steal' timeout to prevent lock-death.
    """
    try:
        client = _get_client()
        # Read current state
        resp = client.table("pm_config").select("pm_is_running, pm_lock_timestamp").eq("id", 1).single().execute()
        if resp.data and resp.data.get("pm_is_running"):
            # Check for lock timeout (2 hours)
            lock_ts_str = resp.data.get("pm_lock_timestamp")
            if lock_ts_str:
                try:
                    lock_ts = datetime.fromisoformat(lock_ts_str.replace("Z", "+00:00"))
                    if (datetime.now(timezone.utc) - lock_ts).total_seconds() > 7200:
                        logger.warning("_acquire_pm_lock: lock timed out (>2h) — stealing lock.")
                    else:
                        return False  # Lock is fresh and held by another process
                except Exception:
                    return False
            else:
                return False
        
        # Acquire lock
        now_iso = datetime.now(timezone.utc).isoformat()
        client.table("pm_config").update({
            "pm_is_running": True,
            "pm_lock_timestamp": now_iso
        }).eq("id", 1).execute()
        return True
    except Exception as exc:
        logger.warning("_acquire_pm_lock: failed to check/set lock — %s", exc)
        return False

def _release_pm_lock() -> None:
    """Release the global PM lock."""
    try:
        _get_client().table("pm_config").update({"pm_is_running": False}).eq("id", 1).execute()
    except Exception as exc:
        logger.error("_release_pm_lock: failed to release lock — %s", exc)

def _set_halt(triggered: bool, halted_until: Optional[datetime] = None) -> None:
    """Update daily_loss_halt_triggered and optional halted_until in pm_config."""
    try:
        update = {
            "daily_loss_halt_triggered": triggered,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if halted_until is not None:
            update["halted_until"] = halted_until.isoformat()
        elif not triggered:
            update["halted_until"] = None

        _get_client().table("pm_config").update(update).eq("id", 1).execute()
    except Exception as exc:
        logger.error("_set_halt: failed — %s", exc)


def _log_pm_decision(decision: Dict[str, Any]) -> None:
    """Insert one decision row into pm_decisions. Never raises."""
    try:
        _get_client().table("pm_decisions").insert(decision).execute()
    except Exception as exc:
        logger.warning("_log_pm_decision: failed to persist decision — %s", exc)


def _next_decision_id() -> str:
    """Generate a unique decision ID: pm_YYYYMMDD_<4-char-uuid>."""
    suffix = uuid.uuid4().hex[:4].upper()
    return f"pm_{date.today().strftime('%Y%m%d')}_{suffix}"


def _next_cycle_id() -> str:
    now = datetime.now(timezone.utc)
    return f"cycle_{now.strftime('%Y%m%d_%H%M')}"


# ── Pre-Claude gating helpers (Changes 1–3) ───────────────────────────────────

def _count_pending_memos() -> int:
    """Count memos with status PENDING_PM_REVIEW. Returns 0 on error."""
    try:
        resp = (
            _get_client()
            .table("memos")
            .select("id", count="exact")
            .eq("status", "PENDING_PM_REVIEW")
            .execute()
        )
        return resp.count if resp.count is not None else len(resp.data or [])
    except Exception as exc:
        logger.warning("_count_pending_memos: failed — %s", exc)
        return 0


def _build_cycle_fingerprint(base_ctx: Dict[str, Any], pending_memo_count: int) -> Dict[str, Any]:
    """Build a hashable snapshot of the fields that determine whether a cycle is redundant."""
    return {
        "position_count": base_ctx.get("position_count", 0),
        "pending_memo_count": pending_memo_count,
        "active_alert_count": sum(
            1 for a in base_ctx.get("active_alerts", [])
            if a.get("severity") in ("CRITICAL", "BREACH")
        ),
        "regime": base_ctx.get("macro_regime", ""),
    }


def _fingerprints_match(fp_a: Dict[str, Any], fp_b: Dict[str, Any]) -> bool:
    keys = ("position_count", "pending_memo_count", "active_alert_count", "regime")
    return all(fp_a.get(k) == fp_b.get(k) for k in keys)


def _handle_empty_portfolio_deploy(
    cycle_id: str,
    cycle_type: str,
    regime: str,
) -> Dict[str, Any]:
    """
    Change 3: Empty portfolio + constructive regime → route to data pipeline.

    If today's screener results exist, trigger the research queue for the top 3
    candidates. If no screener results exist yet, trigger a fresh screener run.
    No Claude call is made.
    """
    today_str = date.today().isoformat()
    top_tickers: List[str] = []

    # Only pick rows that haven't been queued yet so we don't re-trigger research
    # that's already in-flight or completed.
    try:
        resp = (
            _get_client()
            .table("watchlist")
            .select("ticker,composite_score")
            .eq("run_date", today_str)
            .eq("queued_for_research", False)
            .gte("composite_score", _DEPLOY_CASH_MIN_SCORE)
            .order("composite_score", desc=True)
            .limit(3)
            .execute()
        )
        top_tickers = [row["ticker"] for row in (resp.data or [])]
    except Exception as exc:
        logger.warning("_handle_empty_portfolio_deploy: watchlist query failed — %s", exc)

    if top_tickers:
        # Mark rows as queued BEFORE triggering so a concurrent cycle can't double-queue.
        try:
            _get_client().table("watchlist").update({"queued_for_research": True}).in_(
                "ticker", top_tickers
            ).eq("run_date", today_str).execute()
        except Exception as exc:
            logger.error(
                "_handle_empty_portfolio_deploy: failed to set queued_for_research — %s", exc
            )
        action = "triggered_research_queue"
        detail = f"top candidates: {top_tickers}"
        try:
            _trigger_research_queue()
        except Exception as exc:
            logger.error(
                "_handle_empty_portfolio_deploy: research queue trigger failed — %s", exc
            )
    else:
        # No unqueued results for today — kick off the screener. The NEXT PM cycle
        # (≈5 min later) will land in the `top_tickers` branch above once it has data.
        action = "triggered_screener_run"
        detail = "no unqueued screener results found for today"
        try:
            threading.Thread(target=_trigger_screener, daemon=True).start()
        except Exception as exc:
            logger.error(
                "_handle_empty_portfolio_deploy: screener trigger failed — %s", exc
            )

    logger.info(
        "PM cycle [deploy_cash gate] regime=%s — %s (%s)", regime, action, detail
    )
    _log_event(
        "DEPLOY_CASH_ACTION",
        "PM",
        f"{action}: {detail}",
        mode_snapshot=regime,
    )

    return {
        "cycle_id": cycle_id,
        "cycle_type": cycle_type,
        "skipped": False,
        "reason": "deploy_cash_scheduler_action",
        "action": action,
        "detail": detail,
        "decisions_made": [],
    }


def _trigger_deploy_cash_pipeline() -> str:
    """
    Trigger the research/screener pipeline when Claude decides DEPLOY_CASH in a
    REBALANCE context (non-empty portfolio).  Mirrors _handle_empty_portfolio_deploy
    but is called from _route_decision rather than the pre-Claude fast path.

    Returns a short description of the action taken.
    """
    global _deploy_cash_triggered_at

    today_str = date.today().isoformat()
    top_tickers: List[str] = []
    try:
        resp = (
            _get_client()
            .table("watchlist")
            .select("ticker,composite_score")
            .eq("run_date", today_str)
            .eq("queued_for_research", False)
            .gte("composite_score", _DEPLOY_CASH_MIN_SCORE)
            .order("composite_score", desc=True)
            .limit(3)
            .execute()
        )
        top_tickers = [row["ticker"] for row in (resp.data or [])]
    except Exception as exc:
        logger.warning("_trigger_deploy_cash_pipeline: watchlist query failed — %s", exc)

    if not top_tickers:
        # No candidates yet — run screener in background
        try:
            threading.Thread(target=_trigger_screener, daemon=True).start()
            detail = "triggered_screener_background (no candidates found after screen)"
            logger.info("_trigger_deploy_cash_pipeline: screener triggered in background")
        except Exception as exc:
            logger.error("_trigger_deploy_cash_pipeline: screener trigger failed — %s", exc)
            detail = "screener_trigger_failed"
        
        _deploy_cash_triggered_at = datetime.now(timezone.utc)
        return detail

    if top_tickers:
        try:
            _get_client().table("watchlist").update({"queued_for_research": True}).in_(
                "ticker", top_tickers
            ).eq("run_date", today_str).execute()
        except Exception as exc:
            logger.error("_trigger_deploy_cash_pipeline: failed to set queued_for_research — %s", exc)
        try:
            _trigger_research_queue()
        except Exception as exc:
            logger.error("_trigger_deploy_cash_pipeline: research queue trigger failed — %s", exc)
        detail = f"queued_research: {top_tickers}"
    else:
        detail = "triggered_screener (no candidates found after screen)"

    _deploy_cash_triggered_at = datetime.now(timezone.utc)
    logger.info("PM: DEPLOY_CASH pipeline action — %s", detail)
    return detail


# ── Portfolio value (NAV) ─────────────────────────────────────────────────────

_PAPER_TRADING_NAV = 1_000_000.0   # fallback until real NAV tracking exists


def _compute_portfolio_value() -> float:
    """Return best-available portfolio NAV from Supabase data.

    Method:
      1. Sum share_count * current_price for all OPEN positions.
      2. Add cash balance from pm_config (column: cash_balance) if present.

    Returns 0.0 on any failure or if NAV cannot be determined.
    """
    try:
        client = _get_client()

        # Invested value: sum OPEN positions
        invested = 0.0
        try:
            pos_resp = (
                client.table("positions")
                .select("share_count,current_price")
                .eq("status", "OPEN")
                .execute()
            )
            for row in (pos_resp.data or []):
                qty = float(row.get("share_count") or 0)
                price = float(row.get("current_price") or 0)
                invested += qty * price
        except Exception as exc:
            logger.warning("_compute_portfolio_value: positions query failed — %s", exc)

        # Cash balance from pm_config
        cash = 0.0
        try:
            cfg_resp = (
                client.table("pm_config")
                .select("cash_balance")
                .eq("id", 1)
                .single()
                .execute()
            )
            cash_raw = (cfg_resp.data or {}).get("cash_balance")
            if cash_raw is not None:
                cash = float(cash_raw)
        except Exception as exc:
            logger.debug("_compute_portfolio_value: cash_balance not in pm_config — %s", exc)

        nav = invested + cash
        if nav <= 0:
            logger.error("_compute_portfolio_value: NAV computed as %.2f (invalid/empty)", nav)
            return 0.0
        return nav

    except Exception as exc:
        logger.error("_compute_portfolio_value: unexpected error — %s", exc)
        return 0.0


# ── Hard-block enforcement ────────────────────────────────────────────────────

def _check_hard_blocks(
    sizing_rec: Optional[Dict[str, Any]] = None,
    base_ctx: Optional[Dict[str, Any]] = None,
    dollar_amount: Optional[float] = None,
) -> Dict[str, bool]:
    """
    Pre-Claude hard block checks. Returns dict of check_name → passed (bool).
    Any False means execution must not proceed.

    Position cap precedence (most-to-least reliable):
      1. dollar_amount / _compute_portfolio_value()  — used when Claude returns dollar_amount
      2. sizing_rec["dollar_size"] / _compute_portfolio_value() — pre-computed sizing row
      3. sizing_rec["pct_of_portfolio"] — only as a last resort (may be stale)
    """
    blocks = {
        "position_cap_ok": True,
        "market_hours_ok": True,
        "gross_exposure_ok": True,
        "daily_loss_ok": True,
    }

    # Position size cap — always recompute weight from NAV
    dollar_to_check = dollar_amount
    if dollar_to_check is None and sizing_rec:
        raw_sz = sizing_rec.get("dollar_size")
        if raw_sz in (None, ""):
            dollar_to_check = None
        else:
            d = _clean_float(raw_sz)
            dollar_to_check = d if d else None

    if dollar_to_check:
        nav = _compute_portfolio_value()
        weight = dollar_to_check / nav
        if weight > _MAX_POSITION_PCT:
            blocks["position_cap_ok"] = False
            logger.warning(
                "Hard block: position weight %.1f%% (${:,.0f} / ${:,.0f}) exceeds 15%% cap".format(
                    dollar_to_check, nav
                ),
                weight * 100,
            )
    elif sizing_rec:
        # Fallback: use stored pct_of_portfolio only when no dollar figure available
        weight = _clean_float(sizing_rec.get("pct_of_portfolio", 0) or 0)
        if weight > _MAX_POSITION_PCT:
            blocks["position_cap_ok"] = False
            logger.warning(
                "Hard block: stored pct_of_portfolio %.1f%% exceeds 15%% cap",
                weight * 100,
            )

    # Gross exposure ceiling (only blocks new entries, not exits)
    if base_ctx:
        gross = base_ctx.get("portfolio_gross_exposure", 0.0)
        if gross >= _GROSS_EXPOSURE_CEILING:
            blocks["gross_exposure_ok"] = False
            logger.warning(
                "Hard block: gross exposure %.1f%% at/above 200%% ceiling",
                gross * 100,
            )

    # Daily loss halt
    config = _get_pm_config()
    if config.get("daily_loss_halt_triggered", False):
        blocks["daily_loss_ok"] = False

    return blocks


def _is_market_hours() -> bool:
    """Return True if current ET time is within regular market hours (9:30 AM – 4:00 PM Mon–Fri)."""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        # Fallback to manual offset if zoneinfo is unavailable
        # Note: This does not account for DST transitions perfectly but is a safe fallback.
        # Most environments running this (Python 3.9+) will have zoneinfo.
        now_utc = datetime.now(timezone.utc)
        # EST is UTC-5, EDT is UTC-4. Default to EST for safety or 
        # assume most of the time it's UTC-4/5.
        now_et = now_utc - timedelta(hours=5) 

    if now_et.weekday() >= 5:  # Saturday/Sunday
        return False
    
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et < market_close


def _check_intraday_drawdown(portfolio_value: float) -> float:
    """
    Compute intraday loss from OPEN positions. Returns drawdown as positive fraction.
    Sets daily_loss_halt_triggered if > _DAILY_LOSS_HALT_PCT.
    """
    try:
        resp = (
            _get_client()
            .table("positions")
            .select("entry_price,current_price,share_count")
            .eq("status", "OPEN")
            .execute()
        )
        positions = resp.data or []
    except Exception as exc:
        logger.warning("_check_intraday_drawdown: positions read failed — %s", exc)
        return 0.0

    loss = 0.0
    for p in positions:
        try:
            entry = float(p["entry_price"] or 0)
            current = float(p["current_price"] or 0)
            shares = float(p["share_count"] or 0)
            if entry > 0 and current > 0 and shares > 0:
                loss += (entry - current) * shares
        except (TypeError, ValueError):
            continue

    if portfolio_value <= 0:
        return 0.0

    drawdown_pct = loss / portfolio_value
    if drawdown_pct > _DAILY_LOSS_HALT_PCT:
        halted_until = datetime.now(timezone.utc).replace(hour=23, minute=59, second=59)
        _set_halt(True, halted_until)
        logger.critical(
            "PM: daily loss halt triggered — intraday drawdown %.2f%% exceeds %.0f%% threshold",
            drawdown_pct * 100,
            _DAILY_LOSS_HALT_PCT * 100,
        )
        notify_event("DAILY_LOSS_HALT", {
            "drawdown_pct": round(drawdown_pct * 100, 2),
            "halted_until": halted_until.isoformat(),
        })

    return max(0.0, drawdown_pct)


# ── Claude API call ───────────────────────────────────────────────────────────

def _call_claude(system_prompt: str, user_message: str) -> Dict[str, Any]:
    """
    Make a Claude API call with extended thinking (budget_tokens=8000).
    Returns the parsed JSON decision dict, or an empty dict on failure.
    """
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=16000,
            thinking={
                "type": "enabled",
                "budget_tokens": 8000,
            },
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        # Extract the text block (not the thinking block)
        for block in response.content:
            if block.type == "text":
                return json.loads(block.text)

        logger.error("_call_claude: no text block in response")
        return {}

    except json.JSONDecodeError as exc:
        logger.error("_call_claude: JSON parse failed — %s", exc)
        return {}
    except Exception as exc:
        logger.error("_call_claude: API call failed — %s", exc)
        return {}


# ── Context snapshot from base_ctx ───────────────────────────────────────────

def _snapshot(base_ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "gross_exposure": base_ctx.get("portfolio_gross_exposure", 0.0),
        "net_exposure": base_ctx.get("portfolio_net_exposure", 0.0),
        "position_count": base_ctx.get("position_count", 0),
        "cash_pct": base_ctx.get("cash_pct", 1.0),
        "macro_regime": base_ctx.get("macro_regime", "Transitional"),
        "active_critical_alerts": sum(
            1 for a in base_ctx.get("active_alerts", [])
            if a.get("severity") == "CRITICAL"
        ),
    }


# ── Memo status update (runs unconditionally after every Claude decision) ─────

_DECISION_TO_MEMO_STATUS = {
    "EXECUTE":      "APPROVED",
    "MODIFY_SIZE":  "APPROVED",
    "DEFER":        "DEFERRED",
    "REJECT":       "REJECTED",
    "WATCHLIST":    "WATCHLIST",
}


def _update_memo_after_decision(ticker: str, decision: str, memo_id: Optional[str] = None) -> None:
    """Set memos.status to reflect the PM's decision so the row is not re-evaluated.

    Called unconditionally after every Claude call regardless of mode (autonomous /
    supervised / market-closed).  Only touches NEW_ENTRY decisions; other categories
    (EXIT_TRIM, REBALANCE, etc.) are not memo-driven.

    Filters by memo_id when available (preferred) to avoid updating the wrong memo
    when a ticker has multiple rows in PENDING_PM_REVIEW simultaneously.

    For DEFER also sets deferred_until = NOW() + 24h so a future staleness gate
    can re-queue the ticker when the deferral window expires.
    """
    memo_status = _DECISION_TO_MEMO_STATUS.get(decision)
    if not memo_status:
        return  # NO_ACTION / HOLD / etc. — memo stays where it is

    try:
        update: Dict[str, Any] = {"status": memo_status}
        if memo_status == "DEFERRED":
            update["deferred_until"] = (
                datetime.now(timezone.utc) + timedelta(hours=24)
            ).isoformat()

        q = _get_client().table("memos").update(update)
        if memo_id:
            q = q.eq("id", memo_id).eq("status", "PENDING_PM_REVIEW")
        else:
            # Fallback: filter by ticker (less precise but safe for single-memo tickers)
            q = q.eq("ticker", ticker).eq("status", "PENDING_PM_REVIEW")
        q.execute()

        logger.info("_update_memo_after_decision: %s → memos.status=%s", ticker, memo_status)
    except Exception as exc:
        logger.warning(
            "_update_memo_after_decision: failed for %s (decision=%s) — %s",
            ticker, decision, exc,
        )


# ── Decision routing ──────────────────────────────────────────────────────────

def _route_decision(
    decision_data: Dict[str, Any],
    decision_record: Dict[str, Any],
    portfolio_value: Optional[float] = None,
    auto_approve: bool = True,
) -> str:
    """
    After Claude decides, route the action to the appropriate Supabase update.
    Returns the final execution_status string.

    portfolio_value: NAV to pass to run_portfolio_sizing so sizing and the hard
    block both use the same number.  If None, falls back to _compute_portfolio_value().
    """
    if portfolio_value is None or portfolio_value <= 0:
        portfolio_value = _compute_portfolio_value()
    decision = decision_data.get("decision", "NO_ACTION")
    category = decision_record.get("category", "")
    ticker = decision_record.get("ticker")
    memo_id = decision_record.get("memo_id")

    try:
        client = _get_client()

        if category == "NEW_ENTRY" and decision == "EXECUTE":
            # autonomous: size + APPROVED → execution immediately
            # supervised: size + PENDING_APPROVAL → waits for human
            if ticker and memo_id:
                try:
                    from backend.agents.portfolio_agent import run_portfolio_sizing
                    import asyncio as _asyncio
                    import concurrent.futures as _cf
                    # Run in a new thread to avoid "event loop already running" inside
                    # FastAPI/APScheduler contexts where asyncio.run() would fail.
                    with _cf.ThreadPoolExecutor(max_workers=1) as pool:
                        pool.submit(
                            _asyncio.run,
                            run_portfolio_sizing(
                                memo_id=memo_id,
                                portfolio_value=portfolio_value,
                                auto_approve=auto_approve,
                            ),
                        ).result()
                    logger.info("PM: EXECUTE — sized position for %s (auto_approve=%s)", ticker, auto_approve)
                    return "SENT_TO_EXECUTION" if auto_approve else "PENDING_HUMAN"
                except Exception as exc:
                    logger.warning("PM: EXECUTE sizing failed for %s — %s", ticker, exc)
            return "BLOCKED"

        elif category == "NEW_ENTRY" and decision == "MODIFY_SIZE":
            # Size with PM's override values and write directly as APPROVED.
            # Memo status is already set to APPROVED by _update_memo_after_decision.
            action = decision_data.get("action_details", {})
            raw_dollar = action.get("dollar_amount")
            new_dollar = None if raw_dollar in (None, "") else _clean_float(raw_dollar)
            raw_shares = action.get("shares")
            new_shares = None if raw_shares in (None, "") else int(_clean_float(raw_shares))
            if ticker and memo_id:
                try:
                    from backend.agents.portfolio_agent import run_portfolio_sizing
                    import asyncio as _asyncio
                    import concurrent.futures as _cf
                    # Run in a new thread to avoid "event loop already running" inside
                    # FastAPI/APScheduler contexts where asyncio.run() would fail.
                    with _cf.ThreadPoolExecutor(max_workers=1) as pool:
                        pool.submit(
                            _asyncio.run,
                            run_portfolio_sizing(
                                memo_id=memo_id,
                                portfolio_value=portfolio_value,
                                auto_approve=auto_approve,
                                override_dollar_amount=new_dollar
                            ),
                        ).result()
                    # Apply PM's size override on top of Kelly sizing
                    update: Dict[str, Any] = {}
                    if new_shares:
                        update["share_count"] = new_shares
                    if new_dollar:
                        update["dollar_amount"] = new_dollar
                    if update:
                        pos_status = "APPROVED" if auto_approve else "PENDING_APPROVAL"
                        client.table("positions").update(update).eq("ticker", ticker).eq(
                            "status", pos_status
                        ).execute()
                    logger.info("PM: MODIFY_SIZE — sized position for %s (auto_approve=%s)", ticker, auto_approve)
                    return "SENT_TO_EXECUTION" if auto_approve else "PENDING_HUMAN"
                except Exception as exc:
                    logger.warning("PM: MODIFY_SIZE sizing failed for %s — %s", ticker, exc)
            return "BLOCKED"

        elif category == "NEW_ENTRY" and decision == "DEFER":
            # Memo status + deferred_until handled by _update_memo_after_decision.
            return "DEFERRED"

        elif category == "NEW_ENTRY" and decision == "REJECT":
            if ticker:
                # Memo status handled by _update_memo_after_decision.
                # Also reject the pending position so it doesn't linger.
                q = client.table("positions").update({"status": "REJECTED"}).eq("ticker", ticker).eq("status", "PENDING_APPROVAL")
                if memo_id:
                    q = q.eq("memo_id", memo_id)
                q.execute()
            return "BLOCKED"

        elif category == "NEW_ENTRY" and decision == "WATCHLIST":
            # Memo status handled by _update_memo_after_decision.
            return "DEFERRED"

        elif category == "EXIT_TRIM" and decision in ("TRIM", "CLOSE"):
            if ticker:
                update = {
                    "exit_action": decision,
                    "exit_trim_pct": _clean_float(decision_data.get("action_details", {}).get("trim_pct")),
                }
                client.table("positions").update(update).eq(
                    "ticker", ticker
                ).eq("status", "OPEN").execute()
                logger.info(
                    "PM: %s decision for %s — exit_action written to positions",
                    decision,
                    ticker,
                )
            return "SENT_TO_EXECUTION"

        elif category == "CRISIS" and decision == "HALT_NEW_ENTRIES":
            # Set pm_config to halt new entries (similar to supervised mode temporarily)
            client.table("pm_config").update({
                "daily_loss_halt_triggered": True,
                "halted_until": (
                    datetime.now(timezone.utc) + timedelta(hours=4)
                ).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", 1).execute()
            logger.warning("PM: CRISIS — halting new entries for 4 hours")
            notify_event("CRISIS_MODE", {
                "daily_loss_pct": decision_data.get("action_details", {}).get("daily_loss_pct", "—"),
                "duration": "4 hours",
            })
            return "SENT_TO_EXECUTION"

        elif category == "EXIT_TRIM" and decision == "ADD":
            # Add to an existing OPEN position.  Writes exit_action=ADD + add_pct so the
            # execution agent (future enhancement) can place an incremental BUY.
            if ticker:
                action = decision_data.get("action_details", {})
                update: Dict[str, Any] = {"exit_action": "ADD"}
                if action.get("add_pct") not in (None, ""):
                    update["exit_trim_pct"] = _clean_float(action.get("add_pct"))
                client.table("positions").update(update).eq(
                    "ticker", ticker
                ).eq("status", "OPEN").execute()
                logger.info("PM: EXIT_TRIM ADD for %s — exit_action=ADD written to positions", ticker)
            return "SENT_TO_EXECUTION"

        elif category == "REBALANCE" and decision == "REBALANCE":
            # Trim overweight positions to bring gross/net exposure back toward regime caps.
            # PM should include trim_pct in action_details; default to 20% if absent.
            action = decision_data.get("action_details", {})
            trim_pct = _clean_float(action.get("trim_pct"), 0.20)
            if ticker:
                # Single-ticker rebalance (e.g. trim one concentrated position)
                client.table("positions").update({
                    "exit_action": "TRIM",
                    "exit_trim_pct": trim_pct,
                }).eq("ticker", ticker).eq("status", "OPEN").execute()
                logger.info("PM: REBALANCE TRIM %s %.0f%%", ticker, trim_pct * 100)
            else:
                # Portfolio-wide rebalance — trim all open positions proportionally
                client.table("positions").update({
                    "exit_action": "TRIM",
                    "exit_trim_pct": trim_pct,
                }).eq("status", "OPEN").execute()
                logger.info("PM: REBALANCE (portfolio-wide) — trim %.0f%% applied to all OPEN positions", trim_pct * 100)
            return "SENT_TO_EXECUTION"

        elif category == "REBALANCE" and decision == "RAISE_CASH":
            # Reduce gross exposure across all open positions to raise cash buffer.
            action = decision_data.get("action_details", {})
            trim_pct = _clean_float(action.get("trim_pct"), 0.30)
            client.table("positions").update({
                "exit_action": "TRIM",
                "exit_trim_pct": trim_pct,
            }).eq("status", "OPEN").execute()
            logger.warning("PM: REBALANCE RAISE_CASH — trim %.0f%% written to all OPEN positions", trim_pct * 100)
            return "SENT_TO_EXECUTION"

        elif category == "CRISIS" and decision == "REDUCE_EXPOSURE":
            # Trim all open positions to reduce portfolio gross exposure.
            action = decision_data.get("action_details", {})
            trim_pct = _clean_float(action.get("trim_pct"), 0.50)
            client.table("positions").update({
                "exit_action": "TRIM",
                "exit_trim_pct": trim_pct,
            }).eq("status", "OPEN").execute()
            logger.warning(
                "PM: CRISIS REDUCE_EXPOSURE — trim %.0f%% written to all OPEN positions", trim_pct * 100
            )
            notify_event("CRISIS_MODE", {
                "action": "REDUCE_EXPOSURE",
                "trim_pct": trim_pct,
            })
            return "SENT_TO_EXECUTION"

        elif category == "CRISIS" and decision == "LIQUIDATE_TO_TARGET":
            # Close all open positions immediately.
            action = decision_data.get("action_details", {})
            client.table("positions").update({"exit_action": "CLOSE"}).eq(
                "status", "OPEN"
            ).execute()
            logger.critical(
                "PM: CRISIS LIQUIDATE_TO_TARGET — exit_action=CLOSE written to all OPEN positions (target_exposure=%s)",
                action.get("target_exposure", "0%"),
            )
            notify_event("CRISIS_MODE", {
                "action": "LIQUIDATE_TO_TARGET",
                "target_exposure": action.get("target_exposure"),
            })
            return "SENT_TO_EXECUTION"

        elif category == "CRISIS" and decision == "HEDGE":
            # Phase 1: no hedging instruments available.  Decision is logged; no execution action.
            logger.warning(
                "PM: CRISIS HEDGE — no hedging instruments in Phase 1; decision logged, manual intervention required"
            )
            notify_event("CRISIS_MODE", {
                "action": "HEDGE",
                "note": "Phase 1 no-op — manual intervention required",
            })
            return "NO_ACTION"

        elif category == "PRE_EARNINGS" and decision == "SIZE_UP":
            # Add to position ahead of earnings.  Uses exit_action=ADD (same as EXIT_TRIM+ADD).
            if ticker:
                action = decision_data.get("action_details", {})
                update = {"exit_action": "ADD"}
                if action.get("add_pct") not in (None, ""):
                    update["exit_trim_pct"] = _clean_float(action.get("add_pct"))
                client.table("positions").update(update).eq(
                    "ticker", ticker
                ).eq("status", "OPEN").execute()
                logger.info("PM: PRE_EARNINGS SIZE_UP for %s — exit_action=ADD written to positions", ticker)
            return "SENT_TO_EXECUTION"

        elif category == "PRE_EARNINGS" and decision == "TRIM":
            # Reduce position size ahead of earnings.
            if ticker:
                action = decision_data.get("action_details", {})
                trim_pe = _clean_float(action.get("trim_pct"))
                update = {
                    "exit_action": "TRIM",
                    "exit_trim_pct": trim_pe,
                }
                client.table("positions").update(update).eq(
                    "ticker", ticker
                ).eq("status", "OPEN").execute()
                logger.info(
                    "PM: PRE_EARNINGS TRIM for %s — exit_action=TRIM (%.0f%%) written to positions",
                    ticker, trim_pe * 100,
                )
            return "SENT_TO_EXECUTION"

        elif category == "PRE_EARNINGS" and decision == "EXIT":
            # Close position entirely ahead of earnings.
            if ticker:
                client.table("positions").update({"exit_action": "CLOSE"}).eq(
                    "ticker", ticker
                ).eq("status", "OPEN").execute()
                logger.info("PM: PRE_EARNINGS EXIT for %s — exit_action=CLOSE written to positions", ticker)
            return "SENT_TO_EXECUTION"

    except Exception as exc:
        logger.error("_route_decision: routing failed for %s — %s", ticker, exc)

    if decision == "NO_ACTION":
        return "NO_ACTION"
    if decision in ("HOLD", "MONITOR"):
        return "NO_ACTION"

    if decision == "DEPLOY_CASH":
        if category == "REBALANCE":
            detail = _trigger_deploy_cash_pipeline()
            _log_event(
                "DEPLOY_CASH_ACTION",
                "PM",
                detail,
                mode_snapshot=decision_record.get("context_snapshot", {}).get("macro_regime", ""),
            )
            return "TRIGGERED_PIPELINE"
        return "NO_ACTION"

    return "DEFERRED"


# ── Scan for actionable items ─────────────────────────────────────────────────

def _scan_actionable_items(base_ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Scan Supabase for items requiring a PM decision this cycle.
    Returns list of dicts: {category, data, priority}
    """
    items: List[Dict[str, Any]] = []

    # 1. New memos pending PM review
    try:
        resp = (
            _get_client()
            .table("memos")
            .select("*")
            .eq("status", "PENDING_PM_REVIEW")
            .order("created_at", desc=False)
            .limit(5)
            .execute()
        )
        for memo in (resp.data or []):
            items.append({"category": "NEW_ENTRY", "data": {"memo": memo}, "priority": 2})
    except Exception as exc:
        logger.warning("_scan_actionable_items: memos scan failed — %s", exc)

    # 2. Unprocessed CRITICAL/BREACH alerts not yet in pm_decisions
    try:
        alerts = base_ctx.get("active_alerts", [])
        # Get alert IDs already processed
        processed_ids: set = set()
        try:
            resp = (
                _get_client()
                .table("pm_decisions")
                .select("action_details")
                .eq("category", "CRISIS")
                .order("timestamp", desc=True)
                .limit(20)
                .execute()
            )
            for row in (resp.data or []):
                ad = row.get("action_details") or {}
                if ad.get("alert_id"):
                    processed_ids.add(ad["alert_id"])
        except Exception:
            pass

        for alert in alerts:
            if alert.get("id") not in processed_ids:
                priority = 0 if alert.get("severity") == "CRITICAL" else 1
                items.append({"category": "CRISIS", "data": {"alert": alert}, "priority": priority})
    except Exception as exc:
        logger.warning("_scan_actionable_items: alerts scan failed — %s", exc)

    # 3. Positions approaching stops (within 3%)
    today_str = date.today().isoformat()
    for p in base_ctx.get("positions", []):
        current = float(p.get("current_price") or 0)
        stop1 = float(p.get("stop_tier1") or 0)
        if current > 0 and stop1 > 0:
            proximity = (current - stop1) / current
            if 0 <= proximity < _STOP_PROXIMITY_TRIGGER:
                items.append({
                    "category": "EXIT_TRIM",
                    "data": {"position": p, "trigger": "stop_proximity"},
                    "priority": 1,
                })

    # 4. Positions with earnings within 14 days
    cutoff = date.today() + timedelta(days=_EARNINGS_LOOKAHEAD_DAYS)
    for p in base_ctx.get("positions", []):
        earnings_str = p.get("next_earnings_date")
        if earnings_str:
            try:
                earnings_date = date.fromisoformat(str(earnings_str)[:10])
                if date.today() <= earnings_date <= cutoff:
                    items.append({
                        "category": "PRE_EARNINGS",
                        "data": {
                            "position": p,
                            "earnings_data": {
                                "next_earnings_date": str(earnings_date),
                                "days_to_earnings": (earnings_date - date.today()).days,
                            },
                        },
                        "priority": 2,
                    })
            except (ValueError, TypeError):
                pass

    # 5. Rebalancing check (run once per cycle as lowest priority)
    
    # NEW: Check if the daily research cap (10) has been hit
    research_cap_hit = False
    try:
        cfg_resp = _get_client().table("pm_config").select("daily_research_count,daily_research_date").eq("id", 1).single().execute()
        if cfg_resp.data:
            today_str = date.today().isoformat()
            if str(cfg_resp.data.get("daily_research_date")) == today_str and int(cfg_resp.data.get("daily_research_count", 0)) >= 10:
                research_cap_hit = True
    except Exception as exc:
        logger.warning("_scan_actionable_items: failed to read research cap — %s", exc)

    open_positions = base_ctx.get("positions", [])
    new_entry_candidates = [i for i in items if i["category"] == "NEW_ENTRY"]
    
    # Skip if portfolio is empty and no pending memos
    if not open_positions and not new_entry_candidates:
        if research_cap_hit:
            logger.debug("_scan_actionable_items: skipping REBALANCE — empty portfolio but daily research cap is hit")
        else:
            logger.debug("_scan_actionable_items: skipping REBALANCE — no open positions and no pending memos")
        items.sort(key=lambda x: x["priority"])
        return items

    # Skip if DEPLOY_CASH pipeline is still in cooldown
    if (
        _deploy_cash_triggered_at is not None
        and (datetime.now(timezone.utc) - _deploy_cash_triggered_at).total_seconds()
        < _DEPLOY_CASH_COOLDOWN_SECONDS
    ):
        logger.debug(
            "_scan_actionable_items: skipping REBALANCE — DEPLOY_CASH pipeline triggered %.0f min ago (cooldown %d h)",
            (datetime.now(timezone.utc) - _deploy_cash_triggered_at).total_seconds() / 60,
            _DEPLOY_CASH_COOLDOWN_SECONDS // 3600,
        )
        items.sort(key=lambda x: x["priority"])
        return items

    # Skip REBALANCE entirely if the research cap is maxed out
    if research_cap_hit:
        logger.debug("_scan_actionable_items: skipping REBALANCE — daily research cap already maxed out")
        items.sort(key=lambda x: x["priority"])
        return items

    # Evaluate exposure drift
    regime = base_ctx.get("macro_regime", "Transitional")
    caps = base_ctx.get("regime_caps", {"gross": 1.20, "net": 0.20})
    gross = base_ctx.get("portfolio_gross_exposure", 0.0)
    net = base_ctx.get("portfolio_net_exposure", 0.0)
    gross_drift = abs(gross - caps["gross"])
    net_drift = abs(net - caps["net"])

    if gross_drift > 0.15 or net_drift > 0.20:
        items.append({
            "category": "REBALANCE",
            "data": {"trigger": "exposure_drift", "gross_drift": gross_drift, "net_drift": net_drift},
            "priority": 3,
        })

    # Sort by priority (lower = more urgent)
    items.sort(key=lambda x: x["priority"])
    return items


# ── Main PM cycle ─────────────────────────────────────────────────────────────

def run_pm_cycle(
    cycle_type: str = "SCHEDULED",
    portfolio_value: Optional[float] = None,
) -> Dict[str, Any]:
    """
    One full PM decision cycle.

    Steps:
      1. Load pm_config — skip if daily loss halt is active
      2. Check intraday drawdown — halt if > 10%
      3. Build base context (portfolio state, macro regime, alerts)
      4. Scan for actionable items
      5. For each item: build prompt → Claude call → enforce hard blocks → route → log
      6. Return cycle summary

    Called every 5 minutes by APScheduler, or reactively by handle_critical_alert().
    """
    if not _acquire_pm_lock():
        logger.info("PM cycle skipped — another PM instance is currently running.")
        return {
            "cycle_id": _next_cycle_id(),
            "cycle_type": cycle_type,
            "skipped": True,
            "reason": "pm_locked_by_other_process",
            "decisions_made": [],
        }

    try:
        if portfolio_value is None:
            from backend.broker.ibkr import get_portfolio_value as _get_pv
            portfolio_value = _get_pv()

        cycle_id = _next_cycle_id()
        cycle_start = datetime.now(timezone.utc)
        decisions_made: List[Dict[str, Any]] = []

        logger.info("PM cycle start — id=%s type=%s", cycle_id, cycle_type)

        # ── Step 1: Check halt state ──────────────────────────────────────────────
        config = _get_pm_config()
        if config.get("daily_loss_halt_triggered", False):
            halted_until = config.get("halted_until")
            if halted_until:
                try:
                    halt_dt = datetime.fromisoformat(str(halted_until).replace("Z", "+00:00"))
                    if datetime.now(timezone.utc) < halt_dt:
                        logger.info("PM cycle skipped — daily loss halt active until %s", halted_until)
                        return {
                            "cycle_id": cycle_id,
                            "cycle_type": cycle_type,
                            "skipped": True,
                            "reason": "daily_loss_halt",
                            "decisions_made": [],
                        }
                    else:
                        # Halt has expired — clear it
                        _set_halt(False)
                except Exception:
                    pass

        # ── Step 2: Intraday drawdown check ──────────────────────────────────────
        drawdown_pct = _check_intraday_drawdown(portfolio_value)
        if _get_pm_config().get("daily_loss_halt_triggered", False):
            return {
                "cycle_id": cycle_id,
                "cycle_type": cycle_type,
                "skipped": True,
                "reason": f"daily_loss_halt_triggered (drawdown={drawdown_pct:.2%})",
                "decisions_made": [],
            }

        # ── Step 3: Build base context ────────────────────────────────────────────
        from backend.agents.pm_prompts.base_context import build_base_context
        base_ctx = build_base_context(_get_client())
        base_ctx["portfolio_value_usd"] = _compute_portfolio_value()
        
        if base_ctx["portfolio_value_usd"] <= 0:
            logger.error("PM cycle aborted — portfolio value could not be computed (nav <= 0).")
            return {
                "cycle_id": cycle_id,
                "cycle_type": cycle_type,
                "skipped": True,
                "reason": "invalid_portfolio_value",
                "decisions_made": [],
            }

        # ── Step 3b: Event-driven research triggers (market hours only) ───────────
        try:
            _scan_event_triggers()
        except Exception as exc:
            logger.warning("PM cycle: event trigger scan failed — %s", exc)

        # ── Step 3c: Pre-Claude gates (Changes 2 and 3) ───────────────────────────
        pending_memo_count = _count_pending_memos()
        current_fp = _build_cycle_fingerprint(base_ctx, pending_memo_count)

        # Change 2: skip if state is identical to last cycle within 30 minutes
        last_fp = _last_cycle_state.get("fingerprint")
        last_ts = _last_cycle_state.get("timestamp")
        if (
            last_fp is not None
            and last_ts is not None
            and _fingerprints_match(current_fp, last_fp)
            and (datetime.now(timezone.utc) - last_ts).total_seconds() < _FINGERPRINT_TTL_SECONDS
        ):
            logger.debug(
                "PM cycle skipped — state unchanged (position=%d pending=%d alerts=%d regime=%s) within 30 min",
                current_fp["position_count"],
                current_fp["pending_memo_count"],
                current_fp["active_alert_count"],
                current_fp["regime"],
            )
            return {
                "cycle_id": cycle_id,
                "cycle_type": cycle_type,
                "skipped": True,
                "reason": "skipped_no_change",
                "decisions_made": [],
            }

        # Change 3: empty portfolio + constructive regime → delegate to scheduler,
        # no Claude call needed.
        regime_now = base_ctx.get("macro_regime", "Transitional")
        if (
            current_fp["position_count"] == 0
            and current_fp["pending_memo_count"] == 0
            and regime_now in ("Risk-On", "Transitional")
        ):
            result = _handle_empty_portfolio_deploy(cycle_id, cycle_type, regime_now)
            _last_cycle_state["fingerprint"] = current_fp
            _last_cycle_state["timestamp"] = datetime.now(timezone.utc)
            return result

        # ── Step 4: Scan actionable items ─────────────────────────────────────────
        items = _scan_actionable_items(base_ctx)

        if not items:
            logger.debug("PM cycle: no actionable items this cycle — logging as skipped_no_inventory")
            _last_cycle_state["fingerprint"] = current_fp
            _last_cycle_state["timestamp"] = datetime.now(timezone.utc)
            return {
                "cycle_id": cycle_id,
                "cycle_type": cycle_type,
                "skipped": True,
                "reason": "skipped_no_inventory",
                "items_evaluated": 0,
                "decisions_made": [],
                "portfolio_state": _snapshot(base_ctx),
            }

        logger.info("PM cycle: %d actionable items found", len(items))

        # ── Step 5: Evaluate each item ────────────────────────────────────────────
        mode = config.get("mode", "autonomous")

        for item in items:
            category = item["category"]
            data = item["data"]
            ticker = None

            try:
                # Build prompt
                system_prompt, user_message = _build_prompt(category, data, base_ctx)

                # Pre-Claude hard blocks
                sizing_rec = data.get("sizing_rec") or data.get("memo", {})
                hard_blocks = _check_hard_blocks(
                    sizing_rec=sizing_rec if category == "NEW_ENTRY" else None,
                    base_ctx=base_ctx if category == "NEW_ENTRY" else None,
                )

                ticker = (
                    data.get("memo", {}).get("ticker")
                    or data.get("position", {}).get("ticker")
                    or data.get("alert", {}).get("ticker")
                )

                # If hard blocks prevent execution, skip Claude call for new entries
                if category == "NEW_ENTRY" and not all(hard_blocks.values()):
                    failed = [k for k, v in hard_blocks.items() if not v]
                    decision_id = _next_decision_id()
                    record = _build_decision_record(
                        decision_id=decision_id,
                        category=category,
                        ticker=ticker,
                        decision="REJECT",
                        action_details={"hard_block_reason": failed},
                        reasoning=f"Hard block prevented evaluation: {', '.join(failed)}",
                        risk_assessment="Position violated hard constraints before PM evaluation.",
                        confidence=1.0,
                        context_snapshot=_snapshot(base_ctx),
                        hard_blocks_checked=hard_blocks,
                        execution_status="BLOCKED",
                    )
                    _log_pm_decision(record)
                    decisions_made.append({
                        "decision_id": decision_id,
                        "ticker": ticker,
                        "decision": "REJECT",
                        "category": category,
                    })
                    continue

                # Call Claude
                decision_data = _call_claude(system_prompt, user_message)
                if not decision_data:
                    logger.warning("PM: Claude returned empty decision for %s %s", category, ticker)
                    continue

                decision = decision_data.get("decision", "NO_ACTION")

                # Post-Claude hard block re-check for execution decisions
                if decision in ("EXECUTE", "MODIFY_SIZE") and category == "NEW_ENTRY":
                    action_details = decision_data.get("action_details", {})
                    raw_pm_dollar = action_details.get("dollar_amount")
                    post_dollar = None if raw_pm_dollar in (None, "") else _clean_float(raw_pm_dollar)
                    post_blocks = _check_hard_blocks(
                        dollar_amount=post_dollar,
                        base_ctx=base_ctx,
                    )
                    if not all(post_blocks.values()):
                        decision_data["decision"] = "DEFER"
                        decision_data["reasoning"] = (
                            decision_data.get("reasoning", "")
                            + " (Post-Claude hard block: position would exceed portfolio constraints.)"
                        )

                # Determine execution_status
                decision_id = _next_decision_id()
                record_template = _build_decision_record(
                    decision_id=decision_id,
                    category=category,
                    ticker=ticker,
                    decision=decision_data.get("decision", decision),
                    action_details=decision_data.get("action_details", {}),
                    reasoning=decision_data.get("reasoning", ""),
                    risk_assessment=decision_data.get("risk_assessment", ""),
                    confidence=_clean_float(decision_data.get("confidence"), 0.5),
                    context_snapshot=_snapshot(base_ctx),
                    hard_blocks_checked=hard_blocks,
                    execution_status="PENDING_HUMAN",  # filled below
                )

                # Always update memo status so this memo is not re-evaluated next cycle
                final_decision = decision_data.get("decision", decision)
                memo_id: Optional[str] = None
                if category == "NEW_ENTRY" and ticker:
                    memo_id = data.get("memo", {}).get("id")

                # Pass memo_id into record_template so _route_decision can use it
                record_template["memo_id"] = memo_id

                # Route decision (actual Supabase updates — positions/config)
                # autonomous → size + approve immediately, no human step
                # supervised → size but leave as PENDING_APPROVAL for human review
                pv = base_ctx.get("portfolio_value_usd")
                if _is_market_hours():
                    auto_approve = (mode == "autonomous")
                    execution_status = _route_decision(
                        decision_data, record_template,
                        portfolio_value=pv,
                        auto_approve=auto_approve,
                    )
                else:
                    # Non-market-hours: log the decision but defer order placement.
                    # Position SIZING (creating the DB row) is NOT market-hours-dependent —
                    # the execution agent is already market-hours gated and will place the
                    # order at the next open.  Only actual order placement needs to wait.
                    execution_status = "DEFERRED"
                    if category in ("CRISIS",):
                        # Crisis can act outside market hours (prep for next open)
                        execution_status = _route_decision(
                            decision_data, record_template,
                            auto_approve=(mode == "autonomous"),
                        )
                    elif category == "REBALANCE" and decision_data.get("decision") == "DEPLOY_CASH":
                        # Pipeline queueing (screener/research) is not market-hours-dependent
                        execution_status = _route_decision(
                            decision_data, record_template,
                            auto_approve=(mode == "autonomous"),
                        )
                    elif category == "NEW_ENTRY" and final_decision in ("EXECUTE", "MODIFY_SIZE"):
                        # Create the position row now so execution agent picks it up at market open.
                        # auto_approve=False forces PENDING_APPROVAL status — human still confirms
                        # in supervised mode; in autonomous mode we approve but execution is deferred.
                        execution_status = _route_decision(
                            decision_data, record_template,
                            portfolio_value=pv,
                            auto_approve=(mode == "autonomous"),
                        )
                        # Wrap status so the log reflects after-hours context
                        if execution_status == "SENT_TO_EXECUTION":
                            execution_status = "QUEUED_FOR_OPEN"

                    elif category in ("EXIT_TRIM", "PRE_EARNINGS") and final_decision not in (
                        "HOLD", "NO_ACTION", "MONITOR"
                    ):
                        # Writing exit_action to an OPEN position is a pure DB operation —
                        # no market-hours dependency.  Execution agent is already gated.
                        execution_status = _route_decision(
                            decision_data, record_template,
                            auto_approve=(mode == "autonomous"),
                        )

                    elif category == "REBALANCE" and final_decision in ("REBALANCE", "RAISE_CASH"):
                        # Trimming open positions is a pure DB write — route now so the
                        # execution agent can act at market open.
                        execution_status = _route_decision(
                            decision_data, record_template,
                            auto_approve=(mode == "autonomous"),
                        )

                record_template["execution_status"] = execution_status

                # Post-routing memo update (Fix for desynchronization)
                if category == "NEW_ENTRY" and ticker and execution_status != "BLOCKED":
                    _update_memo_after_decision(ticker, final_decision, memo_id=memo_id)

                # Embed alert_id in action_details for CRISIS deduplication
                if category == "CRISIS" and data.get("alert", {}).get("id"):
                    ad = record_template.get("action_details") or {}
                    ad["alert_id"] = data["alert"]["id"]
                    record_template["action_details"] = ad

                # Log decision FIRST, then it's already persisted regardless of routing outcome
                _log_pm_decision(record_template)

                # Slack notification for every PM decision
                notify_event("PM_DECISION", {
                    "category": category,
                    "decision": decision_data.get("decision", decision),
                    "ticker": ticker,
                    "execution_status": execution_status,
                    "confidence": decision_data.get("confidence"),
                    "reasoning": decision_data.get("reasoning", ""),
                })

                decisions_made.append({
                    "decision_id": decision_id,
                    "ticker": ticker,
                    "decision": decision_data.get("decision", decision),
                    "category": category,
                })

                logger.info(
                    "PM: %s %s → %s (confidence=%.2f, status=%s)",
                    category,
                    ticker or "portfolio",
                    decision_data.get("decision"),
                    decision_data.get("confidence", 0.5),
                    execution_status,
                )

            except Exception as exc:
                logger.error(
                    "PM cycle: error processing %s item (%s) — %s",
                    category,
                    ticker or "unknown",
                    exc,
                )

        # Update fingerprint after a full cycle so Change 2 can deduplicate
        # the next cycle if nothing material has changed.
        _last_cycle_state["fingerprint"] = current_fp
        _last_cycle_state["timestamp"] = datetime.now(timezone.utc)

        return {
            "cycle_id": cycle_id,
            "cycle_type": cycle_type,
            "items_evaluated": len(items),
            "decisions_made": decisions_made,
            "portfolio_state": _snapshot(base_ctx),
        }
    finally:
        _release_pm_lock()


def _build_prompt(
    category: str,
    data: Dict[str, Any],
    base_ctx: Dict[str, Any],
) -> tuple:
    """Dispatch to the correct prompt builder based on decision category."""
    from backend.agents.pm_prompts.new_entry import build_new_entry_prompt
    from backend.agents.pm_prompts.exit_trim import build_exit_trim_prompt
    from backend.agents.pm_prompts.rebalance import build_rebalance_prompt
    from backend.agents.pm_prompts.crisis import build_crisis_prompt
    from backend.agents.pm_prompts.pre_earnings import build_pre_earnings_prompt

    if category == "NEW_ENTRY":
        memo = data.get("memo", {})
        sizing_rec = data.get("sizing_rec") or None
        # Load Kelly sizing rec from positions table when not pre-populated
        if sizing_rec is None and memo.get("ticker"):
            try:
                resp = (
                    _get_client()
                    .table("positions")
                    .select(
                        "dollar_size,share_count,size_label,pct_of_portfolio,"
                        "entry_price,stop_loss_price"
                    )
                    .eq("ticker", memo["ticker"])
                    .eq("status", "PENDING_APPROVAL")
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
                sizing_rec = resp.data[0] if resp.data else None
            except Exception as exc:
                logger.warning("_build_prompt: positions query failed for %s — %s", memo.get("ticker"), exc)
                sizing_rec = None
        return build_new_entry_prompt(memo, sizing_rec, base_ctx)

    elif category == "EXIT_TRIM":
        position = data.get("position", {})
        ticker = position.get("ticker")
        position_alerts = [
            a for a in base_ctx.get("active_alerts", [])
            if a.get("ticker") == ticker
        ]
        # Try to load original memo
        original_memo = None
        if ticker:
            try:
                resp = (
                    _get_client()
                    .table("memos")
                    .select("*")
                    .eq("ticker", ticker)
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
                original_memo = resp.data[0] if resp.data else None
            except Exception:
                pass
        return build_exit_trim_prompt(position, position_alerts, base_ctx, original_memo)

    elif category == "REBALANCE":
        return build_rebalance_prompt(base_ctx)

    elif category == "CRISIS":
        alert = data.get("alert", {})
        return build_crisis_prompt(alert, base_ctx)

    elif category == "PRE_EARNINGS":
        position = data.get("position", {})
        earnings_data = data.get("earnings_data", {})
        ticker = position.get("ticker")
        original_memo = None
        if ticker:
            try:
                resp = (
                    _get_client()
                    .table("memos")
                    .select("*")
                    .eq("ticker", ticker)
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
                original_memo = resp.data[0] if resp.data else None
            except Exception:
                pass
        return build_pre_earnings_prompt(position, earnings_data, base_ctx, original_memo)

    raise ValueError(f"Unknown decision category: {category}")


def _build_decision_record(
    decision_id: str,
    category: str,
    ticker: Optional[str],
    decision: str,
    action_details: Dict[str, Any],
    reasoning: str,
    risk_assessment: str,
    confidence: float,
    context_snapshot: Dict[str, Any],
    hard_blocks_checked: Dict[str, bool],
    execution_status: str,
) -> Dict[str, Any]:
    return {
        "decision_id": decision_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "ticker": ticker,
        "decision": decision,
        "action_details": action_details,
        "reasoning": reasoning,
        "risk_assessment": risk_assessment,
        "confidence": round(confidence, 4),
        "context_snapshot": context_snapshot,
        "hard_blocks_checked": hard_blocks_checked,
        "execution_status": execution_status,
        "human_override": None,
    }


# ── Reactive handlers ─────────────────────────────────────────────────────────

async def handle_critical_alert(alert_id: str) -> Dict[str, Any]:
    """
    Immediately run a CRISIS decision cycle for a specific CRITICAL alert.
    Bypasses the 5-minute schedule. Called by the Risk Agent when severity=CRITICAL.
    """
    logger.warning("PM: reactive CRITICAL alert cycle triggered — alert_id=%s", alert_id)

    max_wait_seconds = 120
    waited = 0
    while not _acquire_pm_lock():
        if waited >= max_wait_seconds:
            logger.error("handle_critical_alert: Timeout waiting for PM lock.")
            return {"error": "PM lock timeout — cycle aborted"}
        logger.info("PM is currently running. Reactive CRITICAL handler waiting...")
        await asyncio.sleep(5)
        waited += 5

    try:
        try:
            resp = (
                _get_client()
                .table("risk_alerts")
                .select("*")
                .eq("id", alert_id)
                .limit(1)
                .execute()
            )
            alert = resp.data[0] if resp.data else {"id": alert_id, "severity": "CRITICAL"}
        except Exception as exc:
            logger.error("handle_critical_alert: alert fetch failed — %s", exc)
            alert = {"id": alert_id, "severity": "CRITICAL"}

        from backend.agents.pm_prompts.base_context import build_base_context
        from backend.agents.pm_prompts.crisis import build_crisis_prompt

        base_ctx = build_base_context(_get_client())
        system_prompt, user_message = build_crisis_prompt(alert, base_ctx)
        decision_data = _call_claude(system_prompt, user_message)

        if not decision_data:
            return {"error": "Claude call failed for reactive CRITICAL handler"}

        decision_id = _next_decision_id()
        action_details = decision_data.get("action_details", {})
        action_details["alert_id"] = alert_id

        record = _build_decision_record(
            decision_id=decision_id,
            category="CRISIS",
            ticker=alert.get("ticker"),
            decision=decision_data.get("decision", "MONITOR"),
            action_details=action_details,
            reasoning=decision_data.get("reasoning", ""),
            risk_assessment=decision_data.get("risk_assessment", ""),
            confidence=_clean_float(decision_data.get("confidence"), 0.5),
            context_snapshot=_snapshot(base_ctx),
            hard_blocks_checked={"daily_loss_ok": True},
            execution_status="SENT_TO_EXECUTION",
        )

        execution_status = _route_decision(decision_data, record)
        record["execution_status"] = execution_status
        _log_pm_decision(record)

        logger.info(
            "PM: reactive CRITICAL → %s (confidence=%.2f)",
            decision_data.get("decision"),
            decision_data.get("confidence", 0.5),
        )
        return record
    finally:
        _release_pm_lock()


async def handle_regime_change(new_regime: str) -> Dict[str, Any]:
    """
    Immediately run a REBALANCE review when the Macro Agent detects a regime shift.
    """
    logger.info("PM: reactive regime change cycle — new_regime=%s", new_regime)

    max_wait_seconds = 120
    waited = 0
    while not _acquire_pm_lock():
        if waited >= max_wait_seconds:
            logger.error("handle_regime_change: Timeout waiting for PM lock.")
            return {"error": "PM lock timeout — cycle aborted"}
        logger.info("PM is currently running. Reactive REGIME handler waiting...")
        await asyncio.sleep(5)
        waited += 5

    try:
        from backend.agents.pm_prompts.base_context import build_base_context
        from backend.agents.pm_prompts.rebalance import build_rebalance_prompt

        base_ctx = build_base_context(_get_client())
        base_ctx["macro_regime"] = new_regime  # use incoming regime for this review

        system_prompt, user_message = build_rebalance_prompt(base_ctx)
        decision_data = _call_claude(system_prompt, user_message)

        if not decision_data:
            return {"error": "Claude call failed for reactive regime change handler"}

        decision_id = _next_decision_id()
        record = _build_decision_record(
            decision_id=decision_id,
            category="REBALANCE",
            ticker=None,
            decision=decision_data.get("decision", "NO_ACTION"),
            action_details=decision_data.get("action_details", {}),
            reasoning=decision_data.get("reasoning", ""),
            risk_assessment=decision_data.get("risk_assessment", ""),
            confidence=_clean_float(decision_data.get("confidence"), 0.5),
            context_snapshot=_snapshot(base_ctx),
            hard_blocks_checked={},
            execution_status="SENT_TO_EXECUTION",
        )

        execution_status = _route_decision(decision_data, record)
        record["execution_status"] = execution_status
        _log_pm_decision(record)

        return record
    finally:
        _release_pm_lock()


# ── Legacy compatibility shims ────────────────────────────────────────────────
# These allow existing code that imports from orchestrator.py to keep working.

def _get_config() -> Dict[str, Any]:
    """Legacy shim — redirects to pm_config. Used by orchestrator API router."""
    cfg = _get_pm_config()
    return {
        "mode": "AUTONOMOUS" if cfg.get("mode") == "autonomous" else "SUPERVISED",
        "suspended_until": cfg.get("halted_until"),
        "id": cfg.get("id", 1),
    }


def _set_mode(mode: str) -> Dict[str, Any]:
    """
    Legacy shim — update mode in pm_config (and best-effort orchestrator_config).
    Returns dict with mode (SUPERVISED/AUTONOMOUS) + suspended_until for API usage.
    """
    mode_upper = str(mode).upper()
    if mode_upper not in ("SUPERVISED", "AUTONOMOUS"):
        raise ValueError(f"Invalid mode: {mode}")

    pm_mode = "autonomous" if mode_upper == "AUTONOMOUS" else "supervised"
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        _get_client().table("pm_config").update(
            {"mode": pm_mode, "updated_at": now_iso}
        ).eq("id", 1).execute()
    except Exception as exc:
        logger.warning("_set_mode: pm_config update failed — %s", exc)

    # Best-effort update for legacy table, if it exists.
    try:
        _get_client().table("orchestrator_config").update(
            {"mode": mode_upper, "updated_at": now_iso}
        ).eq("id", 1).execute()
    except Exception:
        pass

    cfg = _get_pm_config()
    notify_event("ORCHESTRATOR_MODE_CHANGE", {
        "mode": mode_upper,
        "changed_by": "user",
    })
    return {
        "mode": mode_upper,
        "suspended_until": cfg.get("halted_until"),
        "id": cfg.get("id", 1),
    }


def _set_suspended_until(suspended_until: Optional[datetime]) -> None:
    """
    Legacy shim — update halted_until in pm_config (and best-effort orchestrator_config).
    Accepts a datetime or None.
    """
    try:
        update = {
            "halted_until": suspended_until.isoformat() if suspended_until else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _get_client().table("pm_config").update(update).eq("id", 1).execute()
    except Exception as exc:
        logger.warning("_set_suspended_until: pm_config update failed — %s", exc)

    # Best-effort update for legacy table, if it exists.
    try:
        update = {
            "suspended_until": suspended_until.isoformat() if suspended_until else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _get_client().table("orchestrator_config").update(update).eq("id", 1).execute()
    except Exception:
        pass


def _log_event(
    event_type: str,
    agent: str,
    detail: str,
    mode_snapshot: Optional[str] = None,
) -> None:
    """Legacy shim — append an orchestrator_log row. Never raises."""
    try:
        payload = {
            "event_type": event_type,
            "agent": agent,
            "detail": detail,
            "run_date": date.today().isoformat(),
            "mode_snapshot": mode_snapshot,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _get_client().table("orchestrator_log").insert(payload).execute()
    except Exception as exc:
        logger.warning("_log_event: failed to persist event — %s", exc)


def _has_critical_alerts() -> bool:
    """Legacy shim — still used by portfolio API approve endpoint."""
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


async def run_orchestrator_cycle(portfolio_value: Optional[float] = None) -> dict:
    """
    Legacy shim — delegates to run_pm_cycle() for backward compatibility with
    any code that still calls run_orchestrator_cycle().
    """
    return run_pm_cycle(cycle_type="SCHEDULED", portfolio_value=portfolio_value)


# ── Scheduler ─────────────────────────────────────────────────────────────────

def create_orchestrator_scheduler():
    """
    Return a configured (not yet started) BackgroundScheduler.
    Same export name as v1 so main.py import is unchanged.

    Schedule:
        - 07:00 ET Mon–Fri: trigger Macro Agent
            - 16:00 ET Mon–Fri: trigger Screener
            - 17:00 ET Mon–Fri: trigger Research queue
      - Every 5 min: PM decision cycle
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BackgroundScheduler()

    # PM decision cycle — every 5 minutes
    scheduler.add_job(
        lambda: run_pm_cycle(cycle_type="SCHEDULED"),
        trigger=IntervalTrigger(seconds=300),
        id="pm_decision_cycle",
        name="AI PM Decision Cycle (5m)",
        replace_existing=True,
    )

    # Macro Agent — 07:00 ET Mon–Fri
    # misfire_grace_time=3600: only catch up if server restarted within 1 hour of 7AM.
    # Without this, APScheduler fires missed cron jobs immediately on any server restart.
    scheduler.add_job(
        _trigger_macro_agent,
        trigger=CronTrigger(
            hour=7, minute=0, day_of_week="mon-fri", timezone="America/New_York"
        ),
        id="pm_trigger_macro",
        name="PM → Macro Agent (7AM ET)",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Screener — 16:00 ET Mon–Fri
    scheduler.add_job(
        _trigger_screener,
        trigger=CronTrigger(
            hour=16, minute=0, day_of_week="mon-fri", timezone="America/New_York"
        ),
        id="pm_trigger_screener",
        name="PM → Screener (4PM ET)",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Research queue — 17:00 ET Mon–Fri
    scheduler.add_job(
        _trigger_research_queue,
        trigger=CronTrigger(
            hour=17, minute=0, day_of_week="mon-fri", timezone="America/New_York"
        ),
        id="pm_trigger_research",
        name="PM → Research Queue (5:00PM ET)",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Ticker events calendar refresh — 16:15 ET Mon–Fri
    # Runs between screener (16:00) and research queue (17:00) so upcoming events
    # are populated before the research scheduler fires.
    scheduler.add_job(
        _refresh_ticker_events_calendar,
        trigger=CronTrigger(
            hour=16, minute=15, day_of_week="mon-fri", timezone="America/New_York"
        ),
        id="pm_ticker_events_refresh",
        name="PM → Ticker Events Calendar Refresh (4:15PM ET)",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    return scheduler


# ── Event-driven research triggers ───────────────────────────────────────────

def _check_news_spike(ticker: str) -> bool:
    """Return True if today's news count is > 3x the 30-day daily average."""
    polygon_key = os.getenv("POLYGON_API_KEY")
    if not polygon_key:
        return False
    try:
        import requests as _req
        cutoff = (date.today() - timedelta(days=30)).isoformat()
        resp = _req.get(
            "https://api.polygon.io/v2/reference/news",
            params={
                "ticker": ticker,
                "published_utc.gte": cutoff,
                "limit": 1000,
                "apiKey": polygon_key,
            },
            timeout=10,
        )
        if not resp.ok:
            return False
        articles = resp.json().get("results", [])
        if not articles:
            return False

        today_str = date.today().isoformat()
        daily_counts: dict = {}
        for a in articles:
            d = str(a.get("published_utc", ""))[:10]
            if d:
                daily_counts[d] = daily_counts.get(d, 0) + 1

        today_count = daily_counts.get(today_str, 0)
        past_counts = [v for k, v in daily_counts.items() if k != today_str]
        if not past_counts:
            return False
        avg = sum(past_counts) / len(past_counts)
        spike = avg > 0 and today_count > 3 * avg
        if spike:
            logger.info("_check_news_spike(%s): today=%d avg=%.1f — SPIKE", ticker, today_count, avg)
        return spike
    except Exception as exc:
        logger.warning("_check_news_spike(%s): failed — %s", ticker, exc)
        return False


def _check_intraday_move(ticker: str) -> bool:
    """Return True if intraday price move exceeds 10%."""
    polygon_key = os.getenv("POLYGON_API_KEY")
    if not polygon_key:
        return False
    try:
        import requests as _req
        prev_resp = _req.get(
            f"https://api.polygon.io/v2/aggs/ticker/{ticker}/prev",
            params={"apiKey": polygon_key},
            timeout=10,
        )
        if not prev_resp.ok:
            return False
        results = prev_resp.json().get("results", [])
        if not results:
            return False
        prev_close = results[0].get("c")
        if not prev_close:
            return False

        trade_resp = _req.get(
            f"https://api.polygon.io/v2/last/trade/{ticker}",
            params={"apiKey": polygon_key},
            timeout=10,
        )
        if not trade_resp.ok:
            return False
        current = trade_resp.json().get("results", {}).get("p")
        if not current:
            return False

        move = abs(float(current) / float(prev_close) - 1)
        if move > 0.10:
            logger.info("_check_intraday_move(%s): move=%.2f%% — TRIGGER", ticker, move * 100)
            return True
        return False
    except Exception as exc:
        logger.warning("_check_intraday_move(%s): failed — %s", ticker, exc)
        return False


def _bulk_earnings_dropped_today(client, tickers: set[str]) -> set[str]:
    """Return the subset of tickers that have an earnings call document available today.

    Single query against ticker_events using an in_() filter — O(1) round trips
    regardless of universe size.
    """
    if not tickers:
        return set()
    try:
        today_str = date.today().isoformat()
        result = (
            client.table("ticker_events")
            .select("ticker")
            .in_("ticker", list(tickers))
            .eq("event_type", "earnings_call")
            .eq("event_date", today_str)
            .eq("document_available", True)
            .execute()
        )
        return {row["ticker"] for row in (result.data or [])}
    except Exception as exc:
        logger.warning("_bulk_earnings_dropped_today: failed — %s", exc)
        return set()


def _set_material_event(client, ticker: str, reason: str, is_held: bool) -> None:
    """Set material_event flag on the most recent watchlist entry for this ticker.
    If no entry exists for today, creates a new one to ensure the trigger is not lost.
    """
    today_str = date.today().isoformat()
    priority = 1 if is_held else 2
    try:
        result = client.table("watchlist").update(
            {
                "material_event": True,
                "material_event_reason": reason,
                "queued_for_research": True,
                "priority": priority,
            }
        ).eq("ticker", ticker).eq("run_date", today_str).execute()
        
        if result.data:
            logger.info(
                "_set_material_event(%s): reason='%s' priority=%d rows_updated=%d",
                ticker, reason, priority, len(result.data),
            )
        else:
            # No watchlist row for today — create a new one so the trigger is persisted
            client.table("watchlist").insert({
                "ticker": ticker,
                "run_date": today_str,
                "material_event": True,
                "material_event_reason": reason,
                "queued_for_research": True,
                "priority": priority,
                "composite_score": 0.0, # Placeholder
            }).execute()
            logger.info(
                "_set_material_event(%s): created new watchlist row for today (reason='%s')",
                ticker, reason
            )
    except Exception as exc:
        logger.warning("_set_material_event(%s): failed — %s", ticker, exc)


def _scan_event_triggers() -> None:
    """Check all watchlisted and held tickers for material events.

    Runs inside the 5-minute PM cycle during market hours only.
    Three triggers:
      - News volume > 3x 30-day daily average (Throttled: 1/hour)
      - Intraday price move > 10% (Throttled: 1/hour)
      - Earnings call document became available today (from ticker_events)

    When triggered, sets material_event=True and queues for research at P1 (held)
    or P2 (watchlist-only) priority.
    """
    if not _is_market_hours():
        return

    client = _get_client()

    # Collect candidate tickers: latest watchlist + all OPEN positions
    tickers_to_check: set[str] = set()
    try:
        today_str = date.today().isoformat()
        wl = (
            client.table("watchlist")
            .select("ticker")
            .eq("run_date", today_str)
            .execute()
        )
        for row in (wl.data or []):
            tickers_to_check.add(row["ticker"])
    except Exception as exc:
        logger.warning("_scan_event_triggers: watchlist fetch failed — %s", exc)

    open_tickers: set[str] = set()
    try:
        pos = (
            client.table("positions")
            .select("ticker")
            .eq("status", "OPEN")
            .execute()
        )
        for row in (pos.data or []):
            open_tickers.add(row["ticker"])
        tickers_to_check.update(open_tickers)
    except Exception as exc:
        logger.warning("_scan_event_triggers: positions fetch failed — %s", exc)

    if not tickers_to_check:
        return

    # Bulk-fetch which tickers have earnings available today — one query for all.
    earnings_dropped = _bulk_earnings_dropped_today(client, tickers_to_check)

    now = datetime.now(timezone.utc)
    for ticker in tickers_to_check:
        is_held = ticker in open_tickers
        trigger_reason: str | None = None

        if ticker in earnings_dropped:
            trigger_reason = "earnings_call_available"
        else:
            # Throttle news and move checks to once per hour per ticker
            last_scan = _last_event_scan.get(ticker)
            if last_scan and (now - last_scan).total_seconds() < 3600:
                continue
            
            _last_event_scan[ticker] = now
            if _check_news_spike(ticker):
                trigger_reason = "news_volume_spike"
            elif _check_intraday_move(ticker):
                trigger_reason = "intraday_move_gt10pct"

        if trigger_reason:
            _set_material_event(client, ticker, trigger_reason, is_held)


# ── Ticker events calendar refresh ───────────────────────────────────────────

def _refresh_ticker_events_calendar() -> None:
    """Update ticker_events for all watchlisted and held tickers.

    Runs at 16:15 ET (between screener at 16:00 and research at 17:00).
    Upserts upcoming earnings and filing events with document_fetched=False
    so the next research run knows to fetch them fresh.

    Data sources (already in stack, no new dependencies):
      - Polygon /vX/reference/financials  — upcoming earnings dates
      - SEC EDGAR /submissions/{CIK}      — filing history
    """
    polygon_key = os.getenv("POLYGON_API_KEY")
    client = _get_client()

    # Collect tickers: latest watchlist + OPEN positions
    tickers: set[str] = set()
    try:
        today_str = date.today().isoformat()
        wl = client.table("watchlist").select("ticker").eq("run_date", today_str).execute()
        for r in (wl.data or []):
            tickers.add(r["ticker"])
    except Exception as exc:
        logger.warning("_refresh_ticker_events_calendar: watchlist read failed — %s", exc)

    try:
        pos = client.table("positions").select("ticker").eq("status", "OPEN").execute()
        for r in (pos.data or []):
            tickers.add(r["ticker"])
    except Exception as exc:
        logger.warning("_refresh_ticker_events_calendar: positions read failed — %s", exc)

    if not tickers:
        logger.info("_refresh_ticker_events_calendar: no tickers to refresh")
        return

    logger.info("_refresh_ticker_events_calendar: refreshing %d tickers", len(tickers))
    rows_upserted = 0

    for ticker in tickers:
        # ── Polygon: upcoming earnings dates ─────────────────────────────────
        if polygon_key:
            try:
                import requests as _req
                resp = _req.get(
                    f"https://api.polygon.io/vX/reference/financials",
                    params={"ticker": ticker, "limit": 4, "apiKey": polygon_key},
                    timeout=10,
                )
                if resp.ok:
                    for fin in resp.json().get("results", []):
                        filing_date = fin.get("filing_date")
                        period = fin.get("fiscal_period")  # e.g. 'Q3' or 'FY'
                        period_year = str(fin.get("fiscal_year", ""))[:4]
                        if not filing_date or not period:
                            continue
                        fiscal_period = (
                            f"{period}_{period_year}" if period_year else period
                        )
                        try:
                            client.table("ticker_events").upsert(
                                {
                                    "ticker": ticker,
                                    "event_type": "earnings_call",
                                    "event_date": filing_date,
                                    "fiscal_period": fiscal_period,
                                    "document_available": False,
                                    "document_fetched": False,
                                    "source": "polygon",
                                },
                                on_conflict="ticker,event_type,fiscal_period",
                                ignore_duplicates=True,
                            ).execute()
                            rows_upserted += 1
                        except Exception:
                            pass
            except Exception as exc:
                logger.warning(
                    "_refresh_ticker_events_calendar: Polygon fetch failed for %s — %s",
                    ticker, exc,
                )

        # ── SEC EDGAR: filing history ─────────────────────────────────────────
        try:
            from backend.fetchers.sec_fetcher import _resolve_cik, _get_filings_metadata, _find_latest_filing
            cik = _resolve_cik(ticker)
            meta = _get_filings_metadata(cik)
            for form_type, event_type in [("10-K", "annual_filing"), ("10-Q", "quarterly_filing")]:
                filing = _find_latest_filing(meta, form_type)
                if not filing:
                    continue
                filing_date = filing.get("date")
                if not filing_date:
                    continue
                try:
                    d = date.fromisoformat(filing_date[:10])
                    fiscal_period = f"FY{d.year}" if form_type == "10-K" else f"Q{(d.month-1)//3+1}_{d.year}"
                except (ValueError, TypeError):
                    fiscal_period = filing_date[:7]

                try:
                    client.table("ticker_events").upsert(
                        {
                            "ticker": ticker,
                            "event_type": event_type,
                            "event_date": filing_date[:10],
                            "fiscal_period": fiscal_period,
                            "document_available": True,
                            "source": "sec_edgar",
                        },
                        on_conflict="ticker,event_type,fiscal_period",
                        ignore_duplicates=True,
                    ).execute()
                    rows_upserted += 1
                except Exception:
                    pass
        except Exception as exc:
            logger.warning(
                "_refresh_ticker_events_calendar: EDGAR fetch failed for %s — %s",
                ticker, exc,
            )

    logger.info(
        "_refresh_ticker_events_calendar: upserted %d event rows for %d tickers",
        rows_upserted, len(tickers),
    )


def _trigger_macro_agent() -> None:
    from backend.agents.macro_agent import run_macro_pipeline
    try:
        run_macro_pipeline()
    except Exception as exc:
        logger.error("PM scheduler: macro agent trigger failed — %s", exc)


def _trigger_screener() -> None:
    from backend.agents.screening_agent import run_screening
    try:
        run_screening()
    except Exception as exc:
        logger.error("PM scheduler: screener trigger failed — %s", exc)


def _trigger_research_queue() -> None:
    from backend.agents.research_scheduler import _poll_research_queue
    try:
        _poll_research_queue()
    except Exception as exc:
        logger.error("PM scheduler: research queue trigger failed — %s", exc)
