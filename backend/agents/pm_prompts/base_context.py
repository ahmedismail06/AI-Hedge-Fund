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
                "conviction_score,pct_of_portfolio,stop_loss_price,"
                "sector,memo_id,opened_at,status"
            )
            .eq("status", "OPEN")
            .execute()
        )
        positions = resp.data or []
        ctx["positions"] = positions
        ctx["position_count"] = len(positions)

        # Compute exposure from pct_of_portfolio (already normalised to portfolio)
        gross = 0.0
        net = 0.0
        for p in positions:
            w = float(p.get("pct_of_portfolio") or 0.0)
            direction = p.get("direction", "LONG")
            gross += abs(w)
            net += w if direction == "LONG" else -w

        ctx["portfolio_gross_exposure"] = round(gross, 4)
        ctx["portfolio_net_exposure"] = round(net, 4)
        ctx["cash_pct"] = round(max(0.0, 1.0 - gross), 4)

        # Weighted unrealized P&L across all open positions (proxy for daily drawdown)
        # = sum(pct_of_portfolio * position_pnl_pct) — approximate when weights are stale
        portfolio_pnl = 0.0
        for p in positions:
            entry = float(p.get("entry_price") or 0)
            current = float(p.get("current_price") or 0)
            weight = float(p.get("pct_of_portfolio") or 0.0)
            if entry > 0 and current > 0 and weight != 0:
                pos_pnl = (current - entry) / entry
                portfolio_pnl += weight * pos_pnl
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

    # ── Recent PM decisions ───────────────────────────────────────────────────
    try:
        resp = (
            supabase_client.table("pm_decisions")
            .select("decision_id,timestamp,category,ticker,decision,confidence,execution_status")
            .order("timestamp", desc=True)
            .limit(10)
            .execute()
        )
        ctx["recent_decisions"] = resp.data or []
    except Exception as exc:
        logger.warning("build_base_context: pm_decisions read failed — %s", exc)

    return ctx
