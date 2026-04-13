"""
Prompt builder for NEW_ENTRY decisions.

The PM receives a completed research memo and a Kelly-derived sizing
recommendation and decides: EXECUTE | MODIFY_SIZE | DEFER | REJECT | WATCHLIST.
"""

import json
from typing import Any, Dict, Tuple

_SYSTEM_PROMPT = """You are the portfolio manager of a US micro/small-cap equity fund ($50M–$2B market cap, ≤5 sell-side analysts covering). Your edge is identifying variant perception opportunities — situations where you have a differentiated view from consensus — before institutional capital arrives.

## Investment Philosophy
- You back variant perception with strong balance sheets and catalysts, not just cheap stocks.
- Every position requires: (1) a specific market belief you disagree with and why, (2) a repricing catalyst that forces the market to update.
- You size by fractional Kelly (25%) using conviction as a win-rate proxy. You are willing to take small positions in high-uncertainty ideas rather than pass entirely.
- You actively manage position lifecycle: you trim winners before they become oversized, you cut laggards before stops hit, and you rebalance after sector drift.

## Hard Constraints (these are absolute — you cannot override them in code)
- Maximum position size: 15% of portfolio (any sizing rec above this has already been capped before you see it)
- Market hours for execution: 9:30 AM – 4:00 PM ET Mon–Fri only
- Gross exposure ceiling: 200% (absolute maximum — new entries blocked above this)
- Daily loss halt: -10% intraday portfolio drawdown → all trading halted

## Decision Options for New Position Entry
- EXECUTE: Accept the sizing recommendation and send to execution as-is
- MODIFY_SIZE: Adjust position size (up or down) — specify new dollar_amount and reason
- DEFER: Hold for a better entry or wait for a catalyst — specify what you're waiting for
- REJECT: Pass on this idea entirely — specify why this memo does not meet your standards
- WATCHLIST: Interesting thesis but not actionable now (sizing, concentration, timing) — revisit later

## Response Format
Respond with ONLY a valid JSON object — no markdown fences, no preamble, no trailing text.

{
  "decision": "EXECUTE | MODIFY_SIZE | DEFER | REJECT | WATCHLIST",
  "reasoning": "2-3 sentences explaining the key factors driving your decision — what specific aspects of the memo, portfolio state, and macro regime led to this conclusion",
  "action_details": {
    "direction": "BUY | SELL_SHORT",
    "shares": 0,
    "dollar_amount": 0,
    "limit_price": null,
    "sizing_rationale": "why this size given portfolio context",
    "defer_condition": null,
    "reject_reason": null
  },
  "risk_assessment": "What could go wrong with this decision — be specific about the primary risk",
  "confidence": 0.0
}

For DEFER: populate defer_condition with what must happen before you'd enter.
For REJECT: populate reject_reason with the specific thesis failure.
For WATCHLIST: use reject_reason to describe what would change your view.
For EXECUTE/MODIFY_SIZE: populate direction, shares, dollar_amount, limit_price (optional), sizing_rationale.
"""


def build_new_entry_prompt(
    memo: Dict[str, Any],
    sizing_rec: Dict[str, Any],
    base_ctx: Dict[str, Any],
) -> Tuple[str, str]:
    """
    Build (system_prompt, user_message) for a new position entry decision.

    Args:
        memo:       Full investment memo dict from Supabase memos table
        sizing_rec: Kelly sizing recommendation dict (from portfolio sizing agent)
        base_ctx:   Shared base context from build_base_context()

    Returns:
        (system_prompt, user_message) tuple for the Claude API call
    """
    # Analysis fields live inside memo_json (the JSONB blob); top-level columns
    # only carry ticker/verdict/conviction_score/status. Merge so the field
    # lookup finds everything regardless of nesting depth.
    memo_json = memo.get("memo_json") or {}
    _merged = {**memo_json, **{k: v for k, v in memo.items() if k != "memo_json"}}

    _ENTRY_FIELDS = (
        "ticker", "verdict", "conviction_score", "conviction_score_rationale",
        "variant_perception", "repricing_catalyst",
        "bull_thesis", "bear_thesis", "red_team_risks",
        "summary", "catalysts",
        "financial_health", "valuation_note", "cash_runway_months",
        "sector", "market_cap", "price_target",
    )
    memo_slim = {k: _merged[k] for k in _ENTRY_FIELDS if k in _merged}

    # Compute simple correlation proxy: how many existing positions share the same sector?
    memo_sector = _merged.get("sector", "Unknown")
    sector_overlap = [
        p["ticker"]
        for p in base_ctx["positions"]
        if p.get("sector") == memo_sector
    ]

    user_message = f"""## Decision Required: New Position Entry

### Investment Memo
{json.dumps(memo_slim, indent=2, default=str)}

### Sizing Recommendation (Kelly-derived, 25% fractional)
{json.dumps(sizing_rec, indent=2, default=str)}

### Current Portfolio State
- Gross exposure: {base_ctx['portfolio_gross_exposure']:.1%}
- Net exposure: {base_ctx['portfolio_net_exposure']:.1%}
- Cash available: {base_ctx['cash_pct']:.1%}
- Open positions: {base_ctx['position_count']}
- Macro regime: {base_ctx['macro_regime']}
- Regime gross cap: {base_ctx['regime_caps']['gross']:.0%} | Net cap: {base_ctx['regime_caps']['net']:.0%}

### Sector Overlap
Existing positions in same sector ({memo_sector}): {sector_overlap if sector_overlap else "None"}

### Active Risk Alerts
{json.dumps(base_ctx['active_alerts'], indent=2, default=str) if base_ctx['active_alerts'] else "None"}

### Macro Briefing
{json.dumps(base_ctx['macro_briefing_summary'], indent=2, default=str)}

### Recent PM Decisions (last 10)
{json.dumps(base_ctx['recent_decisions'], indent=2, default=str)}

---
Evaluate this memo against the current portfolio state and make your entry decision. Consider: thesis quality, variant perception clarity, portfolio fit, sector concentration, regime alignment, and sizing relative to available capacity.

Respond with ONLY a valid JSON object — no markdown fences, no preamble."""

    return _SYSTEM_PROMPT, user_message
