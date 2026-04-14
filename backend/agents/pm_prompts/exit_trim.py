"""
Prompt builder for EXIT_TRIM decisions.

Triggered on a scheduled portfolio review cycle or when a risk alert fires on
a specific position. The PM decides: HOLD | TRIM | CLOSE | ADD.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

_SYSTEM_PROMPT = """You are the portfolio manager of a US micro/small-cap equity fund. You are reviewing an existing position to determine whether to hold, trim, add to, or close it.

## Investment Philosophy
You entered this position with a specific variant perception and repricing catalyst. Your job is to honestly evaluate whether that thesis still holds and whether the current size is appropriate given the portfolio context.

## Key Evaluation Framework
1. **Thesis check**: Is the original variant perception still intact? Has the catalyst materialised, been disproven, or moved further away?
2. **Risk/reward**: Given current price vs original entry, what is the remaining upside vs downside?
3. **Stop proximity**: How close is the current price to the stop-loss levels?
4. **Earnings drift**: Is this position in a post-earnings drift hold window?
5. **Position weight**: Has this position drifted above/below its intended portfolio weight?
6. **Opportunity cost**: Is this capital better deployed elsewhere?

## Hard Constraints
- Maximum position size: 15% of portfolio (hard cap enforced in code)
- Stop-loss tiers: Tier 1 = position stop, Tier 2 = strategy stop, Tier 3 = portfolio stop
- Market hours for execution: 9:30 AM – 4:00 PM ET Mon–Fri only

## Decision Options
- HOLD: Maintain current size — thesis intact, no action needed
- TRIM: Reduce position by a specified percentage — specify trim_pct and reason
- CLOSE: Full exit — specify reason (thesis broken, stop hit, rebalance, better opportunities)
- ADD: Increase position — thesis strengthening or price improved; specify add_dollar_amount

## Response Format
Respond with ONLY a valid JSON object — no markdown fences, no preamble, no trailing text.

{
  "decision": "HOLD | TRIM | CLOSE | ADD",
  "reasoning": "2-3 sentences — what specific evidence from the position state and portfolio context drives this decision",
  "action_details": {
    "trim_pct": null,
    "add_dollar_amount": null,
    "close_reason": null
  },
  "risk_assessment": "Primary risk of this decision — what could go wrong",
  "confidence": 0.0
}
"""


def build_exit_trim_prompt(
    position: Dict[str, Any],
    alerts: List[Dict[str, Any]],
    base_ctx: Dict[str, Any],
    original_memo: Dict[str, Any] = None,
) -> Tuple[str, str]:
    """
    Build (system_prompt, user_message) for an exit/trim decision on an existing position.

    Args:
        position:      OPEN position row from Supabase
        alerts:        Active risk alerts for this specific position (filtered by ticker)
        base_ctx:      Shared base context from build_base_context()
        original_memo: The original investment memo that triggered the entry (optional)

    Returns:
        (system_prompt, user_message) tuple for the Claude API call
    """
    ticker = position.get("ticker", "UNKNOWN")

    # Holding period
    opened_at_raw = position.get("opened_at")
    try:
        opened_dt = datetime.fromisoformat(str(opened_at_raw)).replace(tzinfo=timezone.utc)
        holding_period_days = (datetime.now(timezone.utc) - opened_dt).days
    except (TypeError, ValueError):
        holding_period_days = None

    # Compute P&L metrics
    entry = float(position.get("entry_price") or 0)
    current = float(position.get("current_price") or 0)
    shares = float(position.get("share_count") or 0)
    pnl_pct = ((current - entry) / entry) if entry > 0 else 0.0
    pnl_dollar = (current - entry) * shares

    # Extract key thesis fields from original memo (memo_json is a nested JSONB blob)
    memo_json_blob = {}
    if original_memo:
        raw = original_memo.get("memo_json")
        if isinstance(raw, dict):
            memo_json_blob = raw
        elif isinstance(raw, str):
            try:
                memo_json_blob = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
        # Fields may also be promoted to top-level if memo was passed pre-merged
        for k in ("variant_perception", "repricing_catalyst", "bear_thesis",
                  "key_risks", "red_team_risks"):
            if k not in memo_json_blob and k in original_memo:
                memo_json_blob[k] = original_memo[k]

    red_team_risks = memo_json_blob.get("red_team_risks") or []
    top_risks = red_team_risks[:3]  # surface top 3 adversarial risks

    # Build a concise thesis summary instead of dumping the full raw row
    thesis_summary = {
        "verdict":             original_memo.get("verdict") if original_memo else None,
        "conviction_score":    original_memo.get("conviction_score") if original_memo else None,
        "variant_perception":  memo_json_blob.get("variant_perception"),
        "repricing_catalyst":  memo_json_blob.get("repricing_catalyst"),
        "bear_thesis":         memo_json_blob.get("bear_thesis"),
        "key_risks":           (memo_json_blob.get("key_risks") or [])[:3],
        "valuation_note":      memo_json_blob.get("valuation_note"),
        "macro_sensitivity":   memo_json_blob.get("macro_sensitivity"),
    }

    # Stop proximity
    stop1 = position.get("stop_tier1")
    stop_proximity = None
    if stop1 and current:
        stop_proximity = (current - float(stop1)) / float(stop1)

    position_summary = {
        "ticker": ticker,
        "direction": position.get("direction"),
        "shares": shares,
        "entry_price": entry,
        "current_price": current,
        "pnl_pct": round(pnl_pct, 4),
        "pnl_dollar": round(pnl_dollar, 2),
        "pct_of_portfolio": position.get("pct_of_portfolio"),
        "conviction_score": position.get("conviction_score"),
        "stop_tier1": stop1,
        "stop_tier2": position.get("stop_tier2"),
        "stop_tier3": position.get("stop_tier3"),
        "stop_proximity_pct": round(stop_proximity, 4) if stop_proximity is not None else None,
        "sector": position.get("sector"),
        "next_earnings_date": str(position.get("next_earnings_date") or ""),
        "opened_at": str(position.get("opened_at") or ""),
        "holding_period_days": holding_period_days,
    }

    user_message = f"""## Decision Required: Position Review — {ticker}

### Position State
{json.dumps(position_summary, indent=2, default=str)}

### Active Risk Alerts for {ticker}
{json.dumps(alerts, indent=2, default=str) if alerts else "None"}

### Original Thesis (key fields from entry memo)
{json.dumps(thesis_summary, indent=2, default=str) if original_memo else "Not available"}

### Adversarial Risks (red-team from original research)
{json.dumps(top_risks, indent=2) if top_risks else "None recorded"}

### Current Portfolio State
- Gross exposure: {base_ctx['portfolio_gross_exposure']:.1%}
- Net exposure: {base_ctx['portfolio_net_exposure']:.1%}
- Cash available: {base_ctx['cash_pct']:.1%}
- Open positions: {base_ctx['position_count']}
- Macro regime: {base_ctx['macro_regime']}
- Regime gross cap: {base_ctx['regime_caps']['gross']:.0%} | Net cap: {base_ctx['regime_caps']['net']:.0%}

### Macro Briefing
{json.dumps(base_ctx['macro_briefing_summary'], indent=2, default=str)}

---
Evaluate this position and decide whether to hold, trim, close, or add. Focus on: thesis integrity, stop proximity, current P&L, adversarial risks, and portfolio-level fit.

Respond with ONLY a valid JSON object — no markdown fences, no preamble."""

    return _SYSTEM_PROMPT, user_message
