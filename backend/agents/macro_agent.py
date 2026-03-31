"""
Macro Agent — daily regime intelligence pipeline.

Orchestrates the full macro data fetch → quantitative scoring → Claude qualitative
overlay → Supabase write cycle. Called by the APScheduler cron at 7AM ET Mon–Fri.

Pipeline phases:
  1. Fetch:    FRED indicators, market stress data, FOMC text
  2. Score:    Quantitative regime classification with fed_tone=0.0 default
  3. Claude:   Qualitative overlay — fed tone scoring, override decision, summary
  4. Re-score: Rerun scorer with the actual fed_tone from Claude
  5. Regime:   Apply LLM override if warranted; check regime change vs yesterday
  6. Build:    Assemble MacroBriefing Pydantic model
  7. Store:    Upsert to Supabase macro_briefings table
"""

from dotenv import load_dotenv

load_dotenv()

import json
import logging
import os
import re
from datetime import date
from typing import Optional

# CLAUDE SWAP — switched to Azure OpenAI for testing
# import anthropic
from openai import AzureOpenAI

from backend.macro.indicators.fred_fetcher import fetch_fred_block, FredFetchError
from backend.macro.indicators.market_fetcher import fetch_market_block
from backend.macro.indicators.fed_scraper import get_fed_text
from backend.macro.scorer import (
    RawIndicators,
    DimensionalScores,
    build_raw_indicators,
    score_indicators,
    build_indicator_scores,
)
from backend.models.macro_briefing import MacroBriefing, IndicatorScore
from backend.memory.vector_store import _get_client

logger = logging.getLogger(__name__)

# CLAUDE SWAP — Azure OpenAI for testing
def _build_azure_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    )

AZURE_DEPLOY = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4.1")


class MacroAgentError(Exception):
    pass


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

MACRO_SYSTEM_PROMPT = """You are a senior macro strategist at a long/short hedge fund running a $50M–$2B US micro/small-cap
equity book (SaaS, Healthcare, Industrials). You operate at 7AM ET daily. Your sole function is to
synthesize quantitative macro indicator data and Federal Reserve qualitative language into a
regime verdict and actionable portfolio briefing.

You are NOT a market commentator. You are NOT a news summarizer. You are a regime classifier and
portfolio-level signal generator. A strategist who writes "markets remain uncertain" or "investors
should watch inflation" has failed. Every output sentence must carry a specific implication for
positioning, exposure, or sector tilt.

What failure looks like in this role: producing a qualitative_summary that restates the numbers
already in the indicator table, writing key_themes that could have been written any week in the
last 18 months, or setting override_flag to true without a concrete contradiction between the FOMC
text and the quantitative regime score.

---

THINKING ORDER — complete these steps in sequence before producing any output:

1. FOMC TEXT ASSESSMENT (do this first, before looking at the quantitative regime):
   Read the FOMC statement text provided. Identify the DOMINANT TONE signal using the fed_tone
   scoring rule below. If the text is empty or absent, fed_tone = 0.0 and note "FOMC text
   unavailable." Do not infer Fed language from general knowledge. Only score what is in the text.

2. REGIME CONTRADICTION CHECK (override decision gate):
   State the quantitative regime and its confidence score from the indicator table. Then ask:
   "Does the FOMC language clearly contradict the quantitative regime?" A contradiction exists
   only if the FOMC text contains explicit forward guidance language (rate path language, specific
   threshold commitments, or explicit pivot signals) that would logically produce a DIFFERENT
   regime than the quantitative score. Minor hedging language in an otherwise consistent FOMC
   statement is NOT a contradiction. If no clear contradiction exists, set override_flag = false.

3. REGIME VERDICT:
   If override_flag = true, write the new regime in the "regime" field and explain the specific
   FOMC language that drove the change. If override_flag = false, the "regime" field must be
   omitted from the JSON — the caller will use the quantitative regime.

4. FED TONE CALIBRATION:
   Score fed_tone as a float in [-1.0, +1.0]. Use this mechanical rule — evaluate each criterion
   as present or absent in the actual FOMC text:
   Score starts at 0.0.
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

5. QUALITATIVE SYNTHESIS:
   Write qualitative_summary (3-5 sentences, hard limit) that synthesizes the macro picture
   ACROSS all four dimensions (growth, inflation, Fed stance, market stress) into an integrated
   assessment. Do not write one sentence per dimension. Write sentences that connect dimensions
   causally. The final sentence must name the single largest forward risk or tailwind.

6. KEY THEMES:
   Write 2-4 theme strings. Each must be a forward-looking statement written in institutional
   research note style.

7. PORTFOLIO GUIDANCE:
   Write 2-3 sentences directly naming the regime and its implications for: (a) gross exposure
   level, (b) sector preference within the universe (SaaS, Healthcare, Industrials), and
   (c) stop-loss posture.

---

FED_TONE SCORING — REFERENCE CALIBRATION:

Maximally dovish (fed_tone = +1.0): FOMC explicitly signals imminent rate cuts, acknowledges
inflation is at target, and references "appropriate to begin reducing" policy rate.

Maximally hawkish (fed_tone = -1.0): FOMC signals additional rate increases are warranted,
inflation is significantly above target, and economic strength permits continued tightening.

Neutral (fed_tone = 0.0): FOMC text unavailable, or balanced statement with no explicit
forward-guidance signals in either direction.

---

REGIME PORTFOLIO IMPLICATIONS:

Risk-On:
  Gross exposure: up to 150% gross (Phase 1: full long allocation)
  Sector tilt: favor SaaS (revenue visibility) and high-growth Industrials (book-to-bill > 1.0)
  Stop posture: standard Tier 1 at -8% position, Tier 2 at -15% strategy

Risk-Off:
  Gross exposure: reduce to 80% gross maximum; no new longs above medium conviction
  Sector tilt: rotate toward asset-light Healthcare and Industrials with backlog coverage > 12 months
  Stop posture: tighten to Risk-Off stops: Tier 1 at -5%, Tier 2 at -10%

Stagflation:
  Gross exposure: reduce to 60-70% gross; hold only highest-conviction positions
  Sector tilt: avoid high-multiple SaaS; favor Industrials with pricing power; Healthcare with CMS reimbursement certainty
  Stop posture: Risk-Off stops apply

Transitional:
  Gross exposure: hold current book at reduced size; no new large positions
  Sector tilt: neutral across all three sectors
  Stop posture: standard stops; wait for regime clarity before sizing up

---

OVERRIDE DECISION MATRIX:
  Condition A: FOMC text empty → override_flag = false; fed_tone = 0.0; no "regime" field
  Condition B: FOMC text present but consistent → override_flag = false; no "regime" field
  Condition C: FOMC text contradicts quant regime → override_flag = true; "regime" field required

---

MISSING DATA HANDLING:
  If FOMC text is empty: fed_tone = 0.0; override_flag = false; override_reason = null.
  If regime_confidence < 4.0/10: qualitative_summary must acknowledge signal conflict.

---

CRITICAL FORMATTING RULES:
  - Respond with a single valid JSON object ONLY
  - No markdown, no code fences, no text before or after the JSON
  - Do not include the "regime" field unless override_flag is true
  - fed_tone must be a float (number), not a string

Required JSON schema:
{
  "fed_tone": <float in [-1.0, +1.0]>,
  "fed_tone_rationale": "derivation string",
  "override_flag": <boolean>,
  "override_reason": "string or null",
  "qualitative_summary": "3-5 sentences",
  "key_themes": ["theme1", "theme2"],
  "portfolio_guidance": "2-3 sentences naming regime, exposure ceiling, sector tilt"
}
HARD RULE: Single valid JSON object ONLY. The "regime" field is conditional — include only when override_flag is true."""


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
            return str(result.data[0]["id"])
        return "stored"
    except Exception as exc:
        logger.warning("_store_briefing: Supabase upsert failed — %s", exc)
        return "storage_failed"


# ---------------------------------------------------------------------------
# Indicator summary formatter
# ---------------------------------------------------------------------------

# Category membership for formatting — keyed to names returned by build_indicator_scores
_GROWTH_NAMES = {"GDP YoY", "ISM Mfg PMI (Philly proxy)", "ISM Svc PMI", "Jobless Claims", "Payrolls MoM"}
_INFLATION_NAMES = {"CPI YoY", "Core CPI YoY", "PPI YoY", "PCE YoY", "5Y Breakeven"}
_FED_NAMES = {"Yield Curve Spread"}
_STRESS_NAMES = {"VIX", "HY Spread", "DXY"}


def _format_indicator_summary(ind: RawIndicators, scores: DimensionalScores) -> str:
    """Build a structured macro indicator table string for the Claude user message.

    Organises per-indicator dicts from build_indicator_scores() into four
    labelled sections (Growth, Inflation, Fed/Rates, Market Stress) followed
    by the quantitative regime verdict. Only indicators with non-None values
    are included (build_indicator_scores already filters None values).

    Parameters
    ----------
    ind:
        Assembled RawIndicators snapshot.
    scores:
        DimensionalScores from score_indicators().

    Returns
    -------
    str
        Multi-line formatted indicator table ready for injection into the
        Claude user message.
    """
    today_str = date.today().isoformat()
    all_indicators = build_indicator_scores(ind)

    def _categorise(ind_list: list[dict]) -> dict[str, list[dict]]:
        buckets: dict[str, list[dict]] = {
            "GROWTH": [],
            "INFLATION": [],
            "FED_RATES": [],
            "STRESS": [],
        }
        for item in ind_list:
            name = item["name"]
            if name in _GROWTH_NAMES:
                buckets["GROWTH"].append(item)
            elif name in _INFLATION_NAMES:
                buckets["INFLATION"].append(item)
            elif name in _FED_NAMES:
                buckets["FED_RATES"].append(item)
            elif name in _STRESS_NAMES:
                buckets["STRESS"].append(item)
            else:
                # Any unrecognised indicator defaults to STRESS bucket
                buckets["STRESS"].append(item)
        return buckets

    buckets = _categorise(all_indicators)

    def _fmt_row(item: dict) -> str:
        name = item["name"]
        value = item["value"]
        signal = item["signal"].upper()
        # Format value: integers for large numbers, 2 decimal places otherwise
        if abs(value) >= 1000:
            val_str = f"{value:,.0f}"
        elif name in ("Yield Curve Spread", "HY Spread"):
            val_str = f"{value:+.0f} bps"
        elif name in ("CPI YoY", "Core CPI YoY", "PPI YoY", "PCE YoY",
                      "5Y Breakeven", "GDP YoY"):
            val_str = f"{value:.1f}%"
        else:
            val_str = f"{value:.1f}"
        return f"  {name:<20} {val_str:<14} [{signal}]"

    def _section(title: str, score: float, rows: list[dict]) -> str:
        header = f"\n{title} (score: {score:+.2f})"
        if not rows:
            return header + "\n  (no data available)"
        return header + "\n" + "\n".join(_fmt_row(r) for r in rows)

    # Rate direction is a synthetic indicator — add it explicitly to FED_RATES
    rate_direction_row: dict = {
        "name": "Rate Direction",
        "value": ind.rate_direction,
        "signal": (
            "bullish" if ind.rate_direction > 0
            else ("bearish" if ind.rate_direction < 0 else "neutral")
        ),
        "note": (
            "Fed easing" if ind.rate_direction > 0
            else ("Fed tightening" if ind.rate_direction < 0 else "Fed on hold")
        ),
    }
    fed_rows = [rate_direction_row] + buckets["FED_RATES"]

    # SPX vs 200-day SMA — add explicitly if available
    if ind.spx_pct_above_sma is not None:
        pct = ind.spx_pct_above_sma
        if pct < -2.0:
            spx_signal = "bearish"
        elif pct <= 2.0:
            spx_signal = "neutral"
        else:
            spx_signal = "bullish"
        spx_row: dict = {
            "name": "SPX vs 200SMA",
            "value": pct,
            "signal": spx_signal,
            "note": f"{pct:+.1f}% vs 200-day SMA",
        }
        stress_rows = buckets["STRESS"] + [spx_row]
    else:
        stress_rows = buckets["STRESS"]

    lines = [
        f"MACRO INDICATOR SUMMARY — {today_str}",
        "=====================================",
    ]
    lines.append(_section("GROWTH", scores.growth_score, buckets["GROWTH"]))
    lines.append(_section("INFLATION", scores.inflation_score, buckets["INFLATION"]))
    lines.append(_section("FED / RATES", scores.fed_score, fed_rows))
    lines.append(_section("MARKET STRESS", scores.stress_score, stress_rows))
    lines.append(
        f"\nQUANTITATIVE REGIME: {scores.regime} "
        f"(score: {scores.regime_score:.1f}/100, confidence: {scores.regime_confidence:.1f}/10)"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claude call
# ---------------------------------------------------------------------------


def _call_llm(
    indicator_summary: str,
    fomc_text: str,
    quant_regime: str,
    scores: DimensionalScores,
) -> dict:
    """Call Claude to produce qualitative macro overlay.

    Combines the structured indicator table with the FOMC statement text into
    a single user message, then parses the JSON response.

    Parameters
    ----------
    indicator_summary:
        Formatted indicator table string from _format_indicator_summary().
    fomc_text:
        Raw FOMC statement text, or "" if unavailable.
    quant_regime:
        Quantitative regime classification string — included in user message
        as context for the override decision gate.
    scores:
        DimensionalScores — used to surface regime_confidence to Claude.

    Returns
    -------
    dict
        Parsed JSON with at minimum: fed_tone, override_flag,
        qualitative_summary, key_themes, portfolio_guidance.

    Raises
    ------
    MacroAgentError
        If JSON parsing fails or required keys are missing from the response.
    """
    fomc_section = (
        f"FOMC STATEMENT TEXT:\n{fomc_text.strip()}"
        if fomc_text.strip()
        else "FOMC STATEMENT TEXT:\n[Not available — no recent statement scraped]"
    )

    user_message = (
        f"{indicator_summary}\n\n"
        f"---\n\n"
        f"{fomc_section}\n\n"
        f"---\n\n"
        f"TASK: Analyze the above data. The quantitative model has classified the regime as "
        f'"{quant_regime}" with confidence {scores.regime_confidence:.1f}/10. '
        f"Follow the THINKING ORDER in your system prompt. "
        f"Respond with a single valid JSON object only."
    )

    logger.info(
        "_call_llm: sending request (indicator_summary_len=%d, fomc_text_len=%d)",
        len(indicator_summary),
        len(fomc_text),
    )

    # CLAUDE SWAP — Azure OpenAI for testing
    # client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    # response = client.messages.create(
    #     model="claude-sonnet-4-6",
    #     max_tokens=1500,
    #     temperature=0.3,
    #     system=MACRO_SYSTEM_PROMPT,
    #     messages=[{"role": "user", "content": user_message}],
    # )
    # raw = response.content[0].text
    azure_client = _build_azure_client()
    response = azure_client.chat.completions.create(
        model=AZURE_DEPLOY,
        max_tokens=1500,
        temperature=0.3,
        messages=[
            {"role": "system", "content": MACRO_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )
    raw = response.choices[0].message.content or ""
    logger.debug("_call_llm: raw response length=%d", len(raw))

    # Parse JSON — primary attempt then regex fallback
    cleaned = raw.strip()
    # Strip markdown code fences if present
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
                    f"Claude response JSON parse failed (regex fallback also failed): {exc2}\n"
                    f"Raw response (first 500 chars): {raw[:500]}"
                ) from exc2
        else:
            raise MacroAgentError(
                f"Claude response contains no JSON object.\n"
                f"Raw response (first 500 chars): {raw[:500]}"
            )

    # Validate required keys
    required = {
        "fed_tone",
        "override_flag",
        "qualitative_summary",
        "key_themes",
        "portfolio_guidance",
    }
    missing = required - set(parsed.keys())
    if missing:
        raise MacroAgentError(
            f"Claude response missing required keys: {missing}. "
            f"Present keys: {set(parsed.keys())}"
        )

    # If override_flag is True, "regime" must be present
    if bool(parsed.get("override_flag", False)) and "regime" not in parsed:
        raise MacroAgentError(
            "override_flag is True but 'regime' key is missing from Claude response. "
            "System prompt violation — override requires a regime specification."
        )

    logger.info(
        "_call_llm: parsed OK | override_flag=%s fed_tone=%s",
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
        _row("ISM Mfg PMI (Philly proxy)", ind.ism_mfg,  "{:.1f}"),
        _row("ISM Svc PMI",      ind.ism_svc,          "{:.1f}"),
        _row("Payrolls MoM %",   ind.payrolls_mom_pct, "{:+.2f}%"),
        _row("Jobless Claims",   ind.jobless_claims,   "{:,.0f}"),
    ]
    for label, val in [
        ("GDP YoY", ind.gdp_yoy), ("ISM Mfg PMI (Philly proxy)", ind.ism_mfg),
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

    Fetches all indicators, scores them quantitatively, calls Claude for
    qualitative overlay, re-scores with the actual fed_tone, determines the
    final regime (with optional LLM override), assembles and stores the
    MacroBriefing, then returns it.

    This is the entry point called by the APScheduler cron at 7AM ET Mon–Fri
    and by the POST /macro/run API endpoint.

    If the pipeline fails completely (e.g., FRED API + Claude both down), a
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

        # ── Phase 2: Score (with fed_tone=0.0 default) ───────────────────────
        logger.info("Phase 2: quantitative scoring (fed_tone=0.0 placeholder)")
        raw_ind = build_raw_indicators(fred_block, market_block)
        _print_data_coverage(raw_ind, fomc_text)
        phase2_scores = score_indicators(raw_ind, fed_tone=0.0)

        logger.info(
            "Phase 2 complete: regime=%s score=%.1f confidence=%.1f",
            phase2_scores.regime,
            phase2_scores.regime_score,
            phase2_scores.regime_confidence,
        )

        # ── Phase 3: Call Claude ──────────────────────────────────────────────
        logger.info("Phase 3: Claude qualitative overlay")
        indicator_summary = _format_indicator_summary(raw_ind, phase2_scores)
        claude_resp = _call_llm(
            indicator_summary,
            fomc_text,
            phase2_scores.regime,
            phase2_scores,
        )

        logger.info(
            "Phase 3 complete: fed_tone=%.2f override_flag=%s",
            float(claude_resp.get("fed_tone", 0.0)),
            claude_resp.get("override_flag", False),
        )

        # ── Phase 4: Re-score with actual fed_tone ────────────────────────────
        logger.info("Phase 4: re-scoring with actual fed_tone")
        fed_tone = float(claude_resp.get("fed_tone", 0.0))
        fed_tone = max(-1.0, min(1.0, fed_tone))   # clamp to [-1.0, +1.0]
        final_scores = score_indicators(raw_ind, fed_tone=fed_tone)

        logger.info(
            "Phase 4 complete: final regime=%s score=%.1f confidence=%.1f",
            final_scores.regime,
            final_scores.regime_score,
            final_scores.regime_confidence,
        )

        # ── Phase 5: Read previous regime ────────────────────────────────────
        logger.info("Phase 5: reading previous regime from Supabase")
        previous_regime = _read_previous_regime()
        logger.info("Phase 5 complete: previous_regime=%s", previous_regime)

        # ── Phase 6: Determine final regime (override check) ─────────────────
        logger.info("Phase 6: override check")
        override_flag = bool(claude_resp.get("override_flag", False))
        override_reason: Optional[str] = claude_resp.get("override_reason")

        if override_flag and "regime" in claude_resp:
            final_regime = claude_resp["regime"]
            logger.info(
                "Phase 6: LLM override applied — regime changed from %s to %s",
                final_scores.regime,
                final_regime,
            )
        else:
            final_regime = final_scores.regime
            logger.info(
                "Phase 6: no override — using quantitative regime=%s", final_regime
            )

        regime_changed = (
            previous_regime is not None and final_regime != previous_regime
        )
        if regime_changed:
            logger.info("Regime changed: %s -> %s", previous_regime, final_regime)

        # ── Phase 7: Build IndicatorScore list ────────────────────────────────
        logger.info("Phase 7: building IndicatorScore list")
        raw_indicator_dicts = build_indicator_scores(raw_ind)
        indicator_scores_list: list[IndicatorScore] = []
        for d in raw_indicator_dicts:
            try:
                indicator_scores_list.append(IndicatorScore(**d))
            except Exception as exc:
                logger.warning(
                    "Phase 7: failed to build IndicatorScore from %s — %s", d, exc
                )

        # ── Phase 8: Assemble MacroBriefing ───────────────────────────────────
        logger.info("Phase 8: assembling MacroBriefing")
        briefing = MacroBriefing(
            date=today.isoformat(),
            regime=final_regime,
            regime_score=final_scores.regime_score,
            override_flag=override_flag,
            indicator_scores=indicator_scores_list,
            qualitative_summary=claude_resp["qualitative_summary"],
            key_themes=claude_resp["key_themes"],
            portfolio_guidance=claude_resp["portfolio_guidance"],
            override_reason=override_reason,
            previous_regime=previous_regime,
            regime_changed=regime_changed,
            growth_score=final_scores.growth_score,
            inflation_score=final_scores.inflation_score,
            fed_score=final_scores.fed_score,
            stress_score=final_scores.stress_score,
            regime_confidence=final_scores.regime_confidence,
        )

        # ── Phase 9: Store ────────────────────────────────────────────────────
        logger.info("Phase 9: storing briefing to Supabase")
        row_id = _store_briefing(briefing)
        logger.info(
            "=== Macro pipeline complete | regime=%s confidence=%.1f row_id=%s ===",
            briefing.regime,
            briefing.regime_confidence,
            row_id,
        )
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
                "Hold existing positions, no new entries until pipeline recovers."
            ),
            override_reason=f"Pipeline error: {exc}",
        )
