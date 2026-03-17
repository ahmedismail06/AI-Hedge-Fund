"""
Research Agent
Orchestrates the 3 fetchers → builds LLM prompt → calls Claude → returns structured memo.

NOTE: Azure OpenAI integration is commented out below (search "CLAUDE SWAP").
      To revert to Azure: comment out the anthropic import/client block,
      uncomment the AzureOpenAI block, and swap the API calls back.
"""

import anthropic
import json
import os
import re
from datetime import date
from dotenv import load_dotenv
# from openai import AzureOpenAI  # CLAUDE SWAP: Azure OpenAI — commented out
from pydantic import ValidationError

from backend.fetchers.sec_fetcher import fetch_sec_filings
from backend.fetchers.news_fetcher import fetch_news
from backend.fetchers.transcript_fetcher import fetch_transcripts
from backend.fetchers.form4_fetcher import fetch_form4
from backend.fetchers.fmp_fetcher import fetch_fmp
from backend.models import InvestmentMemo

load_dotenv()


class ResearchAgentError(Exception):
    pass


# ── LLM Client ───────────────────────────────────────────────────────────────

# ACTIVE: Claude API (claude-sonnet-4-6)
def _build_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY")
    )

# ── CLAUDE SWAP ───────────────────────────────────────────────────────────────
# Azure OpenAI (GPT-4.1) — commented out. To revert, uncomment below and
# comment out the Claude client above. Also swap the API calls in
# run_research() and _run_red_team() below.
#
# def _build_client() -> AzureOpenAI:
#     return AzureOpenAI(
#         api_key=os.getenv("AZURE_OPENAI_API_KEY"),
#         azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
#         api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
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


def _build_user_message(
    ticker: str,
    sec: dict,
    news: dict,
    transcripts: dict,
    form4: dict,
    fmp: dict,
) -> tuple[str, str]:
    """
    Build the user message for the main LLM call.
    Returns (user_message, transcript_signal_summary).
    """
    transcript_text, transcript_signal = _format_transcripts_structured(transcripts)

    # Bug 12: Three independent cash sources — SEC regex, yfinance, Polygon — can
    # contradict each other silently. Build a reconciliation note so the LLM sees
    # the discrepancy explicitly rather than picking the last successful value.
    cash_reconciliation = _build_cash_reconciliation(
        sec.get("metrics_10k", {}), sec.get("metrics_10q", {}), fmp
    )

    msg = f"""Analyze {ticker.upper()} and produce a structured investment memo.

{_format_financial_metrics(sec.get('metrics_10k', {}), sec.get('metrics_10q', {}))}

{_format_insider_buying(form4)}

{_format_market_intelligence(fmp)}
{cash_reconciliation}

=== SEC FILINGS ===
{_format_sec(sec)}

=== EARNINGS CALL TRANSCRIPTS ===
{transcript_text}

=== RECENT NEWS (last 30 days) ===
{_format_news(news)}

Respond with a single valid JSON object. No markdown, no code fences, no explanatory text — pure JSON only.
Use today's date ({date.today().isoformat()}) for the "date" field.
"""
    return msg, transcript_signal


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


def _run_red_team(client, memo: dict, raw_context: dict) -> list[str]:
    """
    Second LLM call: adversarial critique of the bull thesis.
    Returns a list of 5 risk strings, or an empty list on failure (never blocks the main memo).
    """
    try:
        # ACTIVE: Claude API call
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            temperature=0.5,  # slightly higher — adversarial creativity benefits from more variance
            system=_build_red_team_system_prompt(),
            messages=[{"role": "user", "content": _build_red_team_user_message(memo, raw_context)}],
        )
        raw = response.content[0].text or ""

        # ── CLAUDE SWAP ────────────────────────────────────────────────────────
        # Azure OpenAI (GPT-4.1) — commented out
        # response = client.chat.completions.create(
        #     model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
        #     messages=[
        #         {"role": "system", "content": _build_red_team_system_prompt()},
        #         {"role": "user", "content": _build_red_team_user_message(memo, raw_context)},
        #     ],
        #     temperature=0.5,
        #     max_tokens=2000,
        # )
        # raw = response.choices[0].message.content or ""
        # ──────────────────────────────────────────────────────────────────────

        cleaned = _strip_code_fences(raw)
        parsed = json.loads(cleaned)
        risks = parsed.get("red_team_risks", [])
        if isinstance(risks, list) and all(isinstance(r, str) for r in risks):
            return risks
        return []
    except Exception:
        # Red team failure must never block the main memo from being returned
        return []


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
    Full pipeline: fetch data → build prompt → call Claude → validate → return memo dict.
    Attaches raw fetcher outputs as '_raw_docs' key (stripped before DB insert in vector_store).
    Raises ResearchAgentError on LLM parse / validation failure.
    """
    ticker = ticker.upper().strip()

    sec_data = fetch_sec_filings(ticker)
    news_data = fetch_news(ticker)
    transcript_data = fetch_transcripts(ticker)
    form4_data = fetch_form4(ticker)
    fmp_data = fetch_fmp(ticker)

    client = _build_client()
    user_message, transcript_signal = _build_user_message(
        ticker, sec_data, news_data, transcript_data, form4_data, fmp_data
    )

    # ACTIVE: Claude API call
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        temperature=0.3,
        system=_build_system_prompt(),
        messages=[
            {"role": "user", "content": user_message},
        ],
    )
    raw_content = response.content[0].text or ""

    # ── CLAUDE SWAP ───────────────────────────────────────────────────────────
    # Azure OpenAI (GPT-4.1) — commented out. Note: Azure uses messages[] for
    # the system prompt, Claude uses system= as a top-level param.
    #
    # response = client.chat.completions.create(
    #     model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
    #     messages=[
    #         {"role": "system", "content": _build_system_prompt()},
    #         {"role": "user", "content": user_message},
    #     ],
    #     temperature=0.3,
    #     max_tokens=4000,
    # )
    # raw_content = response.choices[0].message.content or ""
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

    # ── Red Team: second adversarial LLM call ─────────────────────────────────
    # Collect negative management turns across all transcripts for red team context
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
    red_team_risks = _run_red_team(client, result, raw_context)
    if red_team_risks:
        result["red_team_risks"] = red_team_risks
    # ─────────────────────────────────────────────────────────────────────────

    result["_raw_docs"] = {
        "sec": sec_data,
        "news": news_data,
        "transcripts": transcript_data,
        "form4": form4_data,
        "fmp": fmp_data,
    }

    return result