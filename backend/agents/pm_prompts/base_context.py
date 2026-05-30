"""
Base context builder for the AI Portfolio Manager Agent.

build_base_context(supabase_client) loads the current portfolio state from
Supabase and returns a structured dict that all five decision-category prompts
inject into their user message.

Computes gross and net exposure from OPEN positions so Claude has a
consistent picture of capital deployment regardless of which decision
category is being evaluated.
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Regime guidance: gross/net caps per regime (fractions, not %)
_REGIME_CAPS = {
    "Risk-On":       {"gross": 1.50, "net": 0.50},
    "Constructive":  {"gross": 1.35, "net": 0.35},
    "Risk-Off":      {"gross": 0.80, "net": 0.10},
    "Transitional":  {"gross": 1.20, "net": 0.20},
    "Stagflation":   {"gross": 1.00, "net": 0.00},
}


def build_base_context(supabase_client) -> Dict[str, Any]:
    """
    Load current portfolio state from Supabase and return a structured dict.

    Returns:
        {
          "positions": [...],            # all OPEN position rows
          "portfolio_gross_exposure": float,
          "portfolio_net_exposure": float,
          "cash_pct": float,
          "cash_usd": float | None,          # IBKR TotalCashValue (same as status bar)
          "position_count": int,
          "macro_regime": str,
          "macro_briefing_summary": dict,
          "active_alerts": [...],        # unresolved BREACH + CRITICAL alerts
          "recent_decisions": [...],     # last 10 pm_decisions rows
          "regime_caps": {"gross": float, "net": float},
        }
    """
    ctx: Dict[str, Any] = {
        "positions": [],
        "pending_positions": [],
        "pending_position_count": 0,
        "pending_exit_actions": [],
        "portfolio_gross_exposure": 0.0,
        "portfolio_net_exposure": 0.0,
        "portfolio_unrealized_pnl_pct": 0.0,
        "cash_pct": 1.0,
        "position_count": 0,
        "macro_regime": "Transitional",
        "macro_briefing_summary": {},
        "active_alerts": [],
        "recent_decisions": [],
        "regime_caps": _REGIME_CAPS["Transitional"],
    }

    # ── Open positions ────────────────────────────────────────────────────────
    try:
        resp = (
            supabase_client.table("positions")
            .select(
                "id,ticker,direction,share_count,entry_price,current_price,"
                "conviction_score,dollar_size,pct_of_portfolio,stop_loss_price,"
                "stop_tier1,stop_tier2,stop_tier3,next_earnings_date,"
                "exit_action,exit_trim_pct,sector,memo_id,opened_at,status"
            )
            .eq("status", "OPEN")
            .execute()
        )
        positions = resp.data or []
        ctx["positions"] = positions
        ctx["position_count"] = len(positions)

        # Compute live exposure using current market value (share_count * current_price).
        # dollar_size is set at fill time and only updated on partial exits — it drifts
        # as prices move, causing cash_pct to be wrong. current_price is refreshed every
        # risk monitor cycle so it's the correct denominator input.
        from backend.broker.ibkr import get_portfolio_value, get_account_summary, get_last_account_snapshot
        portfolio_value = get_portfolio_value()  # raises RuntimeError if IBKR + snapshot both unavailable

        # Wire in the exact IBKR cash balance (TotalCashValue) so the PM sees the
        # same figure as the dashboard status bar, rather than a derived estimate.
        # When IBKR is dead, fall back to the most recent account_snapshot row.
        try:
            summary = get_account_summary()
            cash_usd_val = summary.get("TotalCashValue") if summary else None
            if cash_usd_val is not None:
                ctx["cash_usd"] = float(cash_usd_val)
            else:
                snap = get_last_account_snapshot()
                if snap and snap.get("total_cash_value") is not None:
                    ctx["cash_usd"] = float(snap["total_cash_value"])
                    logger.warning(
                        "build_base_context: IBKR cash unavailable — using snapshot %.2f (captured_at=%s)",
                        ctx["cash_usd"], snap.get("captured_at"),
                    )
                else:
                    ctx["cash_usd"] = None
        except Exception as _cash_exc:
            logger.warning("build_base_context: could not fetch IBKR cash balance — %s", _cash_exc)
            ctx["cash_usd"] = None

        gross = 0.0
        net = 0.0
        for p in positions:
            shares = float(p.get("share_count") or 0.0)
            cur_price = float(p.get("current_price") or 0.0)
            # Fall back to dollar_size if current market value can't be computed
            market_value = (shares * cur_price) if shares > 0 and cur_price > 0 else float(p.get("dollar_size") or 0.0)
            w = market_value / portfolio_value if portfolio_value > 0 else 0.0
            direction = str(p.get("direction") or "LONG").upper()
            gross += abs(w)
            net += w if direction == "LONG" else -w

        # APPROVED / PENDING_APPROVAL positions are committed capital — include in
        # exposure so Claude sees what's already queued for next market open.
        try:
            pend_resp = (
                supabase_client.table("positions")
                .select("id,ticker,direction,dollar_size,sector,status,conviction_score")
                .in_("status", ["APPROVED", "PENDING_APPROVAL"])
                .execute()
            )
            pending = pend_resp.data or []
            ctx["pending_positions"] = pending
            ctx["pending_position_count"] = len(pending)
            # Derive pending exit actions from already-fetched OPEN positions (no extra query)
            ctx["pending_exit_actions"] = [
                {
                    "ticker": p.get("ticker"),
                    "direction": p.get("direction"),
                    "exit_action": p.get("exit_action"),
                    "exit_trim_pct": p.get("exit_trim_pct"),
                    "dollar_size": p.get("dollar_size"),
                    "sector": p.get("sector"),
                }
                for p in positions
                if p.get("exit_action") is not None
            ]
            for p in pending:
                w = abs(float(p.get("dollar_size") or 0.0)) / portfolio_value if portfolio_value > 0 else 0.0
                direction = str(p.get("direction") or "LONG").upper()
                gross += abs(w)
                net += w if direction == "LONG" else -w
        except Exception as _pend_exc:
            logger.warning("build_base_context: pending positions read failed — %s", _pend_exc)
            ctx.setdefault("pending_positions", [])
            ctx.setdefault("pending_position_count", 0)

        ctx["portfolio_gross_exposure"] = round(gross, 4)
        ctx["portfolio_net_exposure"] = round(net, 4)
        ctx["cash_pct"] = round(max(0.0, 1.0 - gross), 4)

        # Weighted unrealized P&L across all open positions (proxy for daily drawdown)
        portfolio_pnl = 0.0
        for p in positions:
            entry = float(p.get("entry_price") or 0)
            current = float(p.get("current_price") or 0)
            shares = float(p.get("share_count") or 0.0)
            market_value = (shares * current) if shares > 0 and current > 0 else float(p.get("dollar_size") or 0.0)
            w = market_value / portfolio_value if portfolio_value > 0 else 0.0
            if entry > 0 and current > 0 and w != 0:
                pos_dir = str(p.get("direction") or "LONG").upper()
                if pos_dir == "SHORT":
                    pos_pnl = (entry - current) / entry
                else:
                    pos_pnl = (current - entry) / entry
                portfolio_pnl += w * pos_pnl
        ctx["portfolio_unrealized_pnl_pct"] = round(portfolio_pnl, 4)

    except Exception as exc:
        logger.warning("build_base_context: positions read failed — %s", exc)

    # ── Macro regime ──────────────────────────────────────────────────────────
    try:
        resp = (
            supabase_client.table("macro_briefings")
            .select(
                "regime,regime_confidence,growth_score,inflation_score,"
                "fed_score,stress_score,portfolio_guidance,sector_tilts,qualitative_summary"
            )
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        if resp.data:
            row = resp.data[0]
            regime = row.get("regime", "Transitional")
            ctx["macro_regime"] = regime
            ctx["regime_caps"] = _REGIME_CAPS.get(regime, _REGIME_CAPS["Transitional"])
            ctx["macro_briefing_summary"] = {
                "regime": regime,
                "regime_confidence": row.get("regime_confidence"),
                "growth_score": row.get("growth_score"),
                "inflation_score": row.get("inflation_score"),
                "fed_score": row.get("fed_score"),
                "stress_score": row.get("stress_score"),
                "portfolio_guidance": row.get("portfolio_guidance"),
                "sector_tilts": row.get("sector_tilts"),
                "summary": (row.get("qualitative_summary") or "")[:500],
            }
    except Exception as exc:
        logger.warning("build_base_context: macro_briefings read failed — %s", exc)

    # ── Active risk alerts ────────────────────────────────────────────────────
    try:
        resp = (
            supabase_client.table("risk_alerts")
            .select("id,severity,ticker,trigger,created_at")
            .eq("resolved", False)
            .in_("severity", ["BREACH", "CRITICAL"])
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        ctx["active_alerts"] = resp.data or []
    except Exception as exc:
        logger.warning("build_base_context: risk_alerts read failed — %s", exc)

    # ── Recent PM decisions with outcome data ─────────────────────────────────
    try:
        resp = (
            supabase_client.table("pm_decisions")
            .select("decision_id,timestamp,category,ticker,decision,confidence,execution_status,outcome,confidence_breakdown")
            .order("timestamp", desc=True)
            .limit(15)
            .execute()
        )
        raw_decisions = resp.data or []
        ctx["recent_decisions"] = raw_decisions

        # Build a formatted outcome history for Claude: only decisions that have outcomes
        outcome_entries = []
        for d in raw_decisions:
            outcome = d.get("outcome")
            if not outcome:
                continue
            ret = outcome.get("return_pct")
            ticker = d.get("ticker", "portfolio")
            conviction = d.get("confidence", 0)
            decision = d.get("decision", "")
            status = outcome.get("position_status", "")
            symbol = "✓" if (ret or 0) > 0 else "✗"
            outcome_entries.append(
                f"  {symbol} {ticker} ({decision}, conviction={conviction:.2f}): "
                f"return={ret*100:+.1f}% [{status}]"
            )
        ctx["decision_outcome_history"] = outcome_entries

    except Exception as exc:
        logger.warning("build_base_context: pm_decisions read failed — %s", exc)
        ctx["decision_outcome_history"] = []

    # ── Calibration anchor from pm_calibration ────────────────────────────────
    try:
        cal_resp = (
            supabase_client.table("pm_calibration")
            .select("confidence_at_entry,return_pct,was_correct")
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        cal_rows = cal_resp.data or []
        ctx["calibration_anchor"] = _build_calibration_anchor(cal_rows)
    except Exception as exc:
        logger.warning("build_base_context: pm_calibration read failed — %s", exc)
        ctx["calibration_anchor"] = {}

    # ── NAV-gated capabilities ────────────────────────────────────────────────
    try:
        from backend.capabilities import get_capabilities
        caps = get_capabilities()
        ctx["capabilities"] = caps.model_dump()
        ctx["capability_tier"] = caps.capability_tier
        ctx["shorts_enabled"] = caps.shorts_enabled
        # Capability tier may impose a tighter net cap than the raw regime value
        # (e.g. Constructive regime = 35% but current NAV tier caps at 20%).
        # Apply the effective minimum so the PM sees the real constraint.
        if caps.max_net_exposure_pct is not None:
            ctx["regime_caps"] = {
                **ctx["regime_caps"],
                "net": min(ctx["regime_caps"]["net"], caps.max_net_exposure_pct),
            }
    except Exception as exc:
        logger.warning("build_base_context: capabilities read failed — %s", exc)
        ctx["capabilities"] = None
        ctx["capability_tier"] = "tier_0"
        ctx["shorts_enabled"] = False

    return ctx


def format_calibration_context(base_ctx: dict) -> str:
    """
    Return a formatted string block with past decision outcomes and calibration stats.
    Returns an empty string if no data is available.
    """
    parts = []

    outcome_history = base_ctx.get("decision_outcome_history", [])
    if outcome_history:
        parts.append("### Your Recent Decision Outcomes")
        parts.extend(outcome_history[:10])
        parts.append("")

    calibration = base_ctx.get("calibration_anchor", {})
    if calibration:
        total_n = sum(stats["n"] for stats in calibration.values())
        parts.append("### Historical Calibration (conviction bucket → avg outcome)")
        if total_n < 10:
            parts.append(
                f"  ⚠ Small sample (n={total_n} total). Treat these stats as directional, "
                f"not load-bearing. Empty buckets are shown so you can see where evidence is missing."
            )
        for bucket, stats in calibration.items():
            if stats["n"] == 0:
                parts.append(f"  {bucket}: n=0 (no evidence yet)")
            else:
                parts.append(
                    f"  {bucket}: n={stats['n']}, avg={stats['avg_return_pct']:+.1f}%, "
                    f"win_rate={stats['win_rate']:.0%}"
                )
        parts.append("")

    return "\n".join(parts)


def format_capabilities_context(base_ctx: dict) -> str:
    """
    Return a formatted string block describing current NAV-gated capabilities.
    Included in every PM decision user_message so Claude reasons within constraints.
    """
    caps = base_ctx.get("capabilities")
    if not caps:
        return "### Current Capabilities\nUnable to determine capabilities — defaulting to long-only (tier_0).\n\n"

    tier = caps.get("capability_tier", "tier_0")
    shorts = caps.get("shorts_enabled", False)
    reasons = caps.get("unlocked_reasons", [])
    nav_30d = caps.get("nav_trailing_30d", 0)
    alt_data = caps.get("alt_data_permitted", False)

    shorts_line = (
        f"Shorts ENABLED (universe min cap: ${caps.get('short_universe_min_cap', 0):.0f}M)"
        if shorts
        else "Shorts DISABLED (requires ≥$25K trailing NAV)"
    )

    lines = [
        "### Current Capabilities (NAV-Gated)",
        f"Tier: {tier} | Trailing 30d NAV: ${nav_30d:,.0f}",
        f"Shorts: {shorts_line}",
        f"Alt data: {'permitted (budget gate separate)' if alt_data else 'not permitted (requires ≥$250K NAV)'}",
        "Active capabilities:",
    ] + [f"  • {r}" for r in reasons] + [
        "",
        "If a decision requires a capability not listed above, choose an alternative "
        "within your current capability set or DEFER.",
        "",
    ]
    return "\n".join(lines)


def format_pending_actions_context(base_ctx: dict) -> str:
    """
    Return a formatted block showing all queued position changes not yet executed:
      - Queued entries: APPROVED / PENDING_APPROVAL positions awaiting fill
      - Armed exit actions: OPEN positions with TRIM / ADD / CLOSE already set

    Included in every PM decision user_message so Claude has a full picture of
    committed-but-unexecuted capital before making any decision.
    Returns an empty string if nothing is pending.
    """
    entries = base_ctx.get("pending_positions", [])
    exits = base_ctx.get("pending_exit_actions", [])

    if not entries and not exits:
        return ""

    parts = ["### Pending Position Changes (Queued, Not Yet Executed)"]

    if entries:
        parts.append(f"**Queued entries — {len(entries)} position(s) approved, awaiting fill at market open:**")
        for p in entries:
            size = float(p.get("dollar_size") or 0)
            parts.append(
                f"  • {p.get('ticker')} {p.get('direction', 'LONG')} ${size:,.0f}"
                f"  [{p.get('sector', '?')}]  status={p.get('status')}"
            )

    if exits:
        parts.append(f"**Armed exit actions — {len(exits)} OPEN position(s) with a pending order:**")
        for p in exits:
            action = p.get("exit_action")
            trim_pct = p.get("exit_trim_pct")
            size = float(p.get("dollar_size") or 0)
            if action == "CLOSE":
                parts.append(f"  • {p.get('ticker')}: CLOSE full position (${size:,.0f})")
            elif action == "TRIM" and trim_pct:
                parts.append(
                    f"  • {p.get('ticker')}: TRIM {float(trim_pct):.0%}"
                    f"  (≈${size * float(trim_pct):,.0f} released)"
                )
            elif action == "ADD" and trim_pct:
                parts.append(
                    f"  • {p.get('ticker')}: ADD {float(trim_pct):.0%}"
                    f"  (≈${size * float(trim_pct):,.0f} additional)"
                )
            else:
                parts.append(f"  • {p.get('ticker')}: {action}")

    parts.append(
        "Exposure figures above already include queued entries. "
        "Armed exits will *release* capacity once filled — do not double-count them.\n"
    )
    return "\n".join(parts) + "\n"


_CALIBRATION_MIN_ROWS = 3


def _build_calibration_anchor(rows: list) -> dict:
    """
    Aggregate pm_calibration rows into conviction bucket → outcome stats.

    Activates whenever ≥3 rows with a non-null return_pct exist. Buckets with
    zero rows are still returned (with n=0) so Claude can see the gap rather
    than silently inferring "no data". The prompt-formatting layer should
    surface small-sample warnings when total n < 10.
    """
    valid_rows = [r for r in rows if r.get("return_pct") is not None]
    if len(valid_rows) < _CALIBRATION_MIN_ROWS:
        return {}

    buckets: dict = {
        "high (0.8–1.0)": [],
        "med-high (0.6–0.8)": [],
        "medium (0.4–0.6)": [],
        "low (<0.4)": [],
    }

    for row in valid_rows:
        conf = row.get("confidence_at_entry") or 0
        ret = row["return_pct"]
        if conf >= 0.8:
            buckets["high (0.8–1.0)"].append(ret)
        elif conf >= 0.6:
            buckets["med-high (0.6–0.8)"].append(ret)
        elif conf >= 0.4:
            buckets["medium (0.4–0.6)"].append(ret)
        else:
            buckets["low (<0.4)"].append(ret)

    result = {}
    for label, returns in buckets.items():
        if not returns:
            result[label] = {"n": 0, "avg_return_pct": None, "win_rate": None}
            continue
        avg = sum(returns) / len(returns)
        win_rate = sum(1 for r in returns if r > 0) / len(returns)
        result[label] = {
            "n": len(returns),
            "avg_return_pct": round(avg * 100, 2),
            "win_rate": round(win_rate, 3),
        }
    return result
