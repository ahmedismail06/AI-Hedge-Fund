"""
Prompt builder for CRISIS decisions.

Triggered immediately when a CRITICAL risk alert fires or when a drawdown
spike exceeds monitoring thresholds. Bypasses the normal 5-minute schedule.

The PM decides: REDUCE_EXPOSURE | HALT_NEW_ENTRIES | LIQUIDATE_TO_TARGET | HEDGE | MONITOR.
"""

import json
from typing import Any, Dict, Tuple

_SYSTEM_PROMPT = """You are the portfolio manager of a US micro/small-cap equity fund. A crisis event has been detected — a CRITICAL risk alert, drawdown spike, or regime shift. You must assess the situation and choose a proportional response.

## Crisis Response Philosophy
Your response must be proportional to the actual severity of the event. Overreacting to a false positive is as costly as underreacting to a real crisis — whipsawing between fully active and fully halted destroys alpha. Before escalating, ask:
1. Is this a real signal or a data artifact?
2. Is this portfolio-wide or isolated to one position?
3. Is the macro context supportive of maintaining exposure or reducing it?
4. What does the size of the move tell you about the probability of further deterioration?

## Hard Constraints
- Daily loss limit of -10% intraday portfolio drawdown → all trading halted (enforced in code before you see this)
- Market hours for execution: 9:30 AM – 4:00 PM ET Mon–Fri only
- 200% gross ceiling is always active

## Decision Options
- REDUCE_EXPOSURE: Cut specific positions by specified amounts — list each position and reduction %
- HALT_NEW_ENTRIES: Stop taking new positions until conditions improve — specify trigger for resumption
- LIQUIDATE_TO_TARGET: Reduce overall gross exposure to a specific target % — specify which positions to close first (weakest conviction / highest correlation to the trigger)
- HEDGE: Add a hedge position if available and appropriate — specify instrument and size
- MONITOR: Situation is concerning but not yet actionable — increase monitoring, no immediate action

## Response Format
Respond with ONLY a valid JSON object — no markdown fences, no preamble, no trailing text.

{
  "decision": "REDUCE_EXPOSURE | HALT_NEW_ENTRIES | LIQUIDATE_TO_TARGET | HEDGE | MONITOR",
  "reasoning": "2-3 sentences — what is the nature of the crisis, why is this response proportional, what would escalate or de-escalate the situation",
  "action_details": {
    "positions_to_reduce": [],
    "target_gross_exposure": null,
    "halt_resumption_trigger": null,
    "hedge_details": null,
    "monitor_escalation_threshold": null
  },
  "risk_assessment": "What could go wrong with this crisis response — both over-reacting and under-reacting risks",
  "confidence": 0.0
}

For REDUCE_EXPOSURE: positions_to_reduce is [{"ticker": str, "reduce_pct": float, "reason": str}]
For LIQUIDATE_TO_TARGET: specify target_gross_exposure (fraction) and positions_to_reduce in priority order
For HALT_NEW_ENTRIES: specify halt_resumption_trigger (e.g., "CRITICAL alert resolved", "VIX < 25")
For MONITOR: specify monitor_escalation_threshold (what would trigger a harder response)
"""


def build_crisis_prompt(
    alert: Dict[str, Any],
    base_ctx: Dict[str, Any],
) -> Tuple[str, str]:
    """
    Build (system_prompt, user_message) for a crisis response decision.

    Args:
        alert:    The triggering risk alert row from Supabase
        base_ctx: Shared base context from build_base_context()

    Returns:
        (system_prompt, user_message) tuple for the Claude API call
    """
    # Build affected position summary (most exposed to this alert)
    alert_ticker = alert.get("ticker")
    affected_positions = []
    for p in base_ctx["positions"]:
        is_affected = p.get("ticker") == alert_ticker if alert_ticker else False
        affected_positions.append({
            "ticker": p.get("ticker"),
            "direction": p.get("direction"),
            "portfolio_weight": p.get("portfolio_weight"),
            "pnl_pct": round(
                ((float(p.get("current_price") or 0) - float(p.get("entry_price") or 1))
                 / float(p.get("entry_price") or 1)), 4
            ) if p.get("entry_price") else None,
            "stop_tier1": p.get("stop_tier1"),
            "conviction_score": p.get("conviction_score"),
            "is_directly_affected": is_affected,
        })

    user_message = f"""## CRISIS RESPONSE REQUIRED — Immediate Decision Needed

### Triggering Alert
{json.dumps(alert, indent=2, default=str)}

### All Active Risk Alerts
{json.dumps(base_ctx['active_alerts'], indent=2, default=str)}

### Portfolio Exposure
- Gross exposure: {base_ctx['portfolio_gross_exposure']:.1%}
- Net exposure: {base_ctx['portfolio_net_exposure']:.1%}
- Cash: {base_ctx['cash_pct']:.1%}
- Open positions: {base_ctx['position_count']}
- Portfolio unrealized P&L (weighted, vs entry): {base_ctx['portfolio_unrealized_pnl_pct']:+.2%}
  (daily drawdown halt triggers at −10%; autonomous mode suspends at −5%)

### Position Details
{json.dumps(affected_positions, indent=2, default=str)}

### Macro Regime Context
{json.dumps(base_ctx['macro_briefing_summary'], indent=2, default=str)}
Regime: {base_ctx['macro_regime']} | Gross cap: {base_ctx['regime_caps']['gross']:.0%}

---
Assess the severity of this crisis event and choose a proportional response. Consider: is this isolated or systemic, is it a data artifact or a real signal, and what is the cost of overreacting vs underreacting?

Respond with ONLY a valid JSON object — no markdown fences, no preamble."""

    return _SYSTEM_PROMPT, user_message
