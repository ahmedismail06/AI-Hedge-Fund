"""
Prompt builder for PRE_EARNINGS decisions.

Triggered when a position or watchlist name has an earnings release within
14 days. The PM decides: SIZE_UP | HOLD | TRIM | EXIT.
"""

import json
from typing import Any, Dict, Tuple

from backend.fetchers.earnings_reactions import get_earnings_reactions

_SYSTEM_PROMPT = """You are the portfolio manager of a US micro/small-cap equity fund. An earnings release is approaching for one of your positions. You must decide how to position before the event.

## Pre-Earnings Philosophy
Earnings events create binary risks. Your decision depends on:
1. **Conviction quality**: How confident are you in your estimate relative to consensus?
2. **Setup asymmetry**: Is the risk/reward skewed to the upside or downside?
3. **Current sizing**: Is the position already large relative to its conviction level?
4. **Macro context**: Does the current regime support taking binary event risk?
5. **Historical reaction**: How has this name historically moved on earnings?

You do NOT size up into earnings simply because you like the business. You only size up when you have a differentiated variant perception on the earnings outcome itself — a specific view on a metric or guidance item that the market is mispricing.

## Hard Constraints
- Maximum position size: 15% of portfolio
- Market hours for execution: 9:30 AM – 4:00 PM ET Mon–Fri only

## Decision Options
- SIZE_UP: Increase position before earnings — only if you have high conviction on a specific beat catalyst
- HOLD: Maintain current exposure — uncertainty is balanced or you have no edge on the outcome
- TRIM: Reduce position before the event — downside risk outweighs upside from current size
- EXIT: Close entirely before earnings — thesis is dependent on an uncertain outcome you cannot handicap

## Response Format
Respond with ONLY a valid JSON object — no markdown fences, no preamble, no trailing text.

{
  "decision": "SIZE_UP | HOLD | TRIM | EXIT",
  "reasoning": "2-3 sentences — what specific aspect of the earnings setup drives this decision, what is your edge (or lack of edge) on the outcome",
  "action_details": {
    "size_up_dollar": null,
    "trim_pct": null,
    "beat_catalyst": null,
    "re_entry_plan": null
  },
  "risk_assessment": "Primary risk of this pre-earnings positioning decision",
  "confidence": 0.0
}

For SIZE_UP: specify size_up_dollar and beat_catalyst (the specific metric/guidance you expect to surprise)
For TRIM: specify trim_pct
For EXIT: specify re_entry_plan (conditions under which you'd re-enter post-earnings)
"""


def build_pre_earnings_prompt(
    position: Dict[str, Any],
    earnings_data: Dict[str, Any],
    base_ctx: Dict[str, Any],
    original_memo: Dict[str, Any] = None,
) -> Tuple[str, str]:
    """
    Build (system_prompt, user_message) for a pre-earnings positioning decision.

    Args:
        position:      OPEN position row from Supabase
        earnings_data: Earnings setup data (consensus, estimate, days_to_earnings, etc.)
        base_ctx:      Shared base context from build_base_context()
        original_memo: The original investment memo (optional)

    Returns:
        (system_prompt, user_message) tuple for the Claude API call
    """
    ticker = position.get("ticker", "UNKNOWN")

    historical_reactions = get_earnings_reactions(ticker, n_quarters=8)

    entry = float(position.get("entry_price") or 0)
    current = float(position.get("current_price") or 0)
    shares = float(position.get("share_count") or 0)
    pnl_pct = ((current - entry) / entry) if entry > 0 else 0.0

    position_summary = {
        "ticker": ticker,
        "direction": position.get("direction"),
        "shares": shares,
        "entry_price": entry,
        "current_price": current,
        "pnl_pct": round(pnl_pct, 4),
        "pct_of_portfolio": position.get("pct_of_portfolio"),
        "conviction_score": position.get("conviction_score"),
        "next_earnings_date": str(position.get("next_earnings_date") or ""),
        "opened_at": str(position.get("opened_at") or ""),
    }

    # Build EarningsAlpha signal section from earnings_data["earnings_alpha"] if present
    ea_data = earnings_data.get("earnings_alpha", {})
    if ea_data:
        ea_signal = ea_data.get("pre_earnings_signal", "N/A")
        int_eps = ea_data.get("internal_eps_estimate")
        con_eps = ea_data.get("consensus_eps")
        surprise = ea_data.get("surprise_pct")
        drift_active = ea_data.get("drift_hold_active", False)
        drift_until = ea_data.get("drift_hold_until", "")
        int_eps_str = f"${int_eps:.2f}" if int_eps is not None else "N/A"
        con_eps_str = f"${con_eps:.2f}" if con_eps is not None else "N/A"
        surprise_str = f"{surprise:+.1%}" if surprise is not None else "N/A"
        drift_str = f"ACTIVE until {drift_until}" if drift_active else "INACTIVE"
        ea_section = (
            f"\n### EarningsAlpha Signal (quantitative pre-earnings model)\n"
            f"- Pre-earnings signal: **{ea_signal}**\n"
            f"- Internal EPS estimate: {int_eps_str}  |  Consensus: {con_eps_str}\n"
            f"- Last reported surprise: {surprise_str}\n"
            f"- Drift-hold window: {drift_str}\n"
        )
    else:
        ea_section = ""

    user_message = f"""## Decision Required: Pre-Earnings Positioning — {ticker}

### Position State
{json.dumps(position_summary, indent=2, default=str)}

### Earnings Setup Data
{json.dumps({k: v for k, v in earnings_data.items() if k != "earnings_alpha"}, indent=2, default=str)}
{ea_section}
### Historical Earnings Reactions (last 8 quarters, most recent first)
{json.dumps(historical_reactions, indent=2, default=str) if historical_reactions else "Unavailable (Polygon key missing or no history)"}

### Original Investment Memo (thesis context)
{json.dumps(original_memo, indent=2, default=str) if original_memo else "Not available"}

### Current Portfolio State
- Gross exposure: {base_ctx['portfolio_gross_exposure']:.1%}
- Net exposure: {base_ctx['portfolio_net_exposure']:.1%}
- Cash available: {base_ctx['cash_pct']:.1%}
- Macro regime: {base_ctx['macro_regime']}

### Macro Briefing
{json.dumps(base_ctx['macro_briefing_summary'], indent=2, default=str)}

---
Decide how to position this name into earnings. The EarningsAlpha signal above reflects a quantitative model comparing internal EPS extrapolation against consensus — treat it as one data point, not a directive. Be honest about whether you have a differentiated view on the earnings outcome itself — if you don't, HOLD or TRIM, not SIZE_UP.

Respond with ONLY a valid JSON object — no markdown fences, no preamble."""

    return _SYSTEM_PROMPT, user_message
