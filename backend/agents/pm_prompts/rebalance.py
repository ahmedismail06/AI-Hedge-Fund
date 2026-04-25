"""
Prompt builder for REBALANCE decisions.

Triggered on the daily review cycle or after any position change moves
gross/net exposure outside the regime guidance. The PM decides:
NO_ACTION | REBALANCE | RAISE_CASH | DEPLOY_CASH.
"""

import json
from typing import Any, Dict, Tuple

from backend.agents.pm_prompts.base_context import format_calibration_context

_SYSTEM_PROMPT = """You are the portfolio manager of a US micro/small-cap equity fund. You are conducting a portfolio-level rebalancing review to ensure the portfolio's exposure and sector weights are appropriate for the current macro regime and risk environment.

## Rebalancing Philosophy
You do not rebalance mechanically. You rebalance when:
1. Gross or net exposure has drifted meaningfully outside regime guidance
2. A single sector has become overly concentrated (>25% gross from 3+ positions)
3. The portfolio is significantly under-invested relative to available opportunities and the regime supports deployment
4. Cash has accumulated to a level where opportunity cost is material

You prioritise quality of holdings over hitting precise exposure targets. A slightly over-exposed portfolio with strong positions is better than a precisely-weighted portfolio of weaker ideas.

## Regime Exposure Guidance (these are targets, not hard limits below 200% gross)
- Risk-On: ~150% gross, ~50% net (long-biased, full deployment)
- Risk-Off: ~80% gross, ~10% net (defensive, cash-heavy)
- Transitional: ~120% gross, ~20% net (moderate, selective)
- Stagflation: ~100% gross, ~0% net (balanced long/short or cash)

## Hard Constraint
- 200% gross ceiling is absolute — new entries blocked above this regardless of regime

## Decision Options
- NO_ACTION: Portfolio is within acceptable bounds for the current regime
- REBALANCE: Specific trims/additions to bring weights back to target — list each adjustment
- RAISE_CASH: Reduce overall exposure by closing the weakest positions — specify which and why
- DEPLOY_CASH: Portfolio is under-invested relative to regime guidance — specify criteria for deployment

## Response Format
Respond with ONLY a valid JSON object — no markdown fences, no preamble, no trailing text.

{
  "decision": "NO_ACTION | REBALANCE | RAISE_CASH | DEPLOY_CASH",
  "reasoning": "2-3 sentences — what specifically is off-target and why this action is the right response",
  "action_details": {
    "adjustments": [],
    "target_gross_exposure": null,
    "target_net_exposure": null,
    "deploy_criteria": null
  },
  "risk_assessment": "Primary risk of this rebalancing decision",
  "confidence": 0.0,
  "confidence_breakdown": {
    "data_quality": 0.0,
    "thesis_quality": 0.0,
    "timing": 0.0,
    "portfolio_fit": 0.0
  }
}

confidence_breakdown dimensions (each 0.0–1.0):
- data_quality: how reliable is the exposure and macro data driving this assessment
- thesis_quality: how clearly does the portfolio state deviate from regime guidance
- timing: how well-timed is this rebalancing relative to market conditions
- portfolio_fit: how appropriate is the target exposure given the current opportunity set

For REBALANCE: adjustments is a list of {"ticker": str, "action": "TRIM|ADD", "pct_change": float, "reason": str}
For RAISE_CASH: adjustments lists positions to close with reason
For DEPLOY_CASH: deploy_criteria describes what opportunities would trigger deployment
"""


def build_rebalance_prompt(base_ctx: Dict[str, Any]) -> Tuple[str, str]:
    """
    Build (system_prompt, user_message) for a portfolio rebalancing decision.

    Args:
        base_ctx: Shared base context from build_base_context()

    Returns:
        (system_prompt, user_message) tuple for the Claude API call
    """
    # Build sector concentration summary — gross (abs) and net (signed)
    sector_gross: Dict[str, float] = {}
    sector_net: Dict[str, float] = {}
    for p in base_ctx["positions"]:
        sector = p.get("sector", "Unknown")
        w = float(p.get("pct_of_portfolio") or 0.0)
        direction = p.get("direction", "LONG")
        signed_w = w if direction == "LONG" else -abs(w)
        sector_gross[sector] = sector_gross.get(sector, 0.0) + abs(w)
        sector_net[sector] = sector_net.get(sector, 0.0) + signed_w
    # Keep legacy name for the concentrated_sectors check below
    sector_weights = sector_gross

    # Flag over-concentrated sectors (>25% gross, 3+ positions)
    sector_counts: Dict[str, int] = {}
    for p in base_ctx["positions"]:
        sector = p.get("sector", "Unknown")
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

    concentrated_sectors = {
        s: {"weight": round(w, 4), "positions": sector_counts.get(s, 0)}
        for s, w in sector_weights.items()
        if w > 0.25 and sector_counts.get(s, 0) >= 3
    }

    regime = base_ctx["macro_regime"]
    caps = base_ctx["regime_caps"]
    gross = base_ctx["portfolio_gross_exposure"]
    net = base_ctx["portfolio_net_exposure"]

    # Compute drift from regime targets
    gross_drift = gross - caps["gross"]
    net_drift = net - caps["net"]

    portfolio_overview = {
        "positions": [
            {
                "ticker": p.get("ticker"),
                "direction": p.get("direction"),
                "pct_of_portfolio": p.get("pct_of_portfolio"),
                "sector": p.get("sector"),
                "conviction_score": p.get("conviction_score"),
                "pnl_pct": round(
                    ((float(p.get("current_price") or 0) - float(p.get("entry_price") or 1))
                     / float(p.get("entry_price") or 1)), 4
                ) if p.get("entry_price") else None,
            }
            for p in base_ctx["positions"]
        ],
        "gross_exposure": gross,
        "net_exposure": net,
        "cash_pct": base_ctx["cash_pct"],
        "position_count": base_ctx["position_count"],
    }

    user_message = f"""## Decision Required: Portfolio Rebalancing Review

### Current Portfolio
{json.dumps(portfolio_overview, indent=2, default=str)}

### Sector Breakdown
Gross (abs weight — concentration risk):
{json.dumps({s: round(w, 4) for s, w in sector_gross.items()}, indent=2)}
Net (signed weight — directional bet):
{json.dumps({s: round(w, 4) for s, w in sector_net.items()}, indent=2)}

### Concentrated Sectors (>25% gross, 3+ positions)
{json.dumps(concentrated_sectors, indent=2, default=str) if concentrated_sectors else "None"}

### Regime Alignment
- Current regime: {regime}
- Target gross: {caps['gross']:.0%} | Actual gross: {gross:.1%} | Drift: {gross_drift:+.1%}
- Target net: {caps['net']:.0%} | Actual net: {net:.1%} | Drift: {net_drift:+.1%}

### Active Risk Alerts
{json.dumps(base_ctx['active_alerts'], indent=2, default=str) if base_ctx['active_alerts'] else "None"}

### Macro Briefing
{json.dumps(base_ctx['macro_briefing_summary'], indent=2, default=str)}

{format_calibration_context(base_ctx)}---
Assess whether the portfolio requires rebalancing given the current regime guidance, sector concentration, and exposure drift. Remember: rebalance only when deviation is meaningful, not mechanical.

Respond with ONLY a valid JSON object — no markdown fences, no preamble."""

    return _SYSTEM_PROMPT, user_message
