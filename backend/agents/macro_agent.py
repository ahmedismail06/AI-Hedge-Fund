"""
Macro Agent — daily regime intelligence pipeline.

Orchestrates the full macro data fetch → LLM regime decision → Supabase write cycle.
Called by the APScheduler cron at 7AM ET Mon–Fri.

Pipeline phases:
  1. Fetch:   FRED indicators, market stress data, FOMC text
  2. Assemble: Build RawIndicators and format raw values for the LLM
  3. LLM:     Single LLM call — scores all dimensions, classifies regime, sets
              confidence, determines sector tilts and portfolio guidance
  4. Regime:  Check regime change vs yesterday
  5. Build:   Assemble MacroBriefing Pydantic model
  6. Store:   Upsert to Supabase macro_briefings table

The LLM receives raw indicator values (not pre-computed scores) and decides
everything. scorer.py scoring functions are retained for reference but not called.
"""

from dotenv import load_dotenv

load_dotenv()

import json
import logging
import os
import re
from datetime import date
from typing import Optional

# CLAUDE SWAP — switched to OpenAI for testing
# import anthropic
from openai import OpenAI

from backend.macro.indicators.fred_fetcher import fetch_fred_block, FredFetchError
from backend.macro.indicators.market_fetcher import fetch_market_block
from backend.macro.indicators.fed_scraper import get_fed_text
from backend.macro.scorer import (
    RawIndicators,
    build_raw_indicators,
    build_indicator_scores,
)
from backend.models.macro_briefing import MacroBriefing, IndicatorScore, SectorTilt, UpcomingEvent
from backend.memory.vector_store import _get_client
from backend.notifications.events import notify_event

logger = logging.getLogger(__name__)

# CLAUDE SWAP — OpenAI for testing
def _build_openai_client() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "o4-mini")


class MacroAgentError(Exception):
    pass


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

MACRO_SYSTEM_PROMPT = """You are a senior macro strategist at a long/short hedge fund running a $50M–$2B US micro/small-cap
equity book (SaaS, Healthcare, Industrials). You operate at 7AM ET daily. Your sole function is to
synthesize raw macroeconomic indicator data and Federal Reserve qualitative language into a
regime verdict and actionable portfolio briefing. You receive raw indicator values — not
pre-computed scores — and derive everything from first principles.

You are NOT a market commentator. You are NOT a news summarizer. You are a regime classifier and
portfolio-level signal generator. A strategist who writes "markets remain uncertain" or "investors
should watch inflation" has failed. Every output sentence must carry a specific implication for
positioning, exposure, or sector tilt.

What failure looks like in this role: producing a qualitative_summary that restates the numbers
already in the indicator table, writing key_themes that could have been written any week in the
last 18 months, or setting override_flag to true without a concrete reasoning for deviating from
the Step 3 priority-order guardrails.

---

INDICATOR REFERENCE — Raw Value Context:

Use these ranges as calibration anchors. They reflect historical norms for the US economy.
Use your judgment — an indicator at a boundary carries less signal than one at an extreme.

GROWTH:
  GDP YoY (%): Real GDP year-over-year growth. Trend: ~2.0%.
    < -1.5%: severe contraction.  -1.5 to 0%: mild contraction.  0–1.0%: stall speed.
    1.0–2.5%: moderate expansion.  > 2.5%: strong expansion.

  ISM Services PMI: Diffusion index, 50-neutral.
    < 48: contraction.  48–50: mild contraction.  50–52: neutral expansion.
    52–55: solid expansion.  > 55: strong expansion.

  Payrolls MoM %: Month-over-month nonfarm payroll % change.
    Approximately: 0.09% ≈ +140K jobs, 0.13% ≈ +200K jobs. Negative = job losses.
    < 0%: contraction.  0–0.03%: near-stall.  0.03–0.06%: soft.
    0.06–0.13%: healthy.  > 0.13%: strong.

  Initial Jobless Claims (weekly headcount, NOT thousands):
    < 200K: historically tight.  200–240K: solid.  240–280K: neutral.
    280–320K: softening.  > 320K: deteriorating.

INFLATION:
  CPI YoY / Core CPI YoY (%): Fed target: ~2.0% PCE (CPI runs ~0.3pp above PCE).
    < 1.0%: deflation risk.  1.0–2.0%: below target.  2.0–3.0%: at/near target.
    3.0–5.0%: elevated.  > 5.0%: high inflation.

  PPI YoY (%): Producer prices; leads CPI by 1–3 months.
    < 0%: deflationary upstream.  0–1.0%: flat.  1.0–3.0%: normal.
    3.0–6.0%: elevated pipeline pressure.  > 6.0%: high upstream inflation.

  PCE YoY (%): Fed's preferred gauge. Target: 2.0%.
    < 1.5%: well below target.  1.5–2.0%: below target.  2.0–2.5%: at target.
    2.5–4.0%: above target.  > 4.0%: high inflation.

  5Y Breakeven (%): Market-implied 5-year inflation expectations.
    < 1.5%: de-anchored downside.  1.5–2.0%: below-target anchoring.
    2.0–2.5%: well-anchored.  2.5–3.0%: elevated.  > 3.0%: unanchored.

FED / RATES:
  Rate Direction (-1.0 to +1.0): FEDFUNDS trend.
    +1.0: large easing cycle.  +0.5: small cuts.  0.0: on hold.
    -0.5: small hikes.  -1.0: large tightening cycle.

  Yield Curve (10Y-2Y, bps):
    < -100 bps: severely inverted (historical recession precursor).
    -100 to -50: inverted.  -50 to 0: flat/mildly inverted.
    0–50: normalizing.  50–100: normal steep.  > 100: very steep / accommodative.

MARKET STRESS:
  VIX:
    < 12: extreme complacency.  12–15: very calm.  15–20: normal.
    20–30: elevated fear.  > 30: crisis/panic.  > 40: systemic fear.

  HY Spread (bps OAS, ICE BofA HY Index):
    < 200: boom-time.  200–300: tight/benign.  300–450: normal.
    450–600: stressed.  > 600: crisis-level.

  DXY (US Dollar Index):
    < 95: weak.  95–100: neutral-weak.  100–104: neutral.
    104–108: strong (headwind for EM/commodities).  > 108: very strong / stress signal.

  SPX vs 200-day SMA (%):
    < -10%: deep bear territory.  -5 to -10%: well below trend.  -5 to -2%: below trend.
    -2 to +2%: near trend.  +2 to +10%: above trend (bullish).  > +10%: extended (mean-reversion risk).

---

THINKING ORDER — complete these steps in sequence before producing any output:

1. FOMC TEXT ASSESSMENT:
   Read the FOMC statement text provided. Assess the dominant policy tone using the FED_TONE
   SCORING RULE below. If the text is empty or absent, set fed_tone = 0.0 and note
   "FOMC text unavailable" in fed_tone_rationale. Do not infer Fed language from general
   knowledge — only score what is explicitly in the text.

2. DIMENSIONAL SCORING:
   Using the raw indicator values and INDICATOR REFERENCE above, score each dimension in [-1.0, +1.0]:

   growth_score: GDP YoY, ISM Services PMI, Payrolls MoM %, Jobless Claims.
     Positive = expanding. Negative = contracting. 0 = neutral/mixed.
     Weight toward freshest data and strongest signals.
     If all growth indicators are missing, return 0.0.

   inflation_score: CPI YoY, Core CPI YoY, PPI YoY, PCE YoY, 5Y Breakeven.
     Positive = inflationary pressure above Fed target. Negative = below-target / disinflationary.
     5Y Breakeven carries significant weight — it captures forward-looking market consensus.
     If all inflation indicators are missing, return 0.0.

   fed_score: Rate Direction, Yield Curve Spread, and your fed_tone from Step 1.
     Positive = accommodative. Negative = restrictive.
     Yield curve inversion is a strong negative even if rate direction is neutral.
     If rate direction and yield curve are both missing, base entirely on fed_tone.

   stress_score: VIX, HY Spread, DXY, SPX vs 200SMA.
     Positive = high stress / risk-off conditions. Negative = calm / risk-on.
     VIX and HY Spread are primary; DXY and SPX vs SMA are secondary.
     If all stress indicators are missing, return 0.0.

   If fewer than 2 indicators are available for a dimension, note data sparsity but still
   provide your best estimate from available signals.

3. REGIME CLASSIFICATION:
   Classify as one of: Risk-On | Risk-Off | Stagflation | Transitional.
   Evaluate in priority order (use your dimensional scores from Step 2):

   a. Risk-On: growth_score > 0 AND inflation_score < 0.5 AND stress_score < 0.3. All three must hold.
   b. Risk-Off: stress_score > 0.4 OR (growth_score < -0.3 AND fed_score < 0). Stress overrides all.
   c. Stagflation: growth_score < 0 AND inflation_score > 0.6.
   d. Transitional: none of the above — signals genuinely mixed or inconclusive.

   These guardrails are directional guides, not mechanical rules. If FOMC language provides
   a clear, explicit signal that the above priority order alone would miss, you may deviate —
   set override_flag = true and explain in override_reason. Minor hedging language is NOT a
   basis for override.

4. REGIME CONFIDENCE (required for ALL regimes):
   Assign regime_confidence as a float in [0.0, 10.0]. Reflects signal clarity and
   cross-dimensional agreement with your classified regime.

   0–3: Data sparse, stale, or signals genuinely split. Cannot form a confident view.
   4–5: Mixed signals, dimensions pulling in opposite directions, no dominant pattern.
   6–7: Majority of signals align; one or two outliers.
   8–9: Strong, unambiguous alignment across all four dimensions.
   9–10: Reserve for extreme regimes where all signals are maximal and unambiguous.

   For Transitional additionally:
   1–3: Pure uncertainty, no directional lean. 4–5: Mixed, no pivot signal yet.
   6–7: Can name direction of travel (e.g., "inflecting toward Risk-On").
   8–9: Imminent resolution — one release could flip the regime.

5. REGIME SCORE (0–100):
   Assign regime_score in [0.0, 100.0]. Macro health score for long/short equity:
   higher = better capital deployment environment.
   Score ≈ 100: strong Risk-On, all dimensions favorable.
   Score ≈ 0: crisis Risk-Off, all dimensions maximally stressed.

   Starting formula (adjust ±10 if your holistic view differs materially):
   base = ((growth_score+1)/2 × 35) + ((1−inflation_score)/2 × 30) + ((fed_score+1)/2 × 20) + ((1−stress_score)/2 × 15)
   Multiply by 100. Clamp to [0, 100].

6. SECTOR TILTS (SaaS, Healthcare, Industrials):
   Provide tilt — "overweight", "neutral", or "underweight" — for each sector.
   Rationale must reference actual indicator values.

   Regime defaults:
   Risk-On: SaaS overweight; Industrials overweight (book-to-bill > 1.0); Healthcare neutral.
   Risk-Off: SaaS underweight; Healthcare overweight; Industrials neutral (backlog coverage > 12m).
   Stagflation: SaaS underweight; Healthcare overweight (CMS reimbursement certainty); Industrials neutral.
   Transitional OR regime_confidence < 7.0: all three neutral. Name what signal would change each tilt.

7. QUALITATIVE SYNTHESIS:
   Write qualitative_summary (3–5 sentences, hard limit) synthesizing the macro picture
   ACROSS all four dimensions. Do not write one sentence per dimension — connect dimensions causally.
   CONTRADICTION RULE: If dimensions point in opposing directions, name the tension explicitly
   and identify the BINDING CONSTRAINT for positioning. State which signal wins and why.
   The final sentence must name the single largest forward risk or tailwind.

8. KEY THEMES:
   Write 2–4 forward-looking theme strings in institutional research note style. Must be specific
   to the current readings — not generic observations valid for any week in the last 18 months.

9. PORTFOLIO GUIDANCE:
   Write 2–3 sentences naming the regime and its implications for: (a) gross exposure level,
   (b) sector preference, and (c) stop-loss posture. Reference the REGIME PORTFOLIO IMPLICATIONS
   section below.

10. UPCOMING EVENTS:
    List 2–4 macro events in the next 30 days: FOMC meetings, CPI/PCE releases, payroll dates,
    major GDP prints. Use knowledge of the Fed calendar and BLS release schedule for the current date.
    If a date is uncertain, add "(approx)" to the event name. Include relevance to the current regime.

---

FED_TONE SCORING — MECHANICAL RULE:
Score starts at 0.0. Evaluate each criterion as present or absent in the actual FOMC text:
+0.3  Explicit "patient" or "data-dependent" language suggesting no near-term rate change
+0.3  Any reference to rate cuts being appropriate or under consideration
+0.2  Acknowledgment that inflation is progressing toward target
+0.2  Downward revision language on inflation or upward risk language on employment
-0.3  Explicit "higher for longer" or "additional firming may be appropriate" language
-0.3  Any reference to rate hikes being under consideration or remaining on the table
-0.2  Language emphasizing inflation risks are "not yet" resolved or "still elevated"
-0.2  Hawkish dissents mentioned or unanimous vote on a restrictive decision
Hard floor: -1.0. Hard ceiling: +1.0. Clamp to range after summing.
If FOMC text is empty: fed_tone = 0.0. Do not infer.

---

FED_TONE REFERENCE CALIBRATION:
Maximally dovish (+1.0): FOMC explicitly signals imminent rate cuts, acknowledges inflation
at target, references "appropriate to begin reducing" the policy rate.
Maximally hawkish (-1.0): FOMC signals additional rate increases warranted, inflation
significantly above target, economic strength permits continued tightening.
Neutral (0.0): text unavailable, or balanced statement with no explicit forward-guidance signals.

---

REGIME PORTFOLIO IMPLICATIONS:

Risk-On:
  Gross exposure: up to 150% gross (full long deployment)
  Sector tilt: favor SaaS (revenue visibility) and high-growth Industrials (book-to-bill > 1.0)
  Stop posture: standard Tier 1 at -8% position, Tier 2 at -15% strategy

Risk-Off:
  Gross exposure: reduce to 80% gross maximum; no new longs above medium conviction
  Sector tilt: rotate toward asset-light Healthcare and Industrials with backlog coverage > 12 months
  Stop posture: tighten to Risk-Off stops: Tier 1 at -5%, Tier 2 at -10%

Stagflation:
  Gross exposure: reduce to 60–70% gross; hold only highest-conviction positions
  Sector tilt: avoid high-multiple SaaS; favor Industrials with pricing power; Healthcare with CMS reimbursement certainty
  Stop posture: Risk-Off stops apply

Transitional:
  Gross exposure: hold current book at reduced size
  Entry sizing: Small (≤3% NAV) actionable on high-quality setups. Medium (3–6% NAV) require conviction ≥ 7.5. Large (>6% NAV) deferred until regime_confidence ≥ 7.0.
  Sector tilt: neutral across all three sectors
  Stop posture: standard stops; wait for regime clarity before sizing up large positions

---

MISSING DATA HANDLING:
  If FOMC text is empty: fed_tone = 0.0; note "FOMC text unavailable" in fed_tone_rationale.
  If fewer than 2 indicators available for a dimension: assign 0.0 and note the gap.
  If all indicators across all dimensions are None: return regime="Transitional",
    regime_confidence=0.0, regime_score=50.0, all dimensional scores=0.0, override_flag=false,
    and explain data unavailability in qualitative_summary.
  If regime_confidence < 4.0: qualitative_summary must explicitly acknowledge signal conflict.

---

CRITICAL FORMATTING RULES:
  - Respond with a single valid JSON object ONLY
  - No markdown, no code fences, no text before or after the JSON
  - ALL fields are required — do not omit any
  - "regime" is always required (not conditional on override_flag)
  - "regime_confidence" is always required for all regimes
  - All score fields are always required
  - fed_tone must be a float (number), not a string
  - override_flag=true means you deviated from Step 3 priority-order guardrails; explain in override_reason
  - override_flag=false means you followed the priority order; override_reason must be null

Required JSON schema:
{
  "growth_score": <float in [-1.0, +1.0]>,
  "inflation_score": <float in [-1.0, +1.0]>,
  "fed_score": <float in [-1.0, +1.0]>,
  "stress_score": <float in [-1.0, +1.0]>,
  "regime": <"Risk-On" | "Risk-Off" | "Stagflation" | "Transitional">,
  "regime_score": <float in [0.0, 100.0]>,
  "regime_confidence": <float in [0.0, 10.0]>,
  "fed_tone": <float in [-1.0, +1.0]>,
  "fed_tone_rationale": "derivation string",
  "override_flag": <boolean>,
  "override_reason": "string or null",
  "sector_tilts": [
    {"sector": "SaaS", "tilt": "<overweight|neutral|underweight>", "rationale": "string referencing actual indicator values"},
    {"sector": "Healthcare", "tilt": "<overweight|neutral|underweight>", "rationale": "string referencing actual indicator values"},
    {"sector": "Industrials", "tilt": "<overweight|neutral|underweight>", "rationale": "string referencing actual indicator values"}
  ],
  "qualitative_summary": "3–5 sentences",
  "key_themes": ["theme1", "theme2"],
  "portfolio_guidance": "2–3 sentences naming regime, exposure ceiling, sector tilt, stop posture",
  "upcoming_events": [
    {"date": "YYYY-MM-DD", "event": "event name", "relevance": "why it matters to current regime"}
  ]
}
HARD RULES: Single valid JSON object ONLY. All fields required. upcoming_events: 2–4 events."""


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------


def _read_previous_regime() -> Optional[str]:
    """Query macro_briefings for the most recent regime string.

    Returns the regime string from the latest row, or None if the table is
    empty or the query fails. Failures are logged at WARNING — never raised.
    """
    try:
        client = _get_client()
        result = (
            client.table("macro_briefings")
            .select("regime")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0].get("regime")
        return None
    except Exception as exc:
        logger.warning("_read_previous_regime: Supabase query failed — %s", exc)
        return None


def _trigger_regime_requeue(new_regime: str, prev_regime: Optional[str]) -> None:
    """Queue all OPEN positions for full re-research (P1) when the regime changes.

    Called from _store_briefing() after a successful upsert. Sets material_event=True
    and priority=1 on each held ticker's watchlist entry so the research scheduler
    processes them at the top of its next run.
    Non-fatal — exceptions are logged and swallowed.
    """
    if not prev_regime or prev_regime == new_regime:
        return

    logger.info(
        "Regime change %s → %s: queuing all held positions for P1 re-research",
        prev_regime, new_regime,
    )
    try:
        client = _get_client()
        open_pos = (
            client.table("positions")
            .select("ticker")
            .eq("status", "OPEN")
            .execute()
            .data
        ) or []
        tickers = [r["ticker"] for r in open_pos]
        if not tickers:
            logger.info("_trigger_regime_requeue: no open positions to re-queue")
            return

        today_str = date.today().isoformat()
        reason = f"regime_change:{prev_regime}→{new_regime}"
        for ticker in tickers:
            try:
                client.table("watchlist").update(
                    {
                        "material_event": True,
                        "material_event_reason": reason,
                        "queued_for_research": True,
                        "priority": 1,
                    }
                ).eq("ticker", ticker).eq("run_date", today_str).execute()
            except Exception as exc:
                logger.warning(
                    "_trigger_regime_requeue: watchlist update failed for %s — %s", ticker, exc
                )

        logger.info(
            "_trigger_regime_requeue: queued %d tickers for P1 re-research: %s",
            len(tickers), tickers,
        )
    except Exception as exc:
        logger.warning("_trigger_regime_requeue: failed — %s", exc)


def _store_briefing(briefing: MacroBriefing) -> str:
    """Upsert a MacroBriefing to the macro_briefings Supabase table.

    Conflict resolution is on the 'date' column — re-running the pipeline on
    the same calendar day updates the existing row rather than creating a duplicate.

    Returns the inserted/updated row ID string, or "stored" if the ID is unavailable.
    Storage failures are logged at WARNING and do not raise — the caller always
    receives the briefing object regardless of persistence outcome.
    """
    try:
        row = {
            "date": briefing.date,
            "regime": briefing.regime,
            "regime_score": briefing.regime_score,
            "previous_regime": briefing.previous_regime,
            "regime_changed": briefing.regime_changed,
            "growth_score": briefing.growth_score,
            "inflation_score": briefing.inflation_score,
            "fed_score": briefing.fed_score,
            "stress_score": briefing.stress_score,
            "regime_confidence": briefing.regime_confidence,
            "override_flag": briefing.override_flag,
            "override_reason": briefing.override_reason,
            "qualitative_summary": briefing.qualitative_summary,
            "key_themes": briefing.key_themes,
            "portfolio_guidance": briefing.portfolio_guidance,
            "indicator_scores": [s.model_dump() for s in briefing.indicator_scores],
            "sector_tilts": (
                [t.model_dump() for t in briefing.sector_tilts]
                if briefing.sector_tilts
                else None
            ),
            "upcoming_events": (
                [e.model_dump() for e in briefing.upcoming_events]
                if briefing.upcoming_events
                else None
            ),
            "briefing_json": briefing.model_dump(),
        }
        client = _get_client()
        result = client.table("macro_briefings").upsert(row, on_conflict="date").execute()
        if result.data and result.data[0].get("id"):
            stored_id = str(result.data[0]["id"])
        else:
            stored_id = "stored"

        # Regime change re-queue: queue all held positions for P1 re-research
        if briefing.regime_changed:
            _trigger_regime_requeue(briefing.regime, briefing.previous_regime)

        return stored_id
    except Exception as exc:
        logger.warning("_store_briefing: Supabase upsert failed — %s", exc)
        return "storage_failed"


# ---------------------------------------------------------------------------
# Raw indicator formatter (LLM input)
# ---------------------------------------------------------------------------


def _format_raw_indicators_for_llm(ind: RawIndicators) -> str:
    """Format raw indicator values for the LLM user message.

    Produces a plain-text block of raw indicator values organised by category,
    with units and context notes. Does NOT include pre-computed scores — the
    LLM derives all scores and regime classification from the raw values directly.

    Parameters
    ----------
    ind:
        Assembled RawIndicators snapshot.

    Returns
    -------
    str
        Multi-line formatted raw indicator block for injection into the LLM user message.
    """
    today_str = date.today().isoformat()

    def _row(label: str, value, fmt: str, unit: str = "", note: str = "") -> str:
        if value is None:
            return f"  {label:<26} [not available]"
        formatted = format(value, fmt)
        line = f"  {label:<26} {formatted}{unit}"
        if note:
            line += f"  ({note})"
        return line

    rd = ind.rate_direction
    rd_label = "easing" if rd > 0 else ("tightening" if rd < 0 else "on hold")

    lines = [
        f"MACRO INDICATORS — {today_str}",
        "=" * 42,
        "",
        "GROWTH:",
        _row("GDP YoY", ind.gdp_yoy, ".2f", "%"),
        _row("ISM Services PMI", ind.ism_svc, ".1f", "", "50 = neutral"),
        _row("Payrolls MoM %", ind.payrolls_mom_pct, "+.3f", "%",
             "approx: 0.09% ≈ +140K jobs"),
        _row("Jobless Claims", ind.jobless_claims, ",.0f", "", "weekly headcount"),
        "",
        "INFLATION:",
        _row("CPI YoY", ind.cpi_yoy, ".2f", "%"),
        _row("Core CPI YoY", ind.core_cpi_yoy, ".2f", "%"),
        _row("PPI YoY", ind.ppi_yoy, ".2f", "%"),
        _row("PCE YoY", ind.pce_yoy, ".2f", "%", "Fed target: 2.0%"),
        _row("5Y Breakeven", ind.breakeven_5y, ".2f", "%", "market inflation expectations"),
        "",
        "FED / RATES:",
        f"  {'Rate Direction':<26} {rd:+.1f}  ({rd_label}; -1=tightening, +1=easing)",
        _row("Yield Curve (10Y-2Y)", ind.yield_curve_spread, "+.0f", " bps",
             "negative = inverted"),
        "",
        "MARKET STRESS:",
        _row("VIX", ind.vix, ".1f", "", "< 15 calm, > 20 elevated, > 30 crisis"),
        _row("HY Spread", ind.hy_spread, ".0f", " bps", "< 300 normal, > 450 stressed"),
        _row("DXY", ind.dxy, ".1f", "", "> 104 strong dollar"),
        _row("SPX vs 200SMA", ind.spx_pct_above_sma, "+.1f", "%",
             "above/below 200-day moving avg"),
    ]

    # Explicit missing-indicator summary so the LLM knows what's absent
    missing = [
        name for name, val in [
            ("GDP YoY", ind.gdp_yoy),
            ("ISM Services PMI", ind.ism_svc),
            ("Payrolls MoM %", ind.payrolls_mom_pct),
            ("Jobless Claims", ind.jobless_claims),
            ("CPI YoY", ind.cpi_yoy),
            ("Core CPI YoY", ind.core_cpi_yoy),
            ("PPI YoY", ind.ppi_yoy),
            ("PCE YoY", ind.pce_yoy),
            ("5Y Breakeven", ind.breakeven_5y),
            ("Yield Curve Spread", ind.yield_curve_spread),
            ("VIX", ind.vix),
            ("HY Spread", ind.hy_spread),
            ("DXY", ind.dxy),
            ("SPX vs 200SMA", ind.spx_pct_above_sma),
        ]
        if val is None
    ]
    lines.append("")
    lines.append(
        f"MISSING INDICATORS: {', '.join(missing)}" if missing else "MISSING INDICATORS: none"
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claude call
# ---------------------------------------------------------------------------


_VALID_REGIMES = {"Risk-On", "Risk-Off", "Stagflation", "Transitional"}
_VALID_TILTS = {"overweight", "neutral", "underweight"}

_LLM_REQUIRED_KEYS = {
    "growth_score", "inflation_score", "fed_score", "stress_score",
    "regime", "regime_score", "regime_confidence",
    "fed_tone", "override_flag",
    "qualitative_summary", "key_themes", "portfolio_guidance",
    "sector_tilts", "upcoming_events",
}


def _call_llm(raw_indicator_block: str, fomc_text: str) -> dict:
    """Call the LLM to classify the macro regime and produce a full briefing.

    The LLM receives raw indicator values (no pre-computed scores) and decides
    everything: dimensional scores, regime, confidence, sector tilts, portfolio
    guidance, and all qualitative fields.

    Parameters
    ----------
    raw_indicator_block:
        Formatted raw indicator table from _format_raw_indicators_for_llm().
    fomc_text:
        Raw FOMC statement text, or "" if unavailable.

    Returns
    -------
    dict
        Validated and clamped parsed JSON with all required keys.

    Raises
    ------
    MacroAgentError
        If JSON parsing fails, required keys are missing, or values are invalid.
    """
    fomc_section = (
        f"FOMC STATEMENT TEXT:\n{fomc_text.strip()}"
        if fomc_text.strip()
        else (
            "FOMC STATEMENT TEXT:\n"
            "[EMPTY — no statement available. Per system prompt: fed_tone MUST be 0.0. "
            "Do not infer or score Fed language.]"
        )
    )

    user_message = (
        f"{raw_indicator_block}\n\n"
        f"---\n\n"
        f"{fomc_section}\n\n"
        f"---\n\n"
        f"TASK: Analyze the raw macro indicators and FOMC statement above. "
        f"Follow the THINKING ORDER in your system prompt — score each dimension, "
        f"classify the regime, set confidence and regime_score, determine sector tilts, "
        f"and produce the complete qualitative briefing. "
        f"Respond with a single valid JSON object only."
    )

    logger.info(
        "_call_llm: sending request (raw_block_len=%d, fomc_text_len=%d)",
        len(raw_indicator_block),
        len(fomc_text),
    )

    # CLAUDE SWAP — OpenAI for testing
    # client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    # response = client.messages.create(
    #     model="claude-sonnet-4-6",
    #     max_tokens=3000,
    #     temperature=0.3,
    #     system=MACRO_SYSTEM_PROMPT,
    #     messages=[{"role": "user", "content": user_message}],
    # )
    # raw = response.content[0].text
    openai_client = _build_openai_client()
    response = openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        max_completion_tokens=16000,
        messages=[
            {"role": "developer", "content": MACRO_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )
    raw = response.choices[0].message.content or ""
    logger.debug("_call_llm: raw response length=%d", len(raw))

    # Parse JSON — primary attempt then regex fallback
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    parsed: Optional[dict] = None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("_call_llm: primary JSON parse failed, attempting regex fallback")
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError as exc2:
                raise MacroAgentError(
                    f"LLM response JSON parse failed (regex fallback also failed): {exc2}\n"
                    f"Raw response (first 500 chars): {raw[:500]}"
                ) from exc2
        else:
            raise MacroAgentError(
                f"LLM response contains no JSON object.\n"
                f"Raw response (first 500 chars): {raw[:500]}"
            )

    # Validate required keys
    missing_keys = _LLM_REQUIRED_KEYS - set(parsed.keys())
    if missing_keys:
        raise MacroAgentError(
            f"LLM response missing required keys: {missing_keys}. "
            f"Present keys: {set(parsed.keys())}"
        )

    # Validate and clamp dimensional scores to [-1.0, +1.0]
    for field in ("growth_score", "inflation_score", "fed_score", "stress_score"):
        try:
            parsed[field] = max(-1.0, min(1.0, float(parsed[field])))
        except (TypeError, ValueError) as exc:
            raise MacroAgentError(f"LLM returned non-numeric {field}: {parsed[field]}") from exc

    # Clamp regime_score to [0.0, 100.0]
    try:
        parsed["regime_score"] = max(0.0, min(100.0, float(parsed["regime_score"])))
    except (TypeError, ValueError) as exc:
        raise MacroAgentError(f"LLM returned non-numeric regime_score: {parsed['regime_score']}") from exc

    # Clamp regime_confidence to [0.0, 10.0]
    try:
        parsed["regime_confidence"] = max(0.0, min(10.0, float(parsed["regime_confidence"])))
    except (TypeError, ValueError) as exc:
        raise MacroAgentError(f"LLM returned non-numeric regime_confidence: {parsed['regime_confidence']}") from exc

    # Validate regime value
    if parsed.get("regime") not in _VALID_REGIMES:
        raise MacroAgentError(
            f"LLM returned invalid regime: '{parsed.get('regime')}'. "
            f"Must be one of: {_VALID_REGIMES}"
        )

    # Clamp and guard fed_tone
    try:
        fed_tone = max(-1.0, min(1.0, float(parsed["fed_tone"])))
    except (TypeError, ValueError) as exc:
        raise MacroAgentError(f"LLM returned non-numeric fed_tone: {parsed['fed_tone']}") from exc
    # Hard guard: no FOMC text → fed_tone must be 0.0 regardless of LLM output
    if not fomc_text.strip() and fed_tone != 0.0:
        logger.warning(
            "fed_tone overridden to 0.0: LLM returned %.2f but no FOMC statement was available.",
            fed_tone,
        )
        fed_tone = 0.0
    parsed["fed_tone"] = fed_tone

    # Validate sector_tilts structure
    sector_tilts = parsed.get("sector_tilts", [])
    if not isinstance(sector_tilts, list):
        raise MacroAgentError(f"LLM returned non-list sector_tilts: {type(sector_tilts)}")
    for st in sector_tilts:
        if not isinstance(st, dict):
            raise MacroAgentError(f"sector_tilts entry is not a dict: {st}")
        if st.get("tilt") not in _VALID_TILTS:
            raise MacroAgentError(
                f"Invalid sector tilt value '{st.get('tilt')}' for {st.get('sector')}. "
                f"Must be one of: {_VALID_TILTS}"
            )

    logger.info(
        "_call_llm: parsed OK | regime=%s confidence=%.1f override_flag=%s fed_tone=%.2f",
        parsed.get("regime"),
        parsed.get("regime_confidence"),
        parsed.get("override_flag"),
        parsed.get("fed_tone"),
    )
    return parsed


# ---------------------------------------------------------------------------
# Data coverage diagnostic
# ---------------------------------------------------------------------------


def _print_data_coverage(ind: RawIndicators, fomc_text: str) -> None:
    """Print a colour-coded data coverage summary to stdout after each pipeline run.

    Shows which indicators were successfully fetched (✅) vs unavailable (❌),
    with live values for available fields. Ends with a one-line summary of
    the coverage ratio and the names of any missing indicators.
    """
    CHECK = "  \u2705"
    MISS  = "  \u274c"

    def _row(label: str, value, fmt: str = "") -> str:
        if value is None:
            return f"{MISS}  {label:<26} [not available]"
        if fmt:
            return f"{CHECK}  {label:<26} {fmt.format(value)}"
        return f"{CHECK}  {label:<26} {value}"

    missing: list[str] = []

    def _check(label: str, value) -> None:
        if value is None:
            missing.append(label)

    # Build sections
    growth_rows = [
        _row("GDP YoY",          ind.gdp_yoy,          "{:.2f}%"),
        _row("ISM Mfg PMI",     None,            ""),      # no source — slot empty
        _row("ISM Svc PMI",      ind.ism_svc,          "{:.1f}"),
        _row("Payrolls MoM %",   ind.payrolls_mom_pct, "{:+.2f}%"),
        _row("Jobless Claims",   ind.jobless_claims,   "{:,.0f}"),
    ]
    for label, val in [
        ("GDP YoY", ind.gdp_yoy),
        ("ISM Svc PMI", ind.ism_svc), ("Payrolls MoM %", ind.payrolls_mom_pct),
        ("Jobless Claims", ind.jobless_claims),
    ]:
        _check(label, val)

    inflation_rows = [
        _row("CPI YoY",          ind.cpi_yoy,       "{:.2f}%"),
        _row("Core CPI YoY",     ind.core_cpi_yoy,  "{:.2f}%"),
        _row("PPI YoY",          ind.ppi_yoy,       "{:.2f}%"),
        _row("PCE YoY",          ind.pce_yoy,       "{:.2f}%"),
        _row("5Y Breakeven",     ind.breakeven_5y,  "{:.2f}%"),
    ]
    for label, val in [
        ("CPI YoY", ind.cpi_yoy), ("Core CPI YoY", ind.core_cpi_yoy),
        ("PPI YoY", ind.ppi_yoy), ("PCE YoY", ind.pce_yoy),
        ("5Y Breakeven", ind.breakeven_5y),
    ]:
        _check(label, val)

    fed_rows = [
        _row("Rate Direction",   ind.rate_direction,      "{:+.1f}"),
        _row("Yield Curve Spd",  ind.yield_curve_spread,  "{:+.0f} bps"),
    ]
    _check("Yield Curve Spd", ind.yield_curve_spread)

    stress_rows = [
        _row("VIX",              ind.vix,               "{:.1f}"),
        _row("HY Spread",        ind.hy_spread,         "{:.0f} bps"),
        _row("DXY",              ind.dxy,               "{:.1f}"),
        _row("SPX vs 200SMA",    ind.spx_pct_above_sma, "{:+.1f}%"),
    ]
    for label, val in [
        ("VIX", ind.vix), ("HY Spread", ind.hy_spread),
        ("DXY", ind.dxy), ("SPX vs 200SMA", ind.spx_pct_above_sma),
    ]:
        _check(label, val)

    fomc_available = bool(fomc_text and fomc_text.strip())
    fomc_row = (
        f"{CHECK}  {'FOMC Text':<26} {len(fomc_text):,} chars"
        if fomc_available
        else f"{MISS}  {'FOMC Text':<26} [not available]"
    )
    if not fomc_available:
        missing.append("FOMC Text")

    total_indicators = 5 + 5 + 2 + 4 + 1  # growth + inflation + fed + stress + fomc
    available = total_indicators - len(missing)

    print("\n\u2554" + "\u2550" * 54 + "\u2557")
    print("\u2551" + "       MACRO PIPELINE \u2014 DATA COVERAGE REPORT       " + "\u2551")
    print("\u255a" + "\u2550" * 54 + "\u255d")

    print("\n GROWTH")
    print("\n".join(growth_rows))

    print("\n INFLATION")
    print("\n".join(inflation_rows))

    print("\n FED / RATES")
    print("\n".join(fed_rows))

    print("\n MARKET STRESS")
    print("\n".join(stress_rows))

    print("\n OTHER")
    print(fomc_row)

    bar_filled = int(available / total_indicators * 20)
    bar = "\u2588" * bar_filled + "\u2591" * (20 - bar_filled)
    print(f"\n [{bar}] {available}/{total_indicators} indicators available")

    if missing:
        print(f"  \u26a0\ufe0f  Missing: {', '.join(missing)}")
    else:
        print("  All indicators available.")
    print()


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------


def run_macro_pipeline() -> MacroBriefing:
    """Run the full macro intelligence pipeline and return a MacroBriefing.

    Fetches all indicators, formats raw values for the LLM, calls the LLM once
    to score dimensions and classify the regime, then assembles and stores the
    MacroBriefing.

    This is the entry point called by the APScheduler cron at 7AM ET Mon–Fri
    and by the POST /macro/run API endpoint.

    If the pipeline fails completely (e.g., FRED API + LLM both down), a
    degraded Transitional briefing is returned so downstream agents can still
    operate conservatively.

    Returns
    -------
    MacroBriefing
        Fully populated MacroBriefing Pydantic model.
    """
    today = date.today()

    try:
        logger.info("=== Macro pipeline starting | date=%s ===", today)

        # ── Phase 1: Fetch ────────────────────────────────────────────────────
        logger.info("Phase 1: fetching indicators")
        fred_block = fetch_fred_block()       # returns FredBlock with Nones on partial failure
        market_block = fetch_market_block()   # returns MarketBlock with Nones on partial failure
        fomc_text = get_fed_text()            # returns "" on failure — never raises

        logger.info(
            "Phase 1 complete: fred_block has %d raw_values, fomc_text_len=%d",
            len(fred_block.raw_values),
            len(fomc_text),
        )

        # ── Phase 2: Assemble raw indicators ─────────────────────────────────
        logger.info("Phase 2: assembling raw indicators")
        raw_ind = build_raw_indicators(fred_block, market_block)
        _print_data_coverage(raw_ind, fomc_text)
        raw_indicator_block = _format_raw_indicators_for_llm(raw_ind)
        logger.info("Phase 2 complete: raw indicator block formatted")

        # ── Phase 3: Single LLM call — regime + all outputs ──────────────────
        logger.info("Phase 3: LLM regime classification")
        llm_resp = _call_llm(raw_indicator_block, fomc_text)

        final_regime     = llm_resp["regime"]
        growth_score     = float(llm_resp["growth_score"])
        inflation_score  = float(llm_resp["inflation_score"])
        fed_score        = float(llm_resp["fed_score"])
        stress_score     = float(llm_resp["stress_score"])
        regime_score     = float(llm_resp["regime_score"])
        final_confidence = float(llm_resp["regime_confidence"])
        override_flag    = bool(llm_resp["override_flag"])
        override_reason: Optional[str] = llm_resp.get("override_reason")

        logger.info(
            "Phase 3 complete: regime=%s confidence=%.1f score=%.1f override_flag=%s",
            final_regime, final_confidence, regime_score, override_flag,
        )

        # ── Phase 4: Read previous regime ────────────────────────────────────
        logger.info("Phase 4: reading previous regime from Supabase")
        previous_regime = _read_previous_regime()
        logger.info("Phase 4 complete: previous_regime=%s", previous_regime)

        regime_changed = previous_regime is not None and final_regime != previous_regime
        if regime_changed:
            logger.info("Regime changed: %s → %s", previous_regime, final_regime)
            notify_event("REGIME_CHANGED", {
                "previous_regime": previous_regime,
                "new_regime": final_regime,
                "confidence": round(final_confidence, 1),
                "regime_score": round(regime_score, 1),
            })

        # ── Phase 5: Build IndicatorScore list ────────────────────────────────
        logger.info("Phase 5: building IndicatorScore list")
        raw_indicator_dicts = build_indicator_scores(raw_ind)
        indicator_scores_list: list[IndicatorScore] = []
        for d in raw_indicator_dicts:
            try:
                indicator_scores_list.append(IndicatorScore(**d))
            except Exception as exc:
                logger.warning("Phase 5: failed to build IndicatorScore from %s — %s", d, exc)

        # ── Phase 6: Assemble MacroBriefing ───────────────────────────────────
        logger.info("Phase 6: assembling MacroBriefing")

        sector_tilts_list: list[SectorTilt] = []
        for st in llm_resp.get("sector_tilts", []):
            try:
                sector_tilts_list.append(SectorTilt(**st))
            except Exception as exc:
                logger.warning("Phase 6: failed to build SectorTilt from %s — %s", st, exc)

        upcoming_events_list: list[UpcomingEvent] = []
        for ev in llm_resp.get("upcoming_events", []):
            try:
                upcoming_events_list.append(UpcomingEvent(**ev))
            except Exception as exc:
                logger.warning("Phase 6: failed to build UpcomingEvent from %s — %s", ev, exc)

        briefing = MacroBriefing(
            date=today.isoformat(),
            regime=final_regime,
            regime_score=regime_score,
            override_flag=override_flag,
            indicator_scores=indicator_scores_list,
            qualitative_summary=llm_resp["qualitative_summary"],
            key_themes=llm_resp["key_themes"],
            portfolio_guidance=llm_resp["portfolio_guidance"],
            override_reason=override_reason,
            previous_regime=previous_regime,
            regime_changed=regime_changed,
            growth_score=growth_score,
            inflation_score=inflation_score,
            fed_score=fed_score,
            stress_score=stress_score,
            regime_confidence=final_confidence,
            sector_tilts=sector_tilts_list if sector_tilts_list else None,
            upcoming_events=upcoming_events_list if upcoming_events_list else None,
        )

        # ── Phase 7: Store ────────────────────────────────────────────────────
        logger.info("Phase 7: storing briefing to Supabase")
        row_id = _store_briefing(briefing)
        logger.info(
            "=== Macro pipeline complete | regime=%s confidence=%.1f row_id=%s ===",
            briefing.regime,
            briefing.regime_confidence,
            row_id,
        )
        notify_event("MACRO_BRIEFING_COMPLETE", {
            "regime": briefing.regime,
            "confidence": round(briefing.regime_confidence, 1),
            "portfolio_guidance": briefing.portfolio_guidance,
        })
        return briefing

    except Exception as exc:
        logger.exception("Macro pipeline failed — returning degraded briefing: %s", exc)
        return MacroBriefing(
            date=date.today().isoformat(),
            regime="Transitional",
            regime_score=50.0,
            override_flag=True,
            indicator_scores=[],
            qualitative_summary=(
                "Macro pipeline failed — data unavailable. "
                "Defaulting to Transitional regime for safety."
            ),
            key_themes=["Pipeline error — no macro signals available"],
            portfolio_guidance=(
                "Transitional regime assumed due to data failure. "
                "Hold existing positions. Small positions (≤3% NAV) may be initiated on very "
                "high-conviction setups only. No medium or large new entries until pipeline recovers."
            ),
            override_reason=f"Pipeline error: {exc}",
        )
