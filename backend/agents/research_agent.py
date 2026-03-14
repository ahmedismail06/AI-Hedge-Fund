"""
Research Agent
Orchestrates the 3 fetchers → builds LLM prompt → calls GPT-4.1 → returns structured memo.

NOTE: Claude API integration is commented out below (search "CLAUDE SWAP").
      When free Azure credits run out, swap to Claude by:
      1. pip install anthropic
      2. Add ANTHROPIC_API_KEY to .env
      3. Uncomment the Claude block, comment out the Azure block
"""

import json
import os
import re
from datetime import date
from dotenv import load_dotenv
from openai import AzureOpenAI
from pydantic import ValidationError

from backend.fetchers.sec_fetcher import fetch_sec_filings
from backend.fetchers.news_fetcher import fetch_news
from backend.fetchers.transcript_fetcher import fetch_transcripts
from backend.models import InvestmentMemo

load_dotenv()


class ResearchAgentError(Exception):
    pass


# ── LLM Client ───────────────────────────────────────────────────────────────

# ACTIVE: Azure OpenAI (GPT-4.1)
def _build_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    )

# ── CLAUDE SWAP ───────────────────────────────────────────────────────────────
# When Azure credits run out, comment out the Azure block above and
# uncomment this block. Also swap the API call in run_research() below.
#
# import anthropic
#
# def _build_client() -> anthropic.Anthropic:
#     return anthropic.Anthropic(
#         api_key=os.getenv("ANTHROPIC_API_KEY")
#     )
# ─────────────────────────────────────────────────────────────────────────────


# ── Formatters ───────────────────────────────────────────────────────────────

def _format_news(news_data: dict) -> str:
    if news_data.get("error"):
        return f"[News unavailable: {news_data['error']}]"
    articles = news_data.get("articles", [])
    if not articles:
        return "[No recent news found]"
    lines = []
    for a in articles[:20]:
        pub = a.get("published_utc", "")[:10]
        lines.append(f"- [{pub}] {a['headline']}")
        if a.get("description"):
            lines.append(f"  {a['description'][:200]}")
    return "\n".join(lines)


def _format_sec(sec_data: dict) -> str:
    if sec_data.get("error"):
        return f"[SEC filings unavailable: {sec_data['error']}]"
    parts = []
    for form_type in ("10-K", "10-Q"):
        text = sec_data.get(form_type, "[Not available]")
        parts.append(f"--- {form_type} ---\n{text}")
    return "\n\n".join(parts)


def _format_transcripts(transcript_data: dict) -> str:
    transcripts = transcript_data.get("transcripts", {})
    if not transcripts:
        warning = transcript_data.get("warning", "No transcripts available")
        return f"[Transcripts unavailable: {warning}]"
    parts = []
    for key, t in transcripts.items():
        header = f"--- Earnings Call {key} ---"
        text = t.get("text", "[No text]")
        parts.append(f"{header}\n{text[:8000]}")
    return "\n\n".join(parts)


def _build_user_message(ticker: str, sec: dict, news: dict, transcripts: dict) -> str:
    return f"""Analyze {ticker.upper()} and produce a structured investment memo.

=== SEC FILINGS ===
{_format_sec(sec)}

=== EARNINGS CALL TRANSCRIPTS ===
{_format_transcripts(transcripts)}

=== RECENT NEWS (last 30 days) ===
{_format_news(news)}

Respond with a single valid JSON object. No markdown, no code fences, no explanatory text — pure JSON only.
Use today's date ({date.today().isoformat()}) for the "date" field.
"""


# ── System Prompts ────────────────────────────────────────────────────────────

# ACTIVE: GPT-4.1 system prompt
# Tuned for GPT-4.1's instruction-following style.
# More explicit JSON enforcement and field-level instructions than the Claude version.
def _build_system_prompt() -> str:
    today = date.today().isoformat()
    return f"""You are a senior equity research analyst at a long/short equity hedge fund
focused on US equities. You produce rigorous investment memos based entirely
on primary source documents — SEC 10-K and 10-Q filings, earnings call
transcripts, and recent news. You do not rely on general market knowledge.
You reason only from the documents provided to you.

If a document section is missing or unavailable, say so explicitly in the
relevant field rather than inferring from general knowledge.

You think in this order:
1. Balance sheet first. Assess cash position, debt load, and free cash flow
   generation. Weight this step by company size (see below).
2. Business quality second. Is the moat real or narrative? Are margins
   expanding or being competed away? Is management executing or making excuses?
3. Valuation third. Cheap is not a thesis on its own.
   Cheap + quality + catalyst is a thesis.
4. Catalysts and timing. What specific event or inflection makes this
   the right time to own or short this name?

Adapt your framework by company size:

- Large/mega-cap ($10B+): Balance sheet assumed healthy. Focus on competitive
  moat durability, capital allocation quality, and whether growth is
  re-accelerating or decelerating.

- Mid-cap ($2B–$10B): Focus on whether this is a compounder in early innings
  or a melting ice cube. Assess pricing power and whether management has
  earned the benefit of the doubt.

- Micro/small-cap (under $2B): Balance sheet is the FIRST filter. Survival
  before upside. Debt load, cash runway, and FCF generation assessed before
  anything else. A compelling growth story means nothing if the company runs
  out of cash in 18 months.

You are skeptical by default. High conviction requires strong evidence across
multiple factors — not one good data point. Flag risks explicitly even when
your verdict is bullish. Do not cheerlead.

Bear thesis must contain at least 4 distinct points. Do not repeat variations
of the same risk. Each point must identify a different failure mode —
e.g. balance sheet, competitive position, management execution, valuation,
regulatory, or market structure risks.

Today's date is {today}. Any event dated before {today} has already occurred.
Use this when evaluating catalysts:
- Only list events that have NOT yet occurred as forward-looking catalysts.
- If an event (e.g. earnings call, investor day, product launch) is mentioned
  in the documents but its date has already passed, treat it as historical
  context — reference it in the summary or bull/bear thesis instead.
- Do not list past events in the "catalysts" field.

CRITICAL FORMATTING RULES:
- Respond with a single valid JSON object ONLY
- No markdown, no code fences, no text before or after the JSON
- Every field listed in the schema is REQUIRED — do not omit any
- For financial_health fields, use ONLY the allowed enum values
- If data is unavailable for a field, use "unknown" for string enums
  or explain briefly in the summary field

Required JSON schema (output must match exactly):
{{
  "ticker": "string — uppercase ticker symbol",
  "date": "string — YYYY-MM-DD",
  "company_overview": "string — 2-4 sentence business description from filings",
  "bull_thesis": ["point 1 grounded in filing data", "point 2", "point 3"],
  "bear_thesis": ["point 1 grounded in filing data", "point 2", "point 3", "point 4"],
  "key_risks": ["risk 1", "risk 2"],
  "catalysts": ["catalyst 1", "catalyst 2"],
  "financial_health": {{
    "revenue_trend": "growing | stable | declining | unknown",
    "margin_trend": "expanding | stable | contracting | unknown",
    "debt_level": "low | moderate | high | unknown",
    "fcf": "strong | neutral | weak | unknown"
  }},
  "macro_sensitivity": "string — how macro regime affects this name",
  "verdict": "LONG | SHORT | AVOID",
  "conviction_score": <number 0.0-10.0>,
  "suggested_position_size": "small | medium | large | skip",
  "summary": "string — 3-5 sentence investment summary citing specific data points from filings"
}}"""


# ── CLAUDE SWAP ───────────────────────────────────────────────────────────────
# Claude version of the system prompt — preserved for when you switch providers.
# Claude handles long document context more naturally and needs less explicit
# JSON enforcement than GPT-4.1. Uncomment when switching to Claude.
#
# def _build_system_prompt() -> str:
#     today = date.today().isoformat()
#     return f"""You are a senior equity research analyst at a long/short
# equity hedge fund focused on US equities. You produce rigorous investment
# memos based entirely on primary source documents — SEC 10-K and 10-Q filings,
# earnings call transcripts, and recent news. You do not rely on general market
# knowledge. You reason from the documents in front of you.
#
# You think in this order:
# 1. Balance sheet first. Assess cash position, debt load, and free cash flow
#    generation. Weight this step according to company size (see below).
# 2. Business quality second. Is the moat real or narrative? Are margins
#    expanding or being competed away? Is management executing or making excuses?
# 3. Valuation third. Cheap is not a thesis on its own.
#    Cheap + quality + catalyst is a thesis.
# 4. Catalysts and timing. What is the specific event or inflection that makes
#    this the right time to own or short this name?
#
# You adapt your analytical framework based on company size:
#
# - Large/mega-cap ($10B+): Balance sheet is assumed healthy — not the primary
#   filter. Focus on competitive moat durability, capital allocation quality,
#   and whether growth is re-accelerating or decelerating. Valuation discipline
#   matters more here because the market covers these names thoroughly.
#
# - Mid-cap ($2B–$10B): Balance sheet matters but is not the first question.
#   Focus on whether this is a compounder in early innings or a melting ice cube.
#   Assess whether the business has pricing power and whether management has
#   earned the benefit of the doubt.
#
# - Micro/small-cap (under $2B): Balance sheet is the first filter. Survival
#   before upside. Debt load, cash runway, and FCF generation are assessed
#   before anything else. Small-caps go bankrupt. Large-caps don't. A compelling
#   growth story means nothing if the company runs out of cash in 18 months.
#
# You are skeptical by default. A high conviction score requires strong evidence
# across multiple factors — not one good data point. You flag risks explicitly
# even when your verdict is bullish. You do not cheerflead.
#
# Bear thesis must contain at least 4 distinct points. Do not repeat variations
# of the same risk. Each point must identify a different failure mode —
# e.g. balance sheet, competitive position, management execution, valuation,
# regulatory, or market structure risks.
#
# Today's date is {today}. Use this when evaluating catalysts:
# - Only list events that have NOT yet occurred as forward-looking catalysts.
# - If an event (e.g. earnings call, investor day, product launch) is mentioned
#   in the documents but its date has already passed, treat it as historical
#   context — reference it in the summary or bull/bear thesis instead.
# - Do not list past events in the "catalysts" field.
#
# Your verdict feeds a portfolio construction agent and a human approval gate.
# You are the analyst. You do not size positions, manage exposure, or approve
# trades. Your job ends at the memo.
#
# You MUST respond with a single valid JSON object only. No markdown, no code
# fences, no commentary before or after.
#
# The JSON must match this exact schema:
# {
#   "ticker": "string — uppercase ticker symbol",
#   "date": "string — YYYY-MM-DD",
#   "company_overview": "string — 2-4 sentence business description",
#   "bull_thesis": ["point 1", "point 2", "point 3"],
#   "bear_thesis": ["point 1", "point 2", "point 3", "point 4"],
#   "key_risks": ["risk 1", "risk 2"],
#   "catalysts": ["catalyst 1", "catalyst 2"],
#   "financial_health": {
#     "revenue_trend": "growing | stable | declining",
#     "margin_trend": "expanding | stable | contracting",
#     "debt_level": "low | moderate | high",
#     "fcf": "strong | neutral | weak"
#   },
#   "macro_sensitivity": "string — how macro regime affects this name",
#   "verdict": "LONG | SHORT | AVOID",
#   "conviction_score": <number 0.0-10.0>,
#   "suggested_position_size": "small | medium | large | skip",
#   "summary": "string — 3-5 sentence investment summary"
# }}"""
# ─────────────────────────────────────────────────────────────────────────────


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_code_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
    text = re.sub(r"\s*```$", "", text.strip())
    return text.strip()


def _validate_memo(memo: dict) -> InvestmentMemo:
    """Validate memo dict via Pydantic. Raises ResearchAgentError on any schema violation."""
    try:
        return InvestmentMemo(**memo)
    except ValidationError as exc:
        raise ResearchAgentError(f"LLM response failed schema validation:\n{exc}")


# ── Main Entry Point ──────────────────────────────────────────────────────────

def run_research(ticker: str) -> dict:
    """
    Full pipeline: fetch data → build prompt → call GPT-4.1 → validate → return memo dict.
    Attaches raw fetcher outputs as '_raw_docs' key (stripped before DB insert in vector_store).
    Raises ResearchAgentError on LLM parse / validation failure.
    """
    ticker = ticker.upper().strip()

    sec_data = fetch_sec_filings(ticker)
    news_data = fetch_news(ticker)
    transcript_data = fetch_transcripts(ticker)

    client = _build_client()
    user_message = _build_user_message(ticker, sec_data, news_data, transcript_data)

    # ACTIVE: Azure OpenAI call
    response = client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
        messages=[
            {"role": "system", "content": _build_system_prompt()},
            {"role": "user", "content": user_message},
        ],
        temperature=0.3,
        max_tokens=4000,
    )
    raw_content = response.choices[0].message.content or ""

    # ── CLAUDE SWAP ───────────────────────────────────────────────────────────
    # Replace the Azure call above with this block when switching to Claude.
    # Note: Claude uses system= as a top-level param, not inside messages[].
    #
    # response = client.messages.create(
    #     model="claude-sonnet-4-5",
    #     max_tokens=4000,
    #     temperature=0.3,
    #     system=_build_system_prompt(),
    #     messages=[
    #         {"role": "user", "content": user_message},
    #     ],
    # )
    # raw_content = response.content[0].text or ""
    # ─────────────────────────────────────────────────────────────────────────

    cleaned = _strip_code_fences(raw_content)

    # Robust JSON extraction — handles rare cases where GPT-4.1 prepends a sentence
    try:
        memo = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if not match:
            raise ResearchAgentError(
                f"LLM returned invalid JSON.\n\nRaw response:\n{raw_content[:500]}"
            )
        try:
            memo = json.loads(match.group())
        except json.JSONDecodeError as exc:
            raise ResearchAgentError(
                f"LLM returned invalid JSON: {exc}\n\nRaw response:\n{raw_content[:500]}"
            )

    memo["ticker"] = ticker
    validated = _validate_memo(memo)

    result = validated.model_dump(exclude_none=True)
    result["_raw_docs"] = {
        "sec": sec_data,
        "news": news_data,
        "transcripts": transcript_data,
    }

    return result