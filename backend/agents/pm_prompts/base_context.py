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

        # Compute live exposure using dollar_size against current portfolio value
        # (avoids stale pct_of_portfolio which was baked in at sizing time)
        from backend.broker.ibkr import get_portfolio_value
        portfolio_value = get_portfolio_value()  # raises RuntimeError if IBKR + snapshot both unavailable

        gross = 0.0
        net = 0.0
        for p in positions:
            dollar_size = float(p.get("dollar_size") or 0.0)
            w = dollar_size / portfolio_value if portfolio_value > 0 else 0.0
            direction = p.get("direction", "LONG")
            gross += abs(w)
            net += w if direction == "LONG" else -w

        ctx["portfolio_gross_exposure"] = round(gross, 4)
        ctx["portfolio_net_exposure"] = round(net, 4)
        ctx["cash_pct"] = round(max(0.0, 1.0 - gross), 4)

        # Weighted unrealized P&L across all open positions (proxy for daily drawdown)
        portfolio_pnl = 0.0
        for p in positions:
            entry = float(p.get("entry_price") or 0)
            current = float(p.get("current_price") or 0)
            dollar_size = float(p.get("dollar_size") or 0.0)
            w = dollar_size / portfolio_value if portfolio_value > 0 else 0.0
            if entry > 0 and current > 0 and w != 0:
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
        parts.append("### Historical Calibration (conviction bucket → avg outcome)")
        for bucket, stats in calibration.items():
            parts.append(
                f"  {bucket}: n={stats['n']}, avg={stats['avg_return_pct']:+.1f}%, "
                f"win_rate={stats['win_rate']:.0%}"
            )
        parts.append("")

    return "\n".join(parts)


def _build_calibration_anchor(rows: list) -> dict:
    """
    Aggregate pm_calibration rows into conviction bucket → outcome stats.
    Returns empty dict if insufficient data (<5 rows).
    """
    if len(rows) < 5:
        return {}

    buckets: dict = {
        "high (0.8–1.0)": [],
        "med-high (0.6–0.8)": [],
        "medium (0.4–0.6)": [],
        "low (<0.4)": [],
    }

    for row in rows:
        conf = row.get("confidence_at_entry") or 0
        ret = row.get("return_pct")
        if ret is None:
            continue
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
            continue
        avg = sum(returns) / len(returns)
        win_rate = sum(1 for r in returns if r > 0) / len(returns)
        result[label] = {
            "n": len(returns),
            "avg_return_pct": round(avg * 100, 2),
            "win_rate": round(win_rate, 3),
        }
    return result
