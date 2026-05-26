"""
Prompt builder for EXIT_TRIM decisions.

Triggered on a scheduled portfolio review cycle or when a risk alert fires on
a specific position. The PM decides: HOLD | TRIM | CLOSE | ADD.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from backend.agents.pm_prompts.base_context import format_calibration_context, format_capabilities_context, format_pending_actions_context

_SYSTEM_PROMPT = """You are the portfolio manager of a US micro/small-cap equity fund. You are reviewing an existing position to determine whether to hold, trim, add to, or close it.

## Investment Philosophy
You entered this position with a specific variant perception and repricing catalyst. Your job is to honestly evaluate whether that thesis still holds and whether the current size is appropriate given the portfolio context.

## Direction Awareness (critical — check before reasoning)
The position has a `direction` field of either LONG or SHORT. Every framing below has an opposite for shorts:
- **LONG**: thesis pays off when price rises. Stop_tier1/2/3 sit BELOW entry. Positive pnl_pct means the position is winning. ADD = buy more shares; TRIM/CLOSE = sell.
- **SHORT**: thesis pays off when price FALLS. Stop_tier1/2/3 sit ABOVE entry. Positive pnl_pct still means the position is winning (the data is direction-adjusted before you see it). ADD = sell more shares to expand the short; TRIM/CLOSE = buy-to-cover. A rising price is bad for a short. "Upside" for the short is downward price movement to the target.
- For a SHORT, a SIZE_UP or beat-catalyst signal designed for longs is a WARNING, not a green light.

## Key Evaluation Framework
1. **Thesis check**: Is the original variant perception still intact? Has the catalyst materialised, been disproven, or moved further away? (For SHORTs: has the bear thesis been confirmed by deteriorating fundamentals, or has the company surprised positively?)
2. **Risk/reward**: Given current price vs original entry, what is the remaining upside vs downside?
   - If valuation block is present with method=dcf or method=relative, reference upside/downside as a sanity check on current price.
   - For SHORTs: "upside" is price moving DOWN to the bear target; "downside" is squeeze / re-rating UP. A short past its bear target has little remaining edge and should generally be covered.
   - If method=none, do not rely on price targets — reason from the thesis, risk signals, and macro context only.
3. **Quality & Manipulation**: Check quality_grade and beneish_gate. A quality_grade=C combined with a MANIPULATOR flag is a hard exit signal for LONGs and a thesis-confirming signal for SHORTs (don't cover on it). If beneish_gate=INSUFFICIENT_DATA, treat quality_grade=C with caution but not as an automatic exit.
4. **Stop proximity**: How close is the current price to the stop-loss levels? For LONG, stops are below; for SHORT, stops are above. Use the provided `stop_proximity` field — it is already direction-adjusted (positive = safe, negative = breached).
5. **Earnings drift**: Is this position in a post-earnings drift hold window? (Drift-hold is currently only set for LONGs after positive surprises; for SHORTs, treat any active drift-hold as a strong squeeze warning.)
6. **Position weight**: Has this position drifted above/below its intended portfolio weight? Note: hard caps differ by direction (15% LONG, 7.5% SHORT).
7. **Opportunity cost**: Is this capital better deployed elsewhere?

## Hard Constraints
- Maximum position size: 15% of portfolio for LONG; 7.5% for SHORT (hard caps enforced in code)
- Stop-loss tiers: Tier 1 = position stop, Tier 2 = strategy stop, Tier 3 = portfolio stop (direction-aware in code)
- Market hours for execution: 9:30 AM – 4:00 PM ET Mon–Fri only

## Decision Options
- HOLD: Maintain current size — thesis intact, no action needed
- TRIM: Reduce position by a specified percentage — specify trim_pct and reason. For SHORT, this means buying back a portion to reduce notional.
- CLOSE: Full exit — specify reason (thesis broken, stop hit, rebalance, better opportunities). For SHORT, this is a full buy-to-cover.
- ADD: Increase position — thesis strengthening or price improved; specify add_dollar_amount. For SHORT, this means selling more shares to expand the short (subject to borrow availability and the 7.5% cap).

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
  "confidence": 0.0,
  "confidence_breakdown": {
    "data_quality": 0.0,
    "thesis_quality": 0.0,
    "timing": 0.0,
    "portfolio_fit": 0.0
  }
}

confidence_breakdown dimensions (each 0.0–1.0):
- data_quality: how complete is the position data, alerts, and original memo context
- thesis_quality: how clearly intact or broken is the original variant perception. Use these anchors:
  0.0–0.25 = thesis definitively broken (catalyst failed, fundamental assumption contradicted)
  0.25–0.50 = unresolved binary — thesis is alive but outcome is a coin flip on a single upcoming event with no intermediate signals
  0.50–0.75 = thesis intact and progressing — evidence accumulating toward the catalyst, intermediate signals supportive
  0.75–1.00 = thesis confirmed and ahead of expectations — catalyst materialising or repricing clearly underway
- timing: analytical/strategic timing quality of this decision — consider price action vs. recent range, stop proximity, days to next catalyst, and whether the stock is trending or mean-reverting. Default to 0.5–0.7 in normal markets; reserve <0.3 for acute stress or imminent adverse catalyst, and >0.7 for clear, decision-supporting price action. Do NOT factor in market hours (that constraint is enforced as a separate hard block and has no bearing on the quality of the analytical decision itself).
- portfolio_fit: how well does this decision fit the current portfolio exposure and regime

Set the overall `confidence` as a weighted average of the breakdown:
  confidence = (thesis_quality × 0.40) + (data_quality × 0.25) + (timing × 0.20) + (portfolio_fit × 0.15)
Round to 2 decimal places.
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
    direction = position.get("direction", "LONG")
    if direction == "SHORT":
        pnl_pct = ((entry - current) / entry) if entry > 0 else 0.0
        pnl_dollar = (entry - current) * shares
    else:
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

    # Stop proximity — direction-aware
    stop1 = position.get("stop_tier1")
    stop_proximity = None
    if stop1 and current:
        # LONG: stop is a floor; positive proximity = price is above stop (safe)
        # SHORT: stop is a ceiling; positive proximity = price is below stop (safe)
        if direction == "SHORT":
            stop_proximity = (float(stop1) - current) / float(stop1)
        else:
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

    # Valuation context — fetch latest model for this ticker
    valuation_block: Any = "unavailable"
    valuation_key = "dcf_valuation"
    if ticker:
        try:
            from backend.db.utils import get_supabase_client
            _client = get_supabase_client()
            fm_resp = (
                _client.table("financial_models")
                .select(
                    "dcf_bull_target,dcf_base_target,dcf_bear_target,wacc,quality_grade,"
                    "valuation_method,dcf_skipped_reason,beneish_gate,accruals_ratio,"
                    "relative_bull_target,relative_base_target,relative_bear_target,ev_revenue_used,peer_count"
                )
                .eq("ticker", ticker)
                .order("run_date", desc=True)
                .limit(1)
                .execute()
            )
            if fm_resp.data:
                fm = fm_resp.data[0]
                vm = fm.get("valuation_method") or "dcf"
                
                # Quality signals included in all branches
                quality = {
                    "quality_grade": fm.get("quality_grade"),
                    "beneish_gate": fm.get("beneish_gate"),
                    "accruals_ratio": float(fm["accruals_ratio"]) if fm.get("accruals_ratio") else None,
                }

                if vm == "none":
                    valuation_key = "valuation"
                    valuation_block = {
                        "method": "none",
                        "skipped_reason": fm.get("dcf_skipped_reason") or "no_model",
                        "note": "No reliable valuation model could be computed for this ticker. Use thesis quality and risk signals instead of price targets.",
                        **quality
                    }
                elif vm == "relative":
                    valuation_key = "relative_valuation"
                    rb = float(fm["relative_bull_target"]) if fm.get("relative_bull_target") else None
                    rb_base = float(fm["relative_base_target"]) if fm.get("relative_base_target") else None
                    rb_bear = float(fm["relative_bear_target"]) if fm.get("relative_bear_target") else None
                    valuation_block = {
                        "method": "relative",
                        "ev_revenue_multiple": float(fm["ev_revenue_used"]) if fm.get("ev_revenue_used") else None,
                        "peer_count": fm.get("peer_count"),
                        "bull_target": rb,
                        "base_target": rb_base,
                        "bear_target": rb_bear,
                        "upside_to_bull": round((rb - current) / current, 4) if (rb and current) else None,
                        "downside_to_bear": round((current - rb_bear) / current, 4) if (rb_bear and current) else None,
                        **quality
                    }
                else: # dcf
                    valuation_key = "dcf_valuation"
                    bull = float(fm["dcf_bull_target"]) if fm.get("dcf_bull_target") else None
                    bear = float(fm["dcf_bear_target"]) if fm.get("dcf_bear_target") else None
                    valuation_block = {
                        "method": "dcf",
                        "dcf_bull_target": bull,
                        "dcf_base_target": float(fm["dcf_base_target"]) if fm.get("dcf_base_target") else None,
                        "dcf_bear_target": bear,
                        "upside_to_bull": round((bull - current) / current, 4) if (bull and current) else None,
                        "downside_to_bear": round((current - bear) / current, 4) if (bear and current) else None,
                        **quality
                    }
        except Exception:
            pass

    position_summary[valuation_key] = valuation_block

    user_message = f"""## Decision Required: Position Review — {ticker}

### Position State
{json.dumps(position_summary, indent=2, default=str)}

### Active Risk Alerts for {ticker}
{json.dumps(alerts, indent=2, default=str) if alerts else "None"}

### Original Thesis (key fields from entry memo)
{json.dumps(thesis_summary, indent=2, default=str) if original_memo else "Not available"}

### Adversarial Risks (red-team from original research)
{json.dumps(top_risks, indent=2) if top_risks else "None recorded"}

### Recent Decisions for {ticker}
{json.dumps(base_ctx['ticker_history'], indent=2, default=str) if base_ctx.get('ticker_history') else "No previous decisions found for this ticker."}

### Current Portfolio State
- Gross exposure: {base_ctx['portfolio_gross_exposure']:.1%}
- Net exposure: {base_ctx['portfolio_net_exposure']:.1%}
- Cash available: {base_ctx['cash_pct']:.1%}{(' ($' + f"{base_ctx['cash_usd']:,.0f}" + ' live)') if base_ctx.get('cash_usd') is not None else ''}
- Open positions: {base_ctx['position_count']}
- Macro regime: {base_ctx['macro_regime']}
- Regime gross cap: {base_ctx['regime_caps']['gross']:.0%} | Net cap: {base_ctx['regime_caps']['net']:.0%}

### Macro Briefing
{json.dumps(base_ctx['macro_briefing_summary'], indent=2, default=str)}

{format_pending_actions_context(base_ctx)}{format_calibration_context(base_ctx)}{format_capabilities_context(base_ctx)}---
Evaluate this position and decide whether to hold, trim, close, or add. Focus on: thesis integrity, stop proximity, current P&L, adversarial risks, and portfolio-level fit.

Respond with ONLY a valid JSON object — no markdown fences, no preamble."""

    return _SYSTEM_PROMPT, user_message
