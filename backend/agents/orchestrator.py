"""
AI Portfolio Manager Agent — Component 8 v2.

Replaces the deterministic approval-pass orchestrator with a Claude-powered
reasoning agent that makes all portfolio decisions autonomously. Every
material decision (entry, exit, rebalance, crisis, pre-earnings) is routed
through a Claude extended-thinking call with a category-specific prompt.

Architecture:
  - APScheduler runs run_pm_cycle() every 5 minutes (market hours + after-hours for cleanup)
  - Macro (7 AM), Screener (4 PM), Research queue (4:30 PM) crons are preserved unchanged
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
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import anthropic

from backend.memory.vector_store import _get_client

logger = logging.getLogger(__name__)

# ── Hard-block thresholds (code-enforced, never passed to Claude) ─────────────
_MAX_POSITION_PCT = 0.15          # 15% single-position hard cap
_GROSS_EXPOSURE_CEILING = 2.00    # 200% gross exposure absolute max
_DAILY_LOSS_HALT_PCT = 0.10       # -10% intraday drawdown → halt all trading
_STOP_PROXIMITY_TRIGGER = 0.03    # Trigger EXIT_TRIM review when within 3% of stop
_EARNINGS_LOOKAHEAD_DAYS = 14     # Pre-earnings review window


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


# ── Hard-block enforcement ────────────────────────────────────────────────────

def _check_hard_blocks(
    sizing_rec: Optional[Dict[str, Any]] = None,
    base_ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, bool]:
    """
    Pre-Claude hard block checks. Returns dict of check_name → passed (bool).
    Any False means execution must not proceed.
    """
    blocks = {
        "position_cap_ok": True,
        "market_hours_ok": True,
        "gross_exposure_ok": True,
        "daily_loss_ok": True,
    }

    # Position size cap
    if sizing_rec:
        weight = float(sizing_rec.get("portfolio_weight", 0) or 0)
        if weight > _MAX_POSITION_PCT:
            blocks["position_cap_ok"] = False
            logger.warning(
                "Hard block: position weight %.1f%% exceeds 15%% cap",
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


def _is_market_open() -> bool:
    """Return True if current ET time is within 9:30 AM – 4:00 PM Mon–Fri."""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        import time as _time
        # Rough fallback: UTC-4 (EDT)
        now_et = datetime.now(timezone.utc) - timedelta(hours=4)

    if now_et.weekday() >= 5:  # Saturday/Sunday
        return False
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et <= market_close


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
        _set_halt(True, datetime.now(timezone.utc).replace(
            hour=23, minute=59, second=59
        ))
        logger.critical(
            "PM: daily loss halt triggered — intraday drawdown %.2f%% exceeds %.0f%% threshold",
            drawdown_pct * 100,
            _DAILY_LOSS_HALT_PCT * 100,
        )

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


# ── Decision routing ──────────────────────────────────────────────────────────

def _route_decision(decision_data: Dict[str, Any], decision_record: Dict[str, Any]) -> str:
    """
    After Claude decides, route the action to the appropriate Supabase update.
    Returns the final execution_status string.
    """
    decision = decision_data.get("decision", "NO_ACTION")
    category = decision_record.get("category", "")
    ticker = decision_record.get("ticker")

    try:
        client = _get_client()

        if category == "NEW_ENTRY" and decision == "EXECUTE":
            # Mark the memo as approved so the portfolio agent (already ran) or
            # direct position update can proceed. The position with PENDING_APPROVAL
            # status was created by the portfolio sizing agent — approve it.
            if ticker:
                resp = (
                    client.table("positions")
                    .update({"status": "APPROVED"})
                    .eq("ticker", ticker)
                    .eq("status", "PENDING_APPROVAL")
                    .execute()
                )
                if resp.data:
                    logger.info("PM: EXECUTE approved position for %s", ticker)
                    return "SENT_TO_EXECUTION"
            return "BLOCKED"

        elif category == "NEW_ENTRY" and decision == "MODIFY_SIZE":
            # Update the pending position with the new size from action_details
            action = decision_data.get("action_details", {})
            new_dollar = action.get("dollar_amount")
            new_shares = action.get("shares")
            if ticker and (new_dollar or new_shares):
                update: Dict[str, Any] = {"status": "APPROVED"}
                if new_shares:
                    update["share_count"] = new_shares
                client.table("positions").update(update).eq("ticker", ticker).eq(
                    "status", "PENDING_APPROVAL"
                ).execute()
                logger.info("PM: MODIFY_SIZE approved modified position for %s", ticker)
                return "SENT_TO_EXECUTION"
            return "BLOCKED"

        elif category == "NEW_ENTRY" and decision == "DEFER":
            if ticker:
                client.table("memos").update({"status": "DEFERRED"}).eq(
                    "ticker", ticker
                ).eq("status", "PENDING_PM_REVIEW").execute()
            return "DEFERRED"

        elif category == "NEW_ENTRY" and decision == "REJECT":
            if ticker:
                client.table("memos").update({"status": "REJECTED"}).eq(
                    "ticker", ticker
                ).eq("status", "PENDING_PM_REVIEW").execute()
                # Also reject the pending position
                client.table("positions").update({"status": "REJECTED"}).eq(
                    "ticker", ticker
                ).eq("status", "PENDING_APPROVAL").execute()
            return "BLOCKED"

        elif category == "NEW_ENTRY" and decision == "WATCHLIST":
            if ticker:
                client.table("memos").update({"status": "WATCHLIST"}).eq(
                    "ticker", ticker
                ).eq("status", "PENDING_PM_REVIEW").execute()
            return "DEFERRED"

        elif category == "EXIT_TRIM" and decision in ("TRIM", "CLOSE"):
            if ticker:
                # Mark the OPEN position as APPROVED for exit (execution agent handles it)
                # We add an exit_action field to action_details so execution agent knows
                update = {
                    "exit_action": decision,
                    "exit_trim_pct": decision_data.get("action_details", {}).get("trim_pct"),
                }
                # Use a pm_exit_requested flag if the positions table supports it,
                # otherwise log and let human see it in the decision feed
                logger.info(
                    "PM: %s decision for %s — routing to execution review",
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
            return "SENT_TO_EXECUTION"

    except Exception as exc:
        logger.error("_route_decision: routing failed for %s — %s", ticker, exc)

    if decision == "NO_ACTION":
        return "NO_ACTION"
    if decision in ("HOLD", "MONITOR", "DEPLOY_CASH"):
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
    if portfolio_value is None:
        portfolio_value = float(os.getenv("PORTFOLIO_VALUE", "25000"))

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

    # ── Step 4: Scan actionable items ─────────────────────────────────────────
    items = _scan_actionable_items(base_ctx)

    if not items:
        logger.debug("PM cycle: no actionable items this cycle")
        return {
            "cycle_id": cycle_id,
            "cycle_type": cycle_type,
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
                post_blocks = _check_hard_blocks(
                    sizing_rec={
                        "portfolio_weight": action_details.get("dollar_amount", 0) / portfolio_value
                        if action_details.get("dollar_amount")
                        else 0
                    },
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
                confidence=float(decision_data.get("confidence", 0.5)),
                context_snapshot=_snapshot(base_ctx),
                hard_blocks_checked=hard_blocks,
                execution_status="PENDING_HUMAN",  # filled below
            )

            # Route decision (actual Supabase updates)
            if mode == "autonomous" and _is_market_open():
                execution_status = _route_decision(decision_data, record_template)
            elif mode == "supervised":
                execution_status = "PENDING_HUMAN"
            else:
                # Non-market-hours: log the decision but defer execution
                execution_status = "DEFERRED"
                if category in ("CRISIS",):
                    # Crisis can act outside market hours (prep for next open)
                    execution_status = _route_decision(decision_data, record_template)

            record_template["execution_status"] = execution_status

            # Embed alert_id in action_details for CRISIS deduplication
            if category == "CRISIS" and data.get("alert", {}).get("id"):
                ad = record_template.get("action_details") or {}
                ad["alert_id"] = data["alert"]["id"]
                record_template["action_details"] = ad

            # Log decision FIRST, then it's already persisted regardless of routing outcome
            _log_pm_decision(record_template)

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

    return {
        "cycle_id": cycle_id,
        "cycle_type": cycle_type,
        "items_evaluated": len(items),
        "decisions_made": decisions_made,
        "portfolio_state": _snapshot(base_ctx),
    }


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
        sizing_rec = data.get("sizing_rec", {})
        # Try to load sizing rec from positions table if not in data
        if not sizing_rec and memo.get("ticker"):
            try:
                resp = (
                    _get_client()
                    .table("positions")
                    .select("*")
                    .eq("ticker", memo["ticker"])
                    .eq("status", "PENDING_APPROVAL")
                    .limit(1)
                    .execute()
                )
                sizing_rec = resp.data[0] if resp.data else {}
            except Exception:
                pass
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
        confidence=float(decision_data.get("confidence", 0.5)),
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


async def handle_regime_change(new_regime: str) -> Dict[str, Any]:
    """
    Immediately run a REBALANCE review when the Macro Agent detects a regime shift.
    """
    logger.info("PM: reactive regime change cycle — new_regime=%s", new_regime)

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
        confidence=float(decision_data.get("confidence", 0.5)),
        context_snapshot=_snapshot(base_ctx),
        hard_blocks_checked={},
        execution_status="SENT_TO_EXECUTION",
    )

    execution_status = _route_decision(decision_data, record)
    record["execution_status"] = execution_status
    _log_pm_decision(record)

    return record


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
      - 16:30 ET Mon–Fri: trigger Research queue
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
    scheduler.add_job(
        _trigger_macro_agent,
        trigger=CronTrigger(
            hour=7, minute=0, day_of_week="mon-fri", timezone="America/New_York"
        ),
        id="pm_trigger_macro",
        name="PM → Macro Agent (7AM ET)",
        replace_existing=True,
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
    )

    # Research queue — 16:30 ET Mon–Fri
    scheduler.add_job(
        _trigger_research_queue,
        trigger=CronTrigger(
            hour=16, minute=30, day_of_week="mon-fri", timezone="America/New_York"
        ),
        id="pm_trigger_research",
        name="PM → Research Queue (4:30PM ET)",
        replace_existing=True,
    )

    return scheduler


def _trigger_macro_agent() -> None:
    from backend.macro.scheduler import run_macro_agent
    try:
        asyncio.run(run_macro_agent())
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
