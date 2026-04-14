"""
Research Agent — Hybrid B+D agentic retrieval pipeline.

Phase 0: Fetch all 5 data sources.
Phase 1: Index unstructured narrative into pgvector (SEC + transcripts).
Phase 2: Build structured block (metrics, insider buying, market intel, news).
Phase 3: ReAct agentic retrieval loop — Claude iteratively queries pgvector.
Phase 4: Synthesis call — Claude writes the full memo using structured + retrieved data.
Phase 5: Red team — adversarial second call to harden the bull thesis.

Fallback: If Phase 1 indexing fails (missing deps, pgvector not ready) → retrieved_chunks=[]
          → synthesis proceeds with structured block only (same quality as v1).

OpenAI is used for the ReAct retrieval loop (Phase 3) only.
Synthesis (Phase 4) and red team (Phase 5) run on Claude (claude-sonnet-4-6).
"""

import anthropic
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Optional
from dotenv import load_dotenv
from openai import OpenAI  # ReAct loop only
from pydantic import ValidationError

from backend.fetchers.sec_fetcher import fetch_sec_filings
from backend.fetchers.news_fetcher import fetch_news
from backend.fetchers.transcript_fetcher import fetch_transcripts
from backend.fetchers.form4_fetcher import fetch_form4
from backend.fetchers.fmp_fetcher import fetch_fmp
from backend.memory.vector_store import search_similar
from backend.models import InvestmentMemo

load_dotenv()

logger = logging.getLogger(__name__)


class ResearchAgentError(Exception):
    pass


# ── LLM Clients ──────────────────────────────────────────────────────────────

# OpenAI — ReAct retrieval loop only (Phase 3)
def _build_openai_client() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

# Claude API — synthesis (Phase 4) + red team (Phase 5)
def _build_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── ReAct Tool Schema ─────────────────────────────────────────────────────────

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_documents",
        "description": (
            "Search SEC filings (10-K, 10-Q) and earnings call transcripts for this company. "
            "Use specific, targeted queries to find the exact evidence you need for the memo. "
            "Returns ranked text chunks with source labels and similarity scores."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Specific search query — be precise, not generic",
                },
                "doc_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["10-K", "10-Q", "transcript"]},
                    "description": "Filter to specific document types. Omit to search all.",
                },
                "n": {
                    "type": "integer",
                    "description": "Number of chunks to return (default 4, max 8)",
                    "default": 4,
                },
            },
            "required": ["query"],
        },
    },
}

MAX_TURNS = 10  # hard cap on ReAct loop iterations


# ── Formatters ───────────────────────────────────────────────────────────────

def _format_news(news_data: dict) -> str:
    """Format recent news articles into a plain-text block for the LLM prompt."""
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



def _format_financial_metrics(metrics_10k: dict, metrics_10q: dict) -> str:
    """Render pre-extracted financial metrics block for the LLM prompt."""
    if not metrics_10k and not metrics_10q:
        return "[Pre-extraction unavailable — not in source documents]"

    # Prefer 10-Q values for income statement items (more recent); 10-K for annual context
    m = metrics_10q if metrics_10q else metrics_10k
    m_annual = metrics_10k if metrics_10k else {}

    def _val(key: str, source: dict = m) -> str:
        v = source.get(key)
        return str(v) if v is not None else "unavailable"

    atm = m.get("atm_or_shelf") or m_annual.get("atm_or_shelf", False)
    maturities = m.get("debt_maturities") or m_annual.get("debt_maturities")
    reporting_unit = m.get("reporting_unit") or m_annual.get("reporting_unit")

    if reporting_unit:
        unit_label = f"[REPORTING UNIT: values are in {reporting_unit.upper()} (detected from filing header)]"
    else:
        unit_label = "[REPORTING UNIT: not detected in filing — assume THOUSANDS per SEC convention]"

    lines = [
        "=== PRE-EXTRACTED FINANCIAL METRICS ===",
        "[Programmatically extracted — verify against filing text if values seem off]",
        unit_label,
        "",
        f"Revenue (recent):    {_val('revenue_recent')}",
        f"Revenue (prior):     {_val('revenue_prior')}",
        f"Gross margin:        {_val('gross_margin')}",
        f"Operating income:    {_val('operating_income')}",
        f"Net income/loss:     {_val('net_income')}",
        f"Cash:                {_val('cash')}",
        f"Long-term debt:      {_val('long_term_debt')}",
        f"Accounts payable:    {_val('accounts_payable')}",
        f"Capital raise risk:  {'ATM program on file' if atm else 'None found'}",
        f"Debt maturities:     {maturities if maturities else 'None found'}",
    ]
    return "\n".join(lines)


def _format_insider_buying(form4_data: dict) -> str:
    """Render Form 4 insider buying block."""
    if form4_data.get("error"):
        return f"[Form 4 unavailable: {form4_data['error']}]"

    ceo = form4_data.get("ceo_purchase")
    cfo = form4_data.get("cfo_purchase")
    applies = form4_data.get("conviction_rubric_applies", False)

    lines = ["=== INSIDER BUYING (SEC Form 4, last 90 days) ==="]
    def _fmt_purchase(role: str, p: dict | None) -> str:
        if not p:
            return f"{role}: No qualifying open-market purchase found"
        val = p.get("value", 0)
        val_str = f"~${val / 1_000:.0f}K" if val < 1_000_000 else f"~${val / 1_000_000:.2f}M"
        threshold_note = "" if val >= 25_000 else " [BELOW $25K threshold — rubric does not apply]"
        return (
            f"{role} [{p['name']}]: Purchased {p['shares']:,} shares "
            f"at ${p['price']:.2f} on {p['date']} (open market) — value {val_str}{threshold_note}"
        )

    lines.append(_fmt_purchase("CEO", ceo))
    lines.append(_fmt_purchase("CFO", cfo))

    lines.append(
        f"Conviction rubric: {'+1.0 APPLIES (purchase ≥ $25K)' if applies else 'does not apply'}"
    )
    return "\n".join(lines)


def _format_market_intelligence(fmp_data: dict) -> str:
    """Render market intelligence block (yfinance + Polygon)."""
    if fmp_data.get("error") and not any([
        fmp_data.get("short_interest_pct"), fmp_data.get("analyst_count"),
        fmp_data.get("consensus_revenue_current_year"),
    ]):
        return f"[Market intelligence unavailable: {fmp_data['error']}]"

    si = fmp_data.get("short_interest_pct")
    dtc = fmp_data.get("days_to_cover")
    analysts = fmp_data.get("analyst_count")
    target = fmp_data.get("target_mean_price")
    eps_cur = fmp_data.get("consensus_eps_current_year")
    eps_nxt = fmp_data.get("consensus_eps_next_year")
    rev_cur = fmp_data.get("consensus_revenue_current_year")
    rev_nxt = fmp_data.get("consensus_revenue_next_year")
    earnings = fmp_data.get("next_earnings_date")
    ltd = fmp_data.get("long_term_debt")
    ap = fmp_data.get("accounts_payable")
    mktcap = fmp_data.get("market_cap")
    mktcap_source = fmp_data.get("market_cap_source")
    cash = fmp_data.get("cash")
    ttm_ocf = fmp_data.get("ttm_operating_cash_flow")
    ocf_annualized = fmp_data.get("ocf_annualized", False)
    runway = fmp_data.get("cash_runway_months")

    def _fmt_pct(v) -> str:
        return f"{v:.1f}%" if v is not None else "unavailable"

    def _fmt_num(v, suffix="") -> str:
        return f"{v}{suffix}" if v is not None else "unavailable"

    def _fmt_dollars(v) -> str:
        if v is None:
            return "unavailable"
        return f"${v / 1_000_000:.1f}M" if v < 1_000_000_000 else f"${v / 1_000_000_000:.2f}B"

    # Implied revenue growth %
    growth_str = "unavailable"
    if rev_cur and rev_nxt and rev_cur > 0:
        growth = (rev_nxt - rev_cur) / rev_cur * 100
        growth_str = f"+{growth:.0f}%" if growth >= 0 else f"{growth:.0f}%"

    # Bug 10: flag stale Polygon reference data so the LLM doesn't treat it as live
    mktcap_note = " [STALE — Polygon reference data; may be months old]" if mktcap_source == "polygon_reference" else ""
    # Bug 9: flag when TTM OCF is a single-quarter annualisation
    ocf_note = " [APPROX: single quarter ×4 — may overstate runway for seasonal businesses]" if ocf_annualized else ""

    lines = [
        "=== MARKET INTELLIGENCE ===",
        f"Market cap:         {_fmt_dollars(mktcap)}{mktcap_note}",
        f"Short interest:     {_fmt_pct(si)}  ({_fmt_num(dtc, ' days to cover')})",
        f"Analyst coverage:   {_fmt_num(analysts, ' analysts')}     [confirms ≤5 universe]",
        f"Analyst price target (mean): {_fmt_num(target, '')}",
        f"Consensus EPS:      FY current {_fmt_num(eps_cur, '')}  (FY next {_fmt_num(eps_nxt, '')})",
        f"Revenue estimate:   FY current ${_fmt_num(rev_cur, 'M')}  "
        f"(FY next ${_fmt_num(rev_nxt, 'M')}) → implied {growth_str} growth",
        f"Next earnings:      {earnings if earnings else 'unavailable'}",
        f"Cash:               {_fmt_dollars(cash)}",
        f"TTM operating CFO:  {_fmt_dollars(ttm_ocf)}{ocf_note}",
        f"Cash runway:        {f'{runway} months (pre-computed — use this value directly for cash_runway_months)' if runway else 'unavailable'}",
        f"Long-term debt:     {_fmt_dollars(ltd)}",
        f"Accounts payable:   {_fmt_dollars(ap)}",
    ]
    return "\n".join(lines)


def _format_transcripts_structured(transcript_data: dict) -> tuple[str, str]:
    """
    Structured transcript formatter. Selects high-signal turns.
    Returns (formatted_transcripts, signal_summary) — both plain strings.

    Turn selection per transcript (~5,000 chars total target):
      1. CEO/CFO prepared remarks (before first analyst question) — up to 3,000 chars
      2. Analyst Q&A pairs with negative analyst sentiment — up to 1,500 chars
      3. Any remaining management turns with sentiment < -0.1 — up to 500 chars
    """
    transcripts = transcript_data.get("transcripts", {})
    if not transcripts:
        warning = transcript_data.get("warning", "No transcripts available")
        return f"[Transcripts unavailable: {warning}]", ""

    EXEC_RE = re.compile(r"chief\s+executive|chief\s+financial|ceo|cfo", re.I)
    ANALYST_RE = re.compile(r"analyst|morgan\s+stanley|goldman|jpmorgan|citi|ubs|baird|piper|cowen", re.I)

    transcript_parts = []
    all_ceo_sentiments: list[tuple[str, list[float]]] = []  # (quarter_key, [sentiments])

    for key, t in sorted(transcripts.items(), reverse=True):  # most recent first
        turns: list[dict] = t.get("turns", [])
        q_date = t.get("date", "")
        quarter_label = f"Q{t.get('quarter', '?')} {t.get('year', '?')} EARNINGS CALL"
        if q_date:
            quarter_label += f" ({q_date})"

        if not turns:
            # Fall back to flat text
            text = t.get("text", "[No text]")
            transcript_parts.append(f"--- {quarter_label} ---\n{text[:5000]}")
            continue

        prepared_block: list[str] = []
        qa_block: list[str] = []
        neg_mgmt_block: list[str] = []

        # Find index of first analyst turn (marks end of prepared remarks)
        first_analyst_idx = next(
            (i for i, turn in enumerate(turns) if ANALYST_RE.search(turn.get("title", "") + turn.get("speaker", ""))),
            len(turns)
        )

        # 1. CEO/CFO prepared remarks
        prepared_chars = 0
        for turn in turns[:first_analyst_idx]:
            title = turn.get("title", "")
            if not EXEC_RE.search(title):
                continue
            speaker = turn.get("speaker", "")
            content = turn.get("content", "")
            try:
                sent = float(turn.get("sentiment", 0))
            except (ValueError, TypeError):
                sent = 0.0
            line = f"{speaker} ({title}) [sentiment: {sent:+.1f}]: {content}"
            if prepared_chars + len(line) > 3000:
                break
            prepared_block.append(line)
            prepared_chars += len(line)

        # 2. Q&A pairs where analyst sentiment < -0.2
        qa_chars = 0
        for i in range(first_analyst_idx, len(turns) - 1):
            turn = turns[i]
            if not ANALYST_RE.search(turn.get("title", "") + turn.get("speaker", "")):
                continue
            try:
                analyst_sent = float(turn.get("sentiment", 0))
            except (ValueError, TypeError):
                analyst_sent = 0.0
            if analyst_sent >= -0.2:
                continue
            # Include analyst question + next management answer
            next_turn = turns[i + 1]
            a_speaker = turn.get("speaker", "Analyst")
            a_title = turn.get("title", "")
            m_speaker = next_turn.get("speaker", "Management")
            m_title = next_turn.get("title", "")
            try:
                m_sent = float(next_turn.get("sentiment", 0))
            except (ValueError, TypeError):
                m_sent = 0.0

            pair = (
                f"Analyst ({a_speaker}, {a_title}) [sentiment: {analyst_sent:+.1f}]: {turn.get('content', '')}\n"
                f"{m_speaker} ({m_title}) [sentiment: {m_sent:+.1f}]: {next_turn.get('content', '')}"
            )
            if qa_chars + len(pair) > 1500:
                break
            qa_block.append(pair)
            qa_chars += len(pair)

        # 3. Any negative management turns not already captured
        neg_chars = 0
        for turn in turns[first_analyst_idx:]:
            title = turn.get("title", "")
            if not EXEC_RE.search(title):
                continue
            try:
                sent = float(turn.get("sentiment", 0))
            except (ValueError, TypeError):
                sent = 0.0
            if sent >= -0.1:
                continue
            speaker = turn.get("speaker", "")
            line = f"{speaker} ({title}) [sentiment: {sent:+.1f}]: {turn.get('content', '')}"
            # Only add if not already in qa_block
            if not any(line[:50] in q for q in qa_block):
                if neg_chars + len(line) > 500:
                    break
                neg_mgmt_block.append(line)
                neg_chars += len(line)

        # Build per-transcript formatted block
        block_lines = [f"--- {quarter_label} ---"]
        if prepared_block:
            block_lines.append("[PREPARED REMARKS]")
            block_lines.extend(prepared_block)
        if qa_block:
            block_lines.append("\n[Q&A — selected for signal]")
            block_lines.extend(qa_block)
        if neg_mgmt_block:
            block_lines.append("\n[NEGATIVE MANAGEMENT SENTIMENT]")
            block_lines.extend(neg_mgmt_block)

        transcript_parts.append("\n".join(block_lines))

        # Collect CEO sentiments for cross-quarter summary
        ceo_turns = [t for t in turns if EXEC_RE.search(t.get("title", ""))]
        ceo_sents: list[float] = []
        for ct in ceo_turns:
            try:
                ceo_sents.append(float(ct.get("sentiment", 0)))
            except (ValueError, TypeError):
                pass
        if ceo_sents:
            all_ceo_sentiments.append((key, ceo_sents))

    # Build cross-quarter signal summary
    signal_lines: list[str] = []
    if len(all_ceo_sentiments) >= 2:
        recent_key, recent_sents = all_ceo_sentiments[0]
        prior_key, prior_sents = all_ceo_sentiments[1]
        recent_avg = sum(recent_sents) / len(recent_sents)
        prior_avg = sum(prior_sents) / len(prior_sents)

        def _label(v: float) -> str:
            if v > 0.1:
                return "positive"
            elif v < -0.1:
                return "negative"
            return "neutral"

        signal_lines.append("=== TRANSCRIPT SIGNAL SUMMARY ===")
        signal_lines.append(
            f"CEO sentiment shift {prior_key}→{recent_key}: "
            f"{_label(prior_avg)} → {_label(recent_avg)}"
        )

    # Collect all negative management turns across all transcripts for red team
    all_negative_turns: list[dict] = []
    for t in transcripts.values():
        for turn in t.get("turns", []):
            try:
                sent = float(turn.get("sentiment", 0))
            except (ValueError, TypeError):
                sent = 0.0
            if sent < -0.1 and EXEC_RE.search(turn.get("title", "")):
                all_negative_turns.append(turn)

    if all_negative_turns:
        signal_lines.append(
            f"Negative management turns (total): {len(all_negative_turns)}"
        )

    formatted = "\n\n".join(transcript_parts)
    signal_summary = "\n".join(signal_lines)
    return formatted, signal_summary


def _build_cash_reconciliation(metrics_10k: dict, metrics_10q: dict, fmp_data: dict) -> str:
    """
    Bug 12: Three cash sources (SEC regex, yfinance, Polygon) populate cash independently.
    Uses the reporting_unit detected from the filing header to normalize SEC values to raw
    dollars before comparing. Flags divergences > 20% explicitly.

    Returns a short reconciliation block, or an empty string if only one source fired.
    """
    sec_cash_raw = metrics_10q.get("cash") or metrics_10k.get("cash")  # string, e.g. "$285,000"
    yf_cash = fmp_data.get("cash")  # float, raw dollars, e.g. 285_000_000.0

    # Use detected reporting unit; fall back to thousands (SEC convention)
    reporting_unit = (
        metrics_10q.get("reporting_unit")
        or metrics_10k.get("reporting_unit")
        or "thousands"
    )
    unit_multiplier = {"thousands": 1_000, "millions": 1_000_000}.get(reporting_unit, 1_000)
    unit_label = (
        f"detected from filing: {reporting_unit}"
        if (metrics_10q.get("reporting_unit") or metrics_10k.get("reporting_unit"))
        else "not detected — assumed thousands per SEC convention"
    )

    sources: list[str] = []
    sec_cash_normalized: float | None = None

    if sec_cash_raw:
        digits = sec_cash_raw.replace("$", "").replace(",", "").strip()
        try:
            sec_cash_normalized = float(digits) * unit_multiplier
            sec_fmt = (
                f"${sec_cash_normalized / 1_000_000:.1f}M"
                if sec_cash_normalized < 1_000_000_000
                else f"${sec_cash_normalized / 1_000_000_000:.2f}B"
            )
            sources.append(
                f"SEC filing regex: {sec_cash_raw} (units: {unit_label} → {sec_fmt})"
            )
        except (ValueError, TypeError):
            sources.append(f"SEC filing regex: {sec_cash_raw} (unit conversion failed — {unit_label})")

    if yf_cash is not None:
        yf_str = (
            f"${yf_cash / 1_000_000:.1f}M"
            if yf_cash < 1_000_000_000
            else f"${yf_cash / 1_000_000_000:.2f}B"
        )
        sources.append(f"yfinance (quarterly balance sheet): {yf_str} (raw dollars — authoritative)")

    if len(sources) < 2:
        return ""  # Only one source — no conflict possible, no note needed

    # Divergence check — only meaningful after normalizing SEC to raw dollars
    divergence_note = ""
    if sec_cash_normalized is not None and yf_cash is not None and yf_cash > 0:
        divergence = abs(sec_cash_normalized - yf_cash) / yf_cash
        if divergence > 0.20:
            divergence_note = (
                f"\n  WARNING: values diverge by {divergence:.0%} after unit normalization. "
                "Possible causes: different report dates, incorrect unit assumption, "
                "or short-term investments included in one source but not the other. "
                "Note the discrepancy in the summary if it affects the cash_runway_months calculation."
            )

    return (
        "\n=== CASH SOURCE RECONCILIATION ===\n"
        f"Multiple sources reported cash. SEC value converted using filing units ({unit_label}).\n"
        + "\n".join(f"  • {s}" for s in sources)
        + divergence_note
        + "\nUse the yfinance figure for cash_runway_months.\n"
    )


def _build_structured_block(
    ticker: str,
    sec: dict,
    news: dict,
    form4: dict,
    fmp: dict,
) -> str:
    """
    Build the structured data block: pre-extracted metrics, insider buying,
    market intelligence, cash reconciliation, and recent news.
    This is passed upfront in both the ReAct retrieval message and the synthesis message.
    Does NOT include SEC narrative text or transcripts — those come via pgvector retrieval.
    """
    cash_reconciliation = _build_cash_reconciliation(
        sec.get("metrics_10k", {}), sec.get("metrics_10q", {}), fmp
    )

    return f"""{_format_financial_metrics(sec.get('metrics_10k', {}), sec.get('metrics_10q', {}))}

{_format_insider_buying(form4)}

{_format_market_intelligence(fmp)}
{cash_reconciliation}

=== RECENT NEWS (last 30 days) ===
{_format_news(news)}"""



# ── System Prompts ────────────────────────────────────────────────────────────

# ACTIVE: System prompt (Claude-compatible)
# Originally tuned for GPT-4.1. Claude handles this well as-is.
# A lighter Claude-native version is preserved in the commented block below.
def _build_system_prompt() -> str:
    today = date.today().isoformat()
    return"""You are a senior equity research analyst at a long/short hedge fund focused on
US micro/small-cap equities ($50M–$2B, SaaS/Healthcare/Industrials, ≤5 analysts).
You produce investment memos from primary source documents only — SEC filings,
earnings transcripts, news. No general market knowledge. If a section is missing,
say so explicitly; do not infer.

---

THINKING ORDER — complete in sequence before writing any output:

1. SURVIVAL (sub-$2B only): Cash vs. burn → cash_runway_months. Derive as:
   cash_runway_months = cash and equivalents ÷ average quarterly cash burn
   (last two quarters) × 3. Debt covenants, maturity schedule, FCF trajectory.
   Survival uncertainty overrides all other factors.
2. BALANCE SHEET: Flag net debt > 3x EBITDA, covenant violations, maturities within
   24 months, dilution risk (ATM, converts, preferred).
3. BUSINESS QUALITY — sector-specific tests:
   - All sectors: gross margin trend, guidance hit rate, revenue driver (price/volume/one-time).
   - SaaS: net revenue retention, deferred revenue trend, customer concentration.
   - Healthcare: FDA pathway stage (earlier = higher binary risk); payer mix and CMS
     reimbursement trends; procedure volume vs. revenue divergence (gap = reimbursement
     or coding problem, not demand — distinguish explicitly).
   - Industrials: book-to-bill (>1.0 growth, <1.0 contraction); backlog coverage
     (backlog ÷ quarterly revenue × 3 — below 6 months = visibility risk); capex
     cycle position (state early/mid/late from order trends and commentary).
4. VALUATION: EV = market cap + total debt − cash and equivalents. Always
   cash-adjust before computing any EV-based multiple. Pre-profit → EV/Revenue;
   profitable → EV/EBITDA or P/E. State the computed figure vs. peers or historical
   range. Write "unavailable — not in source documents" only if market cap AND
   revenue estimates are both absent from the prompt.
5. CATALYSTS: Specific, time-bound, binary outcome only. "Continued execution" is not
   a catalyst. Only list events that have NOT yet occurred as of {today}.

---

CONVICTION SCORE RUBRIC — mechanical derivation required:

   Process: (1) Start at 5.0. (2) Evaluate each item YES/NO with reason. (3) Sum.
   (4) Apply hard caps. (5) Write rationale as:
   "5.0 base + [applied additions] - [applied deductions] = [score]; [dominant factor]"
   List only items that moved the score. Do not cite absences.

   Additions:
   +1.0  Strong revenue growth with evidence of operating leverage
   +1.0  Valuation discount to peers with a documented mispricing reason
   +1.0  CEO/CFO open-market purchase ≥ $25K in past 90 days (Form 4; check "value"
         field — purchases below $25K are noted but do not qualify)
   +1.0  Specific near-term catalyst with binary outcome
   +0.5  Management track record of hitting or beating guidance

   Deductions:
   -1.5  Active SEC enforcement / formal government investigation (Wells Notice,
         SEC order, CFTC charge, DOJ indictment). ATTORNEY MARKETING EXCLUSION:
         do NOT apply for plaintiff law firm solicitation letters — identified by
         phrases "on behalf of investors," "class period," "shareholders who
         purchased," "no obligation to you," "no cost to you," "encourage you to
         contact." These are not regulatory findings and never trigger this deduction.
   -1.0  Negative FCF + net debt + cash runway < 18 months (all three required)
   -1.0  Guidance reset or material miss in the most recent quarter
   -1.0  Single product or customer concentration > 30% of revenue
   -0.5  History of missing guidance or moving goalposts
   -0.5  Borrow costs > 15% annualized (short candidates only)

   Hard caps:
   - Score ≤ 8.0 unless variant perception with a named metric is documented
   - Score floor 1.0 (floor 0.5 if insolvency risk present)
   - Score < 4.0 → verdict must be AVOID
   - SHORT requires score < 4.0 AND a specific repricing catalyst

   Calibration anchors:
   - 8.0–8.5: All four +1.0 additions + +0.5 + zero deductions + variant perception
     documented. Rarest band — most memos in this universe will not reach it.
   - 6.0–7.0: Two or three additions, zero or one deduction, variant perception
     present. Example: 5.0 + 1.0 + 1.0 + 0.5 - 1.0 = 6.5.
   - 4.0–5.0: One addition with one offsetting deduction, or baseline. Not
     actionable as LONG.

   Sanity check before writing any score ≥ 7.0 — verify all three:
   (a) -1.0 deduction for negative FCF + net debt + runway < 18 months was
       evaluated and either does not apply or was subtracted.
   (b) Variant perception absent → 8.0 cap applied.
   (c) No single data point drove score above 7.0 without at least two other
       additions also applying. Recalculate if any check fails.

---

VARIANT PERCEPTION — required field:

   Step 1 — Find the contradiction: identify a metric moving OPPOSITE to the price
   action or consensus narrative (e.g. procedures up while revenue missed; margins
   expanding while headline missed; backlog growing while revenue fell). If none
   exists, state that explicitly → conviction capped at 6.0, verdict cannot be LONG.
   Step 2 — Anchor the market belief: state what the PRICE is pricing in, not what
   analysts say. A 15% drop post-miss implies the market believes structural
   deterioration. Name it.
   Step 3 — Write: "The market believes [price-implied belief]. We believe [specific
   contradiction] because [exact metric and value from documents]."
   The field must cite a specific number. "Execution risk is underappreciated" fails.

REPRICING CATALYST — required field:
   Format: "The repricing event is [event], expected [timeframe], which will reveal [Z]."
   Must include a date window and a binary outcome. Use next earnings date from
   MARKET INTELLIGENCE if available. "Continued execution" is not acceptable.

---

BULL THESIS — quality gate per point: "Does this describe something the current
price does NOT already reflect?" If no → consensus, not alpha. Rewrite or remove.

BEAR THESIS — minimum 4 points, each a DIFFERENT failure mode from: balance sheet,
competitive position, management execution, valuation, regulatory/legal, market
structure, fraud/governance. Do not soften existential risks. A formal SEC
enforcement action is not "legal scrutiny." Plaintiff solicitation letters (see
attorney marketing exclusion in rubric) are not regulatory findings — note as
potential litigation risk only.

SUMMARY — 3-5 sentences citing specific dollar amounts, percentages, or dates from
source documents. Address the single highest-severity risk. If verdict is AVOID or
SHORT, end with the specific observable condition that would change the verdict.
Do not write a balanced summary when the risk profile is asymmetric.

---

Today's date is {today}. Past events belong in thesis/summary as context, not in
the catalysts field.

---

CRITICAL FORMATTING RULES:
   - Respond with a single valid JSON object ONLY
   - No markdown, no code fences, no text before or after the JSON
   - Every field is REQUIRED — do not omit any
   - Use "unknown" only when data is genuinely absent from source documents

Required JSON schema (output must match exactly):
{{
  "ticker": "string — uppercase ticker symbol",
  "date": "string — YYYY-MM-DD",
  "company_overview": "string — 2-4 sentence business description from filings only",
  "bull_thesis": [
    "point 1 — forward-looking, describes something not priced in",
    "point 2",
    "point 3"
  ],
  "bear_thesis": [
    "point 1 — balance sheet or survival risk",
    "point 2 — competitive or moat risk",
    "point 3 — management execution or governance risk",
    "point 4 — valuation, regulatory, or market structure risk"
  ],
  "key_risks": ["risk 1 — specific and evidenced", "risk 2"],
  "catalysts": [
    "catalyst 1 — event name, approximate timing, bull outcome vs bear outcome"
  ],
  "financial_health": {{
    "revenue_trend": "growing | stable | declining | unknown",
    "margin_trend": "expanding | stable | contracting | unknown",
    "debt_level": "low | moderate | high | unknown",
    "fcf": "strong | neutral | weak | unknown",
    "cash_runway_months": "number | null — required for sub-$2B market cap (formula: cash and equivalents ÷ average quarterly cash burn last two quarters × 3); null for larger companies"
  }},
  "valuation_note": "string — specific metric (EV/Revenue, EV/EBITDA, P/E, P/FCF) vs peers or historical range; EV must be cash-adjusted (market cap + total debt − cash); or 'unavailable — not in source documents'",
  "macro_sensitivity": "string — one sentence per regime: 'Risk-On: [outperform|underperform|neutral] because [mechanism]. Risk-Off: [...]. Transitional: [...]. Stagflation: [...].' All four required. Base on company-specific cost structure, pricing power, revenue mix — not generic sector assumptions.",
  "verdict": "LONG | SHORT | AVOID",
  "conviction_score": <number 0.0-10.0>,
  "conviction_score_rationale": "string — derivation in format: 5.0 base + [additions] - [deductions] = [score]; [dominant factor]",
  "variant_perception": "string — Format: 'The market believes [price-implied belief]. We believe [specific contradiction] because [exact metric and value from documents].' Must cite a specific number. If absent, conviction is capped at 6.0 and verdict cannot be LONG.",
  "repricing_catalyst": "string — Format: 'The repricing event is [X], expected [timeframe], which will reveal [Z].'",
  "suggested_position_size": "small | medium | large | skip — mapping: skip if conviction < 4.0 OR verdict AVOID/SHORT; small (4.0–5.9) = watchlist only, not an active position trigger; medium 6.0–7.4; large ≥ 7.5 AND variant_perception documented. Apply mechanically.",
  "summary": "string — 3-5 sentences with specific data points; addresses highest-severity risk; ends with verdict-change condition if AVOID or SHORT",
  "red_team_risks": [
    "attack bull thesis point 1 directly — cite a specific number or statement from source documents",
    "attack bull thesis point 2 directly — same citation requirement",
    "worst credible outcome if bear case is right — grounded in a specific document risk",
    "single observable data point or event that would confirm the bear case is playing out",
    "risk the filing language obscures — cite the specific language and explain what it hides"
  ]
}}

HARD RULE: Every red team risk must cite at least one specific data point, quote, or metric
from source documents. Risks with no documentary evidence must not be included. Write fewer
than 5 if evidence is insufficient. Do not invent bearish narratives to fill slots."""


# ── ReAct Retrieval System Prompt ─────────────────────────────────────────────

def _build_react_system_prompt() -> str:
    return """You are a senior equity research analyst at a long/short hedge fund focused on
US micro/small-cap equities ($50M–$2B, SaaS/Healthcare/Industrials, ≤5 analysts).

YOUR ROLE IN THIS PHASE: Information gathering only. You are NOT writing the investment
memo yet. You are using the search_documents tool to retrieve the unstructured narrative
evidence — from SEC filings and earnings call transcripts in the pgvector database — that
will ground every claim in the final memo. Do not analyze, score, or conclude during this
phase. Retrieve, then stop.

You are NOT a news summarizer. You are not here to collect background facts. You are here
to retrieve the specific documentary evidence that will support or refute the thesis signals
already visible in the structured metrics block above.

What failure looks like in this phase: issuing queries that are generic ("company overview",
"financial results") when the structured data has already flagged a specific red flag that
demands targeted retrieval. A retrieval phase that ends without evidence for variant_perception,
repricing_catalyst, and the top two bear thesis failure modes is incomplete.

---

TOOL AVAILABLE:

search_documents(query: str, doc_types: list[str], n: int) -> list[dict]

  query     — natural language search string; write it as a phrase a CFO or analyst would say,
               not a database keyword. "We have sufficient liquidity" retrieves covenant
               language. "Revenue concentration" retrieves customer risk disclosures.
  doc_types — one or more of: ["10-K", "10-Q", "transcript"]. Always specify explicitly.
               Do NOT pass ["all"] — filter by what you actually need.
  n         — number of chunks to retrieve. Use 3–5 for targeted queries. Use 6–8 only when
               fishing for a signal you have not yet found and have budget remaining.

Each returned chunk includes: source_type, date, and text. Read the text before deciding
whether to issue a follow-up query.

---

MANDATORY THINKING ORDER — complete these steps in sequence before issuing each query:

1. SURVIVAL SIGNAL FIRST: Before any other query, check the structured metrics above for
   negative FCF, net debt, cash runway < 18 months, ATM program, or near-term debt
   maturities. If any flag is present, your first query MUST retrieve management commentary
   on liquidity, covenants, or capital raise plans. Evidence of a going concern opinion or
   covenant violation is the highest-priority retrieval target in this universe.

2. SHORT INTEREST CHASE: If short interest > 15% (visible in the MARKET INTELLIGENCE block),
   your second query MUST search for the bear thesis. Search transcripts for analyst pushback
   on contested topics and filings for the risk factors short sellers are likely citing.
   High short interest without a retrieved bear thesis is an incomplete retrieval.

3. VARIANT PERCEPTION HUNT: Identify any metric in the structured block that moves OPPOSITE
   to what the price action or short interest implies. Examples: procedures growing while
   revenue missed; backlog growing while revenue fell; gross margins expanding while the
   stock is down. Issue a query specifically targeting the narrative explanation for that
   divergence. This is the most valuable retrieval target for memo quality.

4. REPRICING CATALYST: Search for any time-bound, binary event mentioned in filings or
   transcripts — FDA decision dates, contract renewal windows, debt maturity dates, earnings
   dates, regulatory approval timelines. "Continued execution" language in transcripts is
   NOT a repricing catalyst. Retrieve the specific event, not the sentiment.

5. MANAGEMENT TONE SHIFT: If two or more transcript quarters are available, issue at least
   one query against transcripts only to retrieve language on guidance, forward outlook, or
   topics the CEO/CFO addressed differently across quarters. Sentiment shift is a signal;
   retrieve the language that explains it.

---

QUERY STRATEGY — mandatory sequence:

Issue queries in this order. Do not skip phases. Do not issue phase 3 queries before
exhausting phase 1 and phase 2.

PHASE 1 — SURVIVAL AND RED FLAGS (queries 1–3):
  Priority order within phase 1:
  a. Liquidity and covenant language (if any survival flag is present in metrics)
  b. Going concern, material weakness, covenant violation — always search this even if
     metrics look clean; these disclosures are often buried in footnotes
  c. Debt maturity schedule and refinancing commentary

  Example queries for phase 1:
    "going concern substantial doubt liquidity"         → doc_types: ["10-K", "10-Q"], n: 5
    "covenant compliance debt maturity refinancing"     → doc_types: ["10-K", "10-Q"], n: 5
    "ATM program equity offering shelf registration"    → doc_types: ["10-K", "10-Q"], n: 4

PHASE 2 — BEAR THESIS AND SHORT INTEREST (queries 4–5):
  If short interest > 15%: search for the exact risk the shorts are pricing.
  If short interest ≤ 15%: search for the top two risk factors by severity from the 10-K
  risk factors section.

  Required coverage — retrieve evidence for at least two DISTINCT failure modes from:
    balance sheet fragility | competitive displacement | management credibility |
    regulatory or legal exposure | customer/product concentration | market structure change

  Example queries for phase 2:
    "customer concentration revenue dependence single customer"  → ["10-K", "10-Q"], n: 5
    "competitive pressure pricing discount market share"         → ["10-K", "transcript"], n: 5

PHASE 3 — BULL THESIS AND VARIANT PERCEPTION (queries 6–8):
  Search for the evidence that would support a non-consensus view — something the price
  does NOT already reflect. Focus on operating metrics that diverge from headline numbers.

  Required: at least one query must target the specific divergence identified in
  THINKING ORDER step 3.

  Example queries for phase 3:
    "procedure volume growth reimbursement rate"         → ["10-K", "10-Q", "transcript"], n: 6
    "net revenue retention churn upsell expansion"       → ["10-K", "10-Q", "transcript"], n: 5
    "book to bill backlog order growth pipeline"         → ["10-K", "10-Q", "transcript"], n: 5
    "gross margin expansion operating leverage scale"    → ["10-K", "10-Q"], n: 5

PHASE 4 — REPRICING CATALYST AND MANAGEMENT TONE (queries 9–10, use only if budget remains):
  Search transcripts separately from filings. Tone shifts, hedging language, or newly
  introduced risk language in prepared remarks are not captured by metrics.

  Example queries for phase 4:
    "FDA approval decision PDUFA date regulatory milestone"      → ["transcript", "10-K"], n: 5
    "guidance raised lowered withdrew visibility confidence"     → ["transcript"], n: 6
    "capital allocation buyback dividend return shareholders"    → ["transcript", "10-K"], n: 4

---

EFFICIENCY RULES:

NEVER issue a query whose result would not change the memo. Ask: "Which memo field does
this query serve?" If you cannot name a specific field (variant_perception, bear_thesis
point 2, cash_runway_months, repricing_catalyst, etc.), do not issue the query.

NEVER issue two queries that retrieve the same content type for the same topic. If query 2
retrieved covenant language from 10-Q filings, do not issue a 10-K query on the same topic
unless the 10-Q result was insufficient (fewer than 2 chunks returned, or no relevant text).

NEVER issue a query with vague phrasing: "financial performance overview", "business
description", "key highlights". Every query must target a specific memo field or red flag.

PREFER multi-field queries: a query like "gross margin expansion operating leverage SaaS"
can simultaneously ground the bull thesis point on unit economics AND the variant_perception
field. Write queries that cover multiple memo fields simultaneously.

---

ANTI-PATTERNS — these are the specific wrong behaviors in this phase:

NEVER: Issue query 1 as "company overview" or "business description."
WHY: This information is already in the structured metrics block. Repeating it wastes
     budget on zero-signal retrieval.
INSTEAD: Start with the highest-severity survival or red-flag query derived from
         the metrics already visible above.

NEVER: Search only filings and skip transcripts.
WHY: Management tone, forward guidance language, and analyst pushback in Q&A are only
     in transcripts. A memo with no transcript evidence for management credibility
     assessments is structurally incomplete.
INSTEAD: Issue at least two queries with doc_types containing "transcript".

NEVER: Stop at query 3 because "enough information has been gathered."
WHY: variant_perception and repricing_catalyst require targeted retrieval that generic
     early queries do not cover. Stopping before phase 3 guarantees those fields will
     be weak or unsupported in the final memo.
INSTEAD: Complete all four phases before evaluating whether to terminate.

NEVER: Issue a query targeting plaintiff law firm language ("class action", "shareholder
lawsuit", "securities class period") and treat a result as evidence of SEC enforcement.
WHY: Attorney marketing solicitations are identifiable by phrases like "on behalf of
     investors," "no cost to you," "encourage you to contact" — these are not regulatory
     findings and produce a false conviction deduction if misclassified.
INSTEAD: Search specifically for SEC enforcement language: "Wells Notice", "SEC order",
         "formal investigation", "consent order", "CFTC charge", "DOJ indictment."

NEVER: Issue more than 10 queries regardless of how many fields remain uncovered.
WHY: The hard cap is a discipline mechanism. If 10 queries are insufficient, the
     structured metrics block above was not used effectively to narrow the target.
INSTEAD: Prioritize phase 1 and phase 2 before spending budget on phase 3 and 4.

---

QUALITY FILTER — apply before terminating:

Before issuing the termination signal, run this self-test:

  (a) Survival check: Is there retrieved documentary evidence for cash runway, covenant
      status, or going concern — OR did the metrics block show clean financials (positive
      FCF, no net debt, runway > 24 months) that made this search unnecessary?
      If neither condition is met -> issue one more phase 1 query.

  (b) Variant perception check: Is there at least one retrieved chunk that shows a metric
      moving OPPOSITE to what the price or short interest implies?
      If no -> issue one phase 3 query targeting the specific divergence before terminating.

  (c) Bear thesis coverage: Are at least two DISTINCT failure modes represented in
      retrieved chunks, from different categories (balance sheet, competitive, management,
      regulatory, concentration, governance)?
      If only one category is covered -> issue one phase 2 query before terminating.

  (d) Transcript evidence: Is at least one retrieved chunk from a transcript?
      If no -> issue one transcript query before terminating.

  If all four conditions are satisfied -> output the termination signal.
  If budget is exhausted (10 queries issued) -> output the termination signal regardless.

---

MISSING DATA HANDLING:

If a required evidence category is absent after exhausting all budget:
  - Survival/covenant: if no filing language found after 2 phase 1 queries, note mentally
    that cash_runway_months will rely solely on the pre-computed value in the metrics block.
  - Variant perception: if no divergent metric is found in any retrieved chunk, note
    mentally that variant_perception will be absent -> conviction cap of 6.0 will apply
    and verdict cannot be LONG in the final memo.
  - Repricing catalyst: if no time-bound event is found in any chunk, note mentally that
    repricing_catalyst will be "unavailable from source documents" -> SHORT verdict is
    blocked regardless of score.
  - Do not invent evidence. An absent field is always preferable to an unsupported claim.

---

EVIDENCE STANDARDS:

Every major claim in the final memo must be grounded in a chunk retrieved in this phase.
This means:
  - Do not stop retrieval until you have chunks that can support: company_overview,
    at least 3 bull thesis points, at least 4 bear thesis failure modes, variant_perception
    (with a specific metric and value), repricing_catalyst (with a timeframe), and
    cash_runway_months (or a clean bill of financial health).
  - If a retrieved chunk does not contain specific numbers, dates, or quoted language,
    it is low-signal. Note this mentally and consider whether a more targeted follow-up
    query would retrieve higher-quality evidence.
  - Chunks with no relevant text (boilerplate, table of contents, legal disclaimers) do
    not count toward evidence coverage. Assess chunk quality before marking a field covered.

---

TERMINATION:

When the quality filter passes (all four conditions satisfied) OR when 10 queries have
been issued, output exactly the following JSON object and nothing else:

{"status": "retrieval_complete"}

No markdown. No code fences. No explanatory text before or after. A single valid JSON
object on a single line.

Do not write any analysis, scoring, or memo draft before outputting the termination
signal. This phase ends at retrieval_complete. The memo-writing phase is separate."""


# ── ReAct Loop ────────────────────────────────────────────────────────────────

def _format_retrieved_chunks(chunks: list[dict]) -> str:
    """Format retrieved chunks with source labels and similarity scores."""
    if not chunks:
        return "[No chunks retrieved]"
    lines = []
    for i, chunk in enumerate(chunks, 1):
        doc_type = chunk.get("doc_type", "unknown")
        section = chunk.get("section", "")
        similarity = chunk.get("similarity", 0)
        source_label = f"{doc_type}" + (f" — {section}" if section else "")
        lines.append(
            f"[{i}] Source: {source_label} | Similarity: {similarity:.3f}\n"
            f"{chunk.get('content', '')}"
        )
    return "\n\n---\n\n".join(lines)


def _run_agentic_retrieval(
    ticker: str,
    structured_block: str,
    openai_client: OpenAI,
) -> tuple[list[dict], dict]:
    """
    ReAct agentic retrieval loop.
    OpenAI iteratively issues search_documents tool calls against pgvector.
    Returns (deduplicated chunks, openai_usage_dict).

    On any failure: logs warning, returns ([], {}) so caller falls back gracefully.
    """
    system_prompt = _build_react_system_prompt()
    initial_user_msg = (
        f"Company: {ticker}\n\n"
        f"STRUCTURED DATA (already available — do not search for this):\n"
        f"{structured_block}\n\n"
        f"Now use search_documents to gather the narrative evidence you need from "
        f"SEC filings and earnings transcripts. Follow the search strategy in your instructions."
    )

    messages = [{"role": "user", "content": initial_user_msg}]
    all_chunks: list[dict] = []
    seen_chunk_ids: set[str] = set()
    openai_input_tokens = 0
    openai_output_tokens = 0

    try:
        for turn in range(MAX_TURNS):
            response = openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "system", "content": system_prompt}] + messages,
                tools=[SEARCH_TOOL],
                tool_choice="auto",
                temperature=0.1,
                max_completion_tokens=1500,
            )

            # Accumulate token usage per turn
            if response.usage:
                openai_input_tokens += response.usage.prompt_tokens
                openai_output_tokens += response.usage.completion_tokens
                print(
                    f"  [ReAct turn {turn+1}] "
                    f"in={response.usage.prompt_tokens:,}  "
                    f"out={response.usage.completion_tokens:,}  "
                    f"chunks_so_far={len(all_chunks)}"
                )

            choice = response.choices[0]
            msg = choice.message

            # Append assistant message to history
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in (msg.tool_calls or [])
                ] or None,
            })

            # Check termination
            if choice.finish_reason == "stop" or not msg.tool_calls:
                content = msg.content or ""
                if "retrieval_complete" in content or not msg.tool_calls:
                    logger.info("_run_agentic_retrieval(%s): terminated at turn %d", ticker, turn + 1)
                    break

            # Process tool calls
            tool_results = []
            for tool_call in (msg.tool_calls or []):
                if tool_call.function.name != "search_documents":
                    continue

                try:
                    tool_input = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {}

                query = tool_input.get("query", "")
                doc_types = tool_input.get("doc_types") or None
                n = min(int(tool_input.get("n", 4)), 8)

                print(f"    → search_documents({query!r}, doc_types={doc_types}, n={n})")

                chunks: list[dict] = []
                if query:
                    try:
                        chunks = search_similar(query, ticker=ticker, doc_types=doc_types, n=n)
                    except Exception as exc:
                        logger.warning(
                            "_run_agentic_retrieval(%s): search_similar failed — %s", ticker, exc
                        )

                # Deduplicate by chunk id
                new_chunks = []
                for c in chunks:
                    chunk_id = str(c.get("id", ""))
                    if chunk_id and chunk_id not in seen_chunk_ids:
                        seen_chunk_ids.add(chunk_id)
                        all_chunks.append(c)
                        new_chunks.append(c)

                print(f"      ↳ {len(new_chunks)} new chunks (total unique: {len(all_chunks)})")

                tool_result_content = _format_retrieved_chunks(new_chunks) if new_chunks else "[No new results]"
                tool_results.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "content": tool_result_content,
                })

            if tool_results:
                messages.extend(tool_results)
            elif not msg.tool_calls:
                break

        else:
            logger.warning(
                "_run_agentic_retrieval(%s): hit MAX_TURNS=%d without termination signal",
                ticker, MAX_TURNS,
            )

    except Exception as exc:
        logger.warning("_run_agentic_retrieval(%s): loop failed — %s; proceeding without retrieval", ticker, exc)
        return [], {}

    logger.info("_run_agentic_retrieval(%s): collected %d unique chunks", ticker, len(all_chunks))
    usage = {"phase": "ReAct retrieval (OpenAI)", "input": openai_input_tokens, "output": openai_output_tokens}
    return all_chunks, usage


def _read_macro_context() -> Optional[str]:
    """Return a formatted macro regime block for injection into the synthesis message.
    Returns None if macro_briefings is empty or the query fails (non-blocking)."""
    try:
        from backend.memory.vector_store import _get_client
        client = _get_client()
        result = (
            client.table("macro_briefings")
            .select("regime,regime_confidence,growth_score,inflation_score,fed_score,stress_score,date")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        if not result.data:
            return None
        row = result.data[0]
        return (
            f"CURRENT MACRO REGIME: {row['regime']} "
            f"(confidence: {row['regime_confidence']:.1f}/10, as of {row['date']})\n"
            f"Sub-scores — Growth: {row['growth_score']:.2f}, "
            f"Inflation: {row['inflation_score']:.2f}, "
            f"Fed: {row['fed_score']:.2f}, "
            f"Stress: {row['stress_score']:.2f}\n"
            f"Use this regime to ground the macro_sensitivity field with today's actual conditions."
        )
    except Exception as exc:
        logger.warning("_read_macro_context: failed — %s", exc)
        return None


def _build_synthesis_message(
    ticker: str,
    structured_block: str,
    retrieved_chunks: list[dict],
    macro_context: Optional[str] = None,
) -> str:
    """Assemble final synthesis prompt combining structured data and retrieved narrative chunks."""
    if retrieved_chunks:
        chunks_section = (
            f"=== RETRIEVED DOCUMENT EVIDENCE ===\n"
            f"(from SEC filings and earnings transcripts, retrieved via semantic search)\n\n"
            f"{_format_retrieved_chunks(retrieved_chunks)}"
        )
    else:
        chunks_section = "[No narrative documents retrieved — base memo on structured data only]"

    message = (
        f"Analyze {ticker.upper()} and produce a structured investment memo.\n\n"
        f"{structured_block}\n\n"
        f"{chunks_section}\n\n"
        "Valuation multiples rule: whenever you cite an EV/Revenue multiple, label it inline as either (FY2025E) or (FY2026E). "
        "Never cite an EV/Revenue multiple without a year label.\n"
        f"Respond with a single valid JSON object. No markdown, no code fences, no explanatory text — pure JSON only.\n"
        f"Use today's date ({date.today().isoformat()}) for the \"date\" field.\n"
    )
    if macro_context:
        message = macro_context + "\n\n" + message
    return message


# ── Red Team ─────────────────────────────────────────────────────────────────

def _build_red_team_system_prompt() -> str:
    return """You are a short-seller at an activist hedge fund. Your job is to destroy bull theses.

You will be given an investment memo that recommends LONG or AVOID on a stock.
Your task: argue as aggressively as possible against the bull case.

Rules:
- Assume the bull thesis is wrong. Find every flaw, hidden risk, and optimistic assumption.
- Be specific — reference the data points in the memo, then explain why they are misleading, overstated, or fragile.
- Each risk must identify a distinct failure mode. Do not repeat variations of the same concern.
- Focus on: balance sheet fragility, competitive threats the memo ignores, management credibility gaps, valuation traps, and macro sensitivities the memo underweights.
- If the memo already lists a risk, you may escalate it — explain why the memo is not taking it seriously enough.
- Do not be diplomatic. Be direct and adversarial.

Respond with a single valid JSON object only. No markdown, no code fences.

Required schema:
{
  "red_team_risks": [
    "specific adversarial risk 1",
    "specific adversarial risk 2",
    "specific adversarial risk 3",
    "specific adversarial risk 4",
    "specific adversarial risk 5"
  ]
}

Up to 5 points. Write fewer if you cannot find documentary evidence for a risk — do not invent bearish narratives to fill slots. Each point must be a distinct failure mode not already fully addressed by the memo."""


def _build_red_team_user_message(memo: dict, raw_context: dict) -> str:
    bull = "\n".join(f"- {p}" for p in memo.get("bull_thesis", []))
    bear = "\n".join(f"- {p}" for p in memo.get("bear_thesis", []))
    risks = "\n".join(f"- {p}" for p in memo.get("key_risks", []))
    health = memo.get("financial_health", {})

    # Build source excerpts block from raw_context
    source_lines = ["SOURCE EXCERPTS (not in memo author's view — look for discrepancies):"]
    if raw_context.get("metrics"):
        source_lines.append(raw_context["metrics"])
    if raw_context.get("transcript_signal"):
        source_lines.append(raw_context["transcript_signal"])
    neg_turns = raw_context.get("negative_turns", [])
    if neg_turns:
        source_lines.append("[Top negative management turns verbatim]")
        for t in neg_turns[:3]:
            speaker = t.get("speaker", "")
            title = t.get("title", "")
            content = t.get("content", "")
            try:
                sent = float(t.get("sentiment", 0))
            except (ValueError, TypeError):
                sent = 0.0
            source_lines.append(f"  {speaker} ({title}) [sentiment: {sent:+.1f}]: {content[:300]}")
    source_block = "\n".join(source_lines)

    return f"""Investment memo for {memo.get('ticker', 'UNKNOWN')}:

COMPANY OVERVIEW:
{memo.get('company_overview', '[unavailable]')}

BULL THESIS (what you must attack):
{bull}

BEAR THESIS (already identified — escalate or find gaps):
{bear}

KEY RISKS (already identified — escalate or find gaps):
{risks}

FINANCIAL HEALTH SUMMARY:
- Revenue trend: {health.get('revenue_trend', 'unknown')}
- Margin trend: {health.get('margin_trend', 'unknown')}
- Debt level: {health.get('debt_level', 'unknown')}
- FCF: {health.get('fcf', 'unknown')}

VERDICT: {memo.get('verdict', 'unknown')} — Conviction: {memo.get('conviction_score', 'unknown')}/10

SUMMARY:
{memo.get('summary', '[unavailable]')}

{source_block}

Now argue aggressively against this bull thesis. Find 5 distinct failure modes the memo misses or underweights."""


def _run_red_team(client: anthropic.Anthropic, memo: dict, raw_context: dict) -> tuple[list[str], dict]:
    """
    Second LLM call: adversarial critique of the bull thesis.
    Returns (risks, usage_dict). Risks is empty list on failure — never blocks the main memo.
    """
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            temperature=0.3,
            system=_build_red_team_system_prompt(),
            messages=[{"role": "user", "content": _build_red_team_user_message(memo, raw_context)}],
        )
        raw = response.content[0].text or ""
        usage = {
            "phase": "Red team (Claude)",
            "input": response.usage.input_tokens,
            "output": response.usage.output_tokens,
            "cache_write": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        }

        cleaned = _strip_code_fences(raw)
        parsed = json.loads(cleaned)
        risks = parsed.get("red_team_risks", [])
        if isinstance(risks, list) and all(isinstance(r, str) for r in risks):
            return risks, usage
        return [], usage
    except Exception as exc:
        logger.warning("_run_red_team(%s): failed — %s", memo.get("ticker", "?"), exc)
        return [], {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_code_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
    text = re.sub(r"\s*```$", "", text.strip())
    return text.strip()


def _print_usage_summary(ticker: str, usage_log: list[dict]) -> None:
    """Print a token usage table to stdout after every research run."""
    total_in = sum(u["input"] for u in usage_log)
    total_out = sum(u["output"] for u in usage_log)
    sep = "─" * 62
    print(f"\n{'='*62}")
    print(f"  TOKEN USAGE — {ticker}")
    print(f"{'='*62}")
    print(f"  {'Phase':<28}  {'Input':>7}  {'Output':>7}  {'Total':>8}")
    print(f"  {sep}")
    for u in usage_log:
        extra = ""
        if u.get("cache_write") or u.get("cache_read"):
            extra = f"  [cache wr={u.get('cache_write',0):,} rd={u.get('cache_read',0):,}]"
        print(f"  {u['phase']:<28}  {u['input']:>7,}  {u['output']:>7,}  {u['input']+u['output']:>8,}{extra}")
    print(f"  {sep}")
    print(f"  {'TOTAL':<28}  {total_in:>7,}  {total_out:>7,}  {total_in+total_out:>8,}")
    print(f"{'='*62}\n")


def _validate_memo(memo: dict) -> InvestmentMemo:
    """Validate memo dict via Pydantic. Raises ResearchAgentError on any schema violation."""
    try:
        return InvestmentMemo(**memo)
    except ValidationError as exc:
        raise ResearchAgentError(f"LLM response failed schema validation:\n{exc}")


# ── Update Mode (incremental refresh for held positions) ─────────────────────

def _run_update_mode(ticker: str, macro_context: Optional[str]) -> dict:
    """Incremental research path for held positions with no material event.

    Fetches only news + transcripts, then asks Claude to return only the fields
    that have materially changed. Merges the delta into the existing memo.
    Falls back to full research if no prior memo exists.
    """
    from backend.memory.vector_store import get_memo
    logger.info("run_research(%s): entering update_mode", ticker)
    print(f"\n{'─'*62}")
    print(f"  UPDATE MODE — news + transcripts only ({ticker})")
    print(f"{'─'*62}")

    # Load existing memo — fall back to full research if none found
    existing_raw = get_memo(ticker)
    if not existing_raw or not existing_raw.get("memo_json"):
        logger.info(
            "run_research(%s): update_mode fallback — no existing memo; running full research",
            ticker,
        )
        return run_research(ticker, use_cache=False, update_mode=False)

    existing_memo = existing_raw.get("memo_json", {})
    memo_date = str(existing_raw.get("date", date.today().isoformat()))

    # Fetch only lightweight sources
    new_news = fetch_news(ticker)
    new_transcripts = fetch_transcripts(ticker)

    # Build update prompt
    update_message = _build_update_synthesis_message(existing_memo, new_news, new_transcripts, memo_date)

    client = _build_client()
    usage_log: list[dict] = []
    print(f"\n{'─'*62}")
    print(f"  UPDATE PHASE — incremental synthesis ({ticker})")
    print(f"{'─'*62}")

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        temperature=0.3,
        system=_build_update_system_prompt(),
        messages=[{"role": "user", "content": update_message}],
    )
    raw_content = response.content[0].text if response.content else ""
    usage_log.append({
        "phase": "Update-mode synthesis (Claude)",
        "input": response.usage.input_tokens,
        "output": response.usage.output_tokens,
    })

    # Parse the delta JSON
    cleaned = _strip_code_fences(raw_content)
    try:
        updated_fields = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        updated_fields = json.loads(match.group()) if match else {}

    merged = _merge_updated_fields(existing_memo, updated_fields)
    merged["ticker"] = ticker

    _print_usage_summary(ticker, usage_log)

    # Attach lightweight raw_docs so store_memo can persist them
    merged["_raw_docs"] = {
        "sec": existing_raw.get("raw_docs", {}).get("sec", {}),
        "news": new_news,
        "transcripts": new_transcripts,
        "form4": existing_raw.get("raw_docs", {}).get("form4", {}),
        "fmp": existing_raw.get("raw_docs", {}).get("fmp", {}),
    }
    return merged


# ── Ticker Events Calendar ────────────────────────────────────────────────────

def _populate_ticker_events(ticker: str, sec_data: dict) -> None:
    """Upsert known filing events into ticker_events after a successful SEC fetch.

    Marks document_fetched=True for whatever we just pulled from EDGAR so that
    future runs can skip the API call and load from document_chunks instead.
    Called after Phase 0 in full-research mode (skipped in update_mode).
    Non-fatal — any failure is logged and swallowed.
    """
    from backend.memory.vector_store import _get_client
    try:
        client = _get_client()
        today = date.today().isoformat()
        rows = []

        # Determine fiscal period labels from available data
        # SEC metrics contain the filing date; we use it to derive fiscal period keys
        for form_type, event_type in [("metrics_10k", "annual_filing"), ("metrics_10q", "quarterly_filing")]:
            metrics = sec_data.get(form_type, {})
            if not metrics:
                continue
            # Prefer explicit filing_date from metrics; fall back to today
            filing_date_str = metrics.get("filing_date") or today
            try:
                filing_date = date.fromisoformat(filing_date_str[:10])
            except (ValueError, TypeError):
                filing_date = date.today()
            # Derive fiscal period label: FY{year} for 10-K, Q{q}_{year} for 10-Q
            if event_type == "annual_filing":
                fiscal_period = f"FY{filing_date.year}"
            else:
                quarter = (filing_date.month - 1) // 3 + 1
                fiscal_period = f"Q{quarter}_{filing_date.year}"

            rows.append({
                "ticker": ticker,
                "event_type": event_type,
                "event_date": filing_date_str[:10],
                "fiscal_period": fiscal_period,
                "document_available": True,
                "document_fetched": True,
                "fetched_at": datetime.utcnow().isoformat(),
                "source": "sec_edgar",
            })

        if rows:
            client.table("ticker_events").upsert(
                rows, on_conflict="ticker,event_type,fiscal_period"
            ).execute()
            logger.info("_populate_ticker_events(%s): upserted %d event rows", ticker, len(rows))
    except Exception as exc:
        logger.warning("_populate_ticker_events(%s): failed — %s", ticker, exc)


# ── Update-mode helpers ───────────────────────────────────────────────────────

def _build_update_system_prompt() -> str:
    """Stripped system prompt for incremental memo updates (held positions)."""
    return (
        "You are an equity research analyst updating a prior investment memo with fresh news "
        "and earnings transcript data. The underlying thesis, SEC filings, valuation, and "
        "position sizing have NOT changed — only update fields where the new information "
        "materially changes the view.\n\n"
        "Return a JSON object containing ONLY the fields that have changed. Include all of these "
        "if they need updating: bull_thesis, bear_thesis, key_risks, catalysts, conviction_score, "
        "conviction_score_rationale, red_team_risks, summary.\n\n"
        "Rules:\n"
        "- If a field is unchanged, omit it entirely.\n"
        "- Do NOT return verdict, variant_perception, repricing_catalyst, valuation_note, "
        "price_target, or any position-sizing fields — those require full SEC re-analysis.\n"
        "- conviction_score must remain between 0–10. Only change it if new information "
        "materially shifts the risk/reward.\n"
        "- Return valid JSON only. No markdown fences, no preamble."
    )


def _build_update_synthesis_message(
    existing_memo: dict,
    new_news: dict,
    new_transcripts: dict,
    memo_date: str,
) -> str:
    """Build the user message for an incremental memo update."""
    lines = [
        f"TICKER: {existing_memo.get('ticker', 'UNKNOWN')}",
        f"PRIOR MEMO DATE: {memo_date}",
        f"UPDATE DATE: {date.today().isoformat()}",
        "",
        "═══ PRIOR MEMO SUMMARY ═══",
        f"Verdict: {existing_memo.get('verdict', 'N/A')}",
        f"Conviction: {existing_memo.get('conviction_score', 'N/A')}",
        f"Bull thesis: {existing_memo.get('bull_thesis', 'N/A')}",
        f"Bear thesis: {existing_memo.get('bear_thesis', 'N/A')}",
        f"Key risks: {json.dumps(existing_memo.get('key_risks', []))}",
        f"Catalysts: {json.dumps(existing_memo.get('catalysts', []))}",
        f"Summary: {existing_memo.get('summary', 'N/A')}",
        "",
        "═══ NEW NEWS ═══",
    ]

    articles = new_news.get("articles", [])
    if articles:
        for a in articles[:10]:
            lines.append(f"• [{a.get('published_utc', '')[:10]}] {a.get('headline', '')}")
            if a.get("description"):
                lines.append(f"  {a['description'][:200]}")
    else:
        lines.append("No new news articles.")

    lines += ["", "═══ NEW TRANSCRIPT EXCERPTS ═══"]
    transcripts = new_transcripts.get("transcripts", {})
    if transcripts:
        for qkey, tdata in list(transcripts.items())[:2]:
            lines.append(f"\n{qkey} ({tdata.get('date', 'N/A')}):")
            text = tdata.get("text", "")
            lines.append(text[:3000] + ("…" if len(text) > 3000 else ""))
    else:
        lines.append("No new transcript data.")

    lines += [
        "",
        "Based on the above, return a JSON object with only the fields that need updating. "
        "If nothing material has changed, return an empty JSON object {}.",
    ]
    return "\n".join(lines)


def _merge_updated_fields(existing_memo: dict, updated_fields: dict) -> dict:
    """Merge Claude's incremental update into the existing memo dict."""
    if not updated_fields:
        return existing_memo

    updatable = {
        "bull_thesis", "bear_thesis", "key_risks", "catalysts",
        "conviction_score", "conviction_score_rationale", "red_team_risks", "summary",
    }
    merged = dict(existing_memo)
    for field, value in updated_fields.items():
        if field in updatable:
            merged[field] = value
            logger.info("_merge_updated_fields: updated field '%s'", field)
        else:
            logger.debug("_merge_updated_fields: ignoring non-updatable field '%s'", field)

    merged["_update_mode"] = True
    merged["_update_date"] = date.today().isoformat()
    return merged


# ── Main Entry Point ──────────────────────────────────────────────────────────

def run_research(ticker: str, use_cache: bool = False, update_mode: bool = False) -> dict:
    """
    5-phase hybrid B+D pipeline:
      Phase 0: Fetch all 5 data sources (skipped when use_cache=True).
      Phase 1: Index narrative into pgvector (skipped when use_cache=True).
      Phase 2: Build structured block.
      Phase 3: ReAct agentic retrieval loop (OpenAI; fallback-safe).
      Phase 4: Synthesis call — Claude (claude-sonnet-4-6).
      Phase 5: Red team call — Claude (claude-sonnet-4-6).

    use_cache=True: pulls raw_docs from the most recent Supabase memo for this ticker
    and skips all API fetching and pgvector re-indexing. pgvector chunks from the
    previous run are still queried in Phase 3.

    update_mode=True: incremental path for held positions with no material event.
      - Skips SEC, Form4, FMP fetchers (those don't change between filings).
      - Runs news + transcripts only.
      - Skips ReAct retrieval loop.
      - Asks Claude to return only changed fields and merges them into existing memo.
      - Falls back to full research if no existing memo is found.

    Raises ResearchAgentError on LLM parse / validation failure.
    Phase 1 and Phase 3 failures are non-fatal — degrade gracefully.
    """
    ticker = ticker.upper().strip()
    macro_context = _read_macro_context()  # None if macro pipeline hasn't run yet — non-blocking

    # ── update_mode: incremental path for held positions ──────────────────────
    if update_mode:
        return _run_update_mode(ticker, macro_context)

    # ── Phase 0: Fetch (or load from cache) ──────────────────────────────────
    if use_cache:
        from backend.memory.vector_store import get_memo
        cached = get_memo(ticker)
        if cached and cached.get("raw_docs"):
            raw = cached["raw_docs"]
            sec_data = raw.get("sec", {})
            news_data = raw.get("news", {})
            transcript_data = raw.get("transcripts", {})
            form4_data = raw.get("form4", {})
            fmp_data = raw.get("fmp", {})
            logger.info("run_research(%s): using cached raw_docs (memo id=%s)", ticker, cached.get("id"))
            print(f"\n{'─'*62}")
            print(f"  CACHE MODE — skipping fetch + indexing ({ticker})")
            print(f"{'─'*62}")
            indexing_ok = True  # assume chunks already in pgvector from prior run
        else:
            logger.info("run_research(%s): use_cache=True but no cache found — falling back to full fetch", ticker)
            use_cache = False

    if not use_cache:
        sec_data = fetch_sec_filings(ticker)
        news_data = fetch_news(ticker)
        transcript_data = fetch_transcripts(ticker)
        form4_data = fetch_form4(ticker)
        fmp_data = fetch_fmp(ticker)

        # Populate ticker_events calendar from filing data (non-fatal)
        _populate_ticker_events(ticker, sec_data)

        # ── Phase 1: Index narrative into pgvector ────────────────────────────
        try:
            from backend.memory.document_indexer import index_documents
            n_chunks = index_documents(ticker, sec_data, transcript_data)
            logger.info("run_research(%s): indexed %d chunks", ticker, n_chunks)
            indexing_ok = n_chunks > 0
        except Exception as exc:
            logger.warning(
                "run_research(%s): indexing failed (%s) — falling back to structured-only synthesis",
                ticker, exc,
            )
            indexing_ok = False

    # ── Phase 2: Build structured block ──────────────────────────────────────
    structured_block = _build_structured_block(ticker, sec_data, news_data, form4_data, fmp_data)

    usage_log: list[dict] = []

    # ── Phase 3: ReAct agentic retrieval ─────────────────────────────────────
    retrieved_chunks: list[dict] = []
    if indexing_ok:
        print(f"\n{'─'*62}")
        print(f"  PHASE 3 — ReAct retrieval ({ticker})")
        print(f"{'─'*62}")
        try:
            openai_client = _build_openai_client()
            retrieved_chunks, react_usage = _run_agentic_retrieval(ticker, structured_block, openai_client)
            if react_usage:
                usage_log.append(react_usage)
        except Exception as exc:
            logger.warning(
                "run_research(%s): agentic retrieval failed (%s) — proceeding without retrieved chunks",
                ticker, exc,
            )

    # ── Phase 4: Synthesis (Claude) ───────────────────────────────────────────
    print(f"\n{'─'*62}")
    print(f"  PHASE 4 — Synthesis ({ticker})")
    print(f"{'─'*62}")

    client = _build_client()
    synthesis_message = _build_synthesis_message(ticker, structured_block, retrieved_chunks, macro_context)
    response = client.messages.create(                                                       
      model="claude-sonnet-4-6",                            
      max_tokens=16000,
      temperature=1,           # required for extended thinking                            
      thinking={"type": "enabled", "budget_tokens": 10000},
      system=_build_system_prompt(),                                                       
      messages=[{"role": "user", "content": synthesis_message}],                           
  ) 
    # response = client.messages.create(
    #     model="claude-sonnet-4-6",
    #     max_tokens=8000,
    #     temperature=0.3,
    #     system=_build_system_prompt(),
    #     messages=[{"role": "user", "content": synthesis_message}],
    # )
    raw_content = ""
    for block in response.content:
        if block.type == "thinking":
            print(f"\n{'='*62}\n  SYNTHESIS THINKING — {ticker}\n{'='*62}")
            print(block.thinking)
            print(f"{'='*62}\n")
        elif block.type == "text":
            raw_content = block.text or ""
    usage_log.append({
        "phase": "Synthesis (Claude)",
        "input": response.usage.input_tokens,
        "output": response.usage.output_tokens,
        "cache_write": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
    })

    # ── CLAUDE SWAP ───────────────────────────────────────────────────────────
    # OpenAI synthesis — commented out
    #
    # openai_client = _build_openai_client()
    # oai_response = openai_client.chat.completions.create(
    #     model=OPENAI_MODEL,
    #     messages=[
    #         {"role": "system", "content": _build_system_prompt()},
    #         {"role": "user", "content": synthesis_message},
    #     ],
    #     temperature=0.3,
    #     max_tokens=4000,
    # )
    # raw_content = oai_response.choices[0].message.content or ""
    # if oai_response.usage:
    #     usage_log.append({
    #         "phase": "Synthesis (OpenAI)",
    #         "input": oai_response.usage.prompt_tokens,
    #         "output": oai_response.usage.completion_tokens,
    #     })
    # ─────────────────────────────────────────────────────────────────────────

    cleaned = _strip_code_fences(raw_content)

    # Robust JSON extraction — handles rare cases where model prepends a sentence
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
    if not memo.get("sector"):
        memo["sector"] = fmp_data.get("sector") if isinstance(fmp_data, dict) else None
    validated = _validate_memo(memo)
    result = validated.model_dump(exclude_none=True)

    # ── Phase 5: Red Team ─────────────────────────────────────────────────────
    print(f"\n{'─'*62}")
    print(f"  PHASE 5 — Red team ({ticker})")
    print(f"{'─'*62}")

    _, transcript_signal = _format_transcripts_structured(transcript_data)
    all_negative_turns: list[dict] = []
    for t in transcript_data.get("transcripts", {}).values():
        for turn in t.get("turns", []):
            try:
                sent = float(turn.get("sentiment", 0))
            except (ValueError, TypeError):
                sent = 0.0
            if sent < -0.1:
                all_negative_turns.append(turn)

    raw_context = {
        "metrics": _format_financial_metrics(
            sec_data.get("metrics_10k", {}), sec_data.get("metrics_10q", {})
        ),
        "transcript_signal": transcript_signal,
        "negative_turns": all_negative_turns[:3],
    }
    red_team_risks, rt_usage = _run_red_team(client, result, raw_context)
    if red_team_risks:
        result["red_team_risks"] = red_team_risks
    if rt_usage:
        usage_log.append(rt_usage)

    # ── Token usage summary ───────────────────────────────────────────────────
    _print_usage_summary(ticker, usage_log)

    result["_raw_docs"] = {
        "sec": sec_data,
        "news": news_data,
        "transcripts": transcript_data,
        "form4": form4_data,
        "fmp": fmp_data,
    }

    return result
