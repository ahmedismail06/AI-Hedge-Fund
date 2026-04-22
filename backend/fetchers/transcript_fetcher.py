"""
Transcript Fetcher
Fetches the 2 most recent earnings call transcripts from Alpha Vantage.
Tries quarters in descending order until 2 transcripts are collected.
Free tier: 25 requests/day. Each ticker costs 1 request per quarter tried.

Efficiency improvements (2026-04-10):
  - ticker_events cache check: if document_fetched=True in Supabase, loads chunks
    from document_chunks and skips the Alpha Vantage API call.
  - Supabase-backed AV daily counter: persists across process restarts so the 25/day
    quota is enforced even when uvicorn reloads. In-memory dict is used as a
    session cache to avoid a Supabase round-trip on every quarter probe.
  - After a successful AV fetch, marks document_fetched=True in ticker_events so
    future runs for the same ticker+quarter are served from cache.
"""

import datetime
import logging
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

AV_BASE = "https://www.alphavantage.co/query"
AV_PER_KEY_LIMIT = 25  # Free-tier limit per Alpha Vantage API key
AV_DAILY_LIMIT = AV_PER_KEY_LIMIT  # baseline for one configured key
AV_BUDGET_WARNING_THRESHOLD = 5  # Log warning when this many requests remain

# Quarters to probe, most-recent first. Generates the last 8 quarters dynamically.
# Prioritizes the latest earnings call event found in ticker_events.
def _get_quarters_to_try(ticker: str = None, num_quarters: int = 8) -> list[str]:
    anchor_year = None
    anchor_quarter = None

    if ticker:
        try:
            from backend.memory.vector_store import _get_client
            client = _get_client()
            # Look for the most recent earnings call event
            row = (
                client.table("ticker_events")
                .select("fiscal_period,event_date")
                .eq("ticker", ticker.upper())
                .eq("event_type", "earnings_call")
                .order("event_date", desc=True)
                .limit(1)
                .execute()
                .data
            )
            if row and row[0].get("fiscal_period") and row[0].get("event_date"):
                fp = row[0]["fiscal_period"]  # e.g. 'Q1_2026'
                event_date_str = row[0]["event_date"]
                
                # If the event date is in the future, this quarter's transcript 
                # definitely won't be on Alpha Vantage yet.
                today = datetime.datetime.now(datetime.timezone.utc).date()
                event_date = datetime.date.fromisoformat(event_date_str)
                
                if "_" in fp and fp.startswith("Q"):
                    parts = fp.split("_")
                    q = int(parts[0][1])
                    y = int(parts[1])
                    
                    if event_date > today:
                        # Event hasn't happened yet — anchor to the previous quarter
                        logger.debug("_get_quarters_to_try(%s): %s is in future (%s), skipping anchor", ticker, fp, event_date_str)
                        q -= 1
                        if q == 0:
                            q = 4
                            y -= 1
                    
                    anchor_quarter = q
                    anchor_year = y
                    logger.debug("_get_quarters_to_try(%s): using anchor Q%d_%d", ticker, anchor_quarter, anchor_year)
        except Exception as exc:
            logger.debug("_get_quarters_to_try(%s): ticker_events lookup failed: %s", ticker, exc)

    # Fallback: start from the most recently completed quarter based on current date
    if anchor_year is None:
        now = datetime.datetime.now(datetime.timezone.utc)
        anchor_year = now.year
        # (now.month - 1) // 3 gives 0 for Jan-Mar, 1 for Apr-Jun, etc.
        anchor_quarter = (now.month - 1) // 3
        if anchor_quarter == 0:
            anchor_quarter = 4
            anchor_year -= 1
        logger.debug("_get_quarters_to_try(%s): using fallback anchor %dQ%d", ticker, anchor_year, anchor_quarter)
    
    quarters = []
    curr_q = anchor_quarter
    curr_y = anchor_year
    for _ in range(num_quarters):
        quarters.append(f"{curr_y}Q{curr_q}")
        curr_q -= 1
        if curr_q == 0:
            curr_q = 4
            curr_y -= 1
    return quarters


# In-process session cache — reduces Supabase round-trips within a single run.
# Supabase is the source of truth; this is only populated by _load_av_session_count().
_av_session: dict = {"date": None, "count": 0, "loaded": False}


def _load_av_session_count() -> int:
    """Read today's AV request count from Supabase into the session cache.

    Returns the current count (before any increment). Safe to call multiple
    times — subsequent calls within the same day return the cached value.
    """
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    if _av_session["loaded"] and _av_session["date"] == today:
        return _av_session["count"]

    try:
        from backend.memory.vector_store import _get_client
        client = _get_client()
        row = (
            client.table("pm_config")
            .select("av_daily_count,av_daily_date")
            .eq("id", 1)
            .single()
            .execute()
            .data
        )
        if row and str(row.get("av_daily_date") or "") == today:
            count = int(row.get("av_daily_count", 0))
        else:
            count = 0  # new day or first use
    except Exception as exc:
        logger.warning("_load_av_session_count: Supabase read failed — %s; using 0", exc)
        count = 0

    _av_session.update({"date": today, "count": count, "loaded": True})
    return count


def _persist_av_count(count: int) -> None:
    """Write the current AV request count back to Supabase."""
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    try:
        from backend.memory.vector_store import _get_client
        client = _get_client()
        client.table("pm_config").update(
            {"av_daily_count": count, "av_daily_date": today}
        ).eq("id", 1).execute()
    except Exception as exc:
        logger.warning("_persist_av_count: Supabase write failed — %s", exc)


def _av_request_allowed() -> bool:
    """Return True if we are under the daily AV request budget.

    Does not increment the counter — call _increment_av_count() after success.
    """
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    if _av_session["date"] != today:
        _av_session.update({"date": today, "count": 0, "loaded": False})
    return _av_session["count"] < _get_av_daily_limit()


def av_requests_remaining() -> int:
    """How many AV requests are left today (useful for logging in multi-ticker runs)."""
    today = datetime.date.today().isoformat()
    if _av_session["date"] != today or not _av_session["loaded"]:
        _load_av_session_count()
    return max(0, _get_av_daily_limit() - _av_session["count"])


def _get_av_api_keys() -> list[str]:
    """Return all configured numbered Alpha Vantage API keys in numeric order."""
    numbered_keys = []
    for name, value in os.environ.items():
        if not name.startswith("ALPHA_VANTAGE_API_KEY_"):
            continue
        try:
            index = int(name.split("_")[-1])
        except ValueError:
            continue
        if value and value.strip():
            numbered_keys.append((index, value.strip()))
    numbered_keys.sort(key=lambda pair: pair[0])
    return [key for _, key in numbered_keys]


def _get_av_daily_limit() -> int:
    """Compute the total Alpha Vantage daily limit from configured keys."""
    api_keys = _get_av_api_keys()
    return len(api_keys) * AV_PER_KEY_LIMIT


# ── ticker_events cache helpers ───────────────────────────────────────────────

def _quarter_str_to_fiscal_period(quarter_str: str) -> str:
    """Convert '2025Q4' → 'Q4_2025' (fiscal_period format used in ticker_events)."""
    year = quarter_str[:4]
    q = quarter_str[5]
    return f"Q{q}_{year}"


def _transcript_cached(ticker: str, quarter_str: str) -> dict | None:
    """Check ticker_events for a cached transcript for this ticker + quarter.

    Returns a transcript dict compatible with fetch_transcripts() output format
    if the document has been fetched before (document_fetched=True in ticker_events
    AND the chunks exist in document_chunks). Returns None if no cache hit.
    """
    fiscal_period = _quarter_str_to_fiscal_period(quarter_str)
    try:
        from backend.memory.vector_store import _get_client, search_similar
        client = _get_client()
        row = (
            client.table("ticker_events")
            .select("document_fetched,source,event_date")
            .eq("ticker", ticker)
            .eq("fiscal_period", fiscal_period)
            .eq("event_type", "earnings_call")
            .eq("document_fetched", True)
            .limit(1)
            .execute()
            .data
        )
        if not row:
            return None

        # Confirmed cached — load text from document_chunks
        chunks = search_similar(
            ticker=ticker,
            query=f"earnings call {fiscal_period}",
            doc_types=["transcript"],
        )
        if not chunks:
            return None

        combined_text = "\n\n".join(c.get("content", "") for c in chunks)
        year_int = int(quarter_str[:4])
        quarter_int = int(quarter_str[5])
        logger.info(
            "_transcript_cached: cache hit for %s %s (source=%s)",
            ticker, fiscal_period, row[0].get("source", "cached"),
        )
        return {
            "quarter": quarter_int,
            "year": year_int,
            "date": row[0].get("event_date"),
            "text": combined_text,
            "turns": [],  # chunks don't retain structured turns
            "_from_cache": True,
        }
    except Exception as exc:
        logger.warning("_transcript_cached: check failed for %s %s — %s", ticker, quarter_str, exc)
        return None


def _mark_transcript_fetched(ticker: str, quarter_str: str, event_date: str | None) -> None:
    """Mark a transcript as fetched in ticker_events after a successful AV call."""
    fiscal_period = _quarter_str_to_fiscal_period(quarter_str)
    try:
        from backend.memory.vector_store import _get_client
        client = _get_client()
        client.table("ticker_events").upsert(
            {
                "ticker": ticker,
                "event_type": "earnings_call",
                "fiscal_period": fiscal_period,
                "event_date": event_date,
                "document_available": True,
                "document_fetched": True,
                "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "source": "alpha_vantage",
            },
            on_conflict="ticker,event_type,fiscal_period",
        ).execute()
    except Exception as exc:
        logger.warning("_mark_transcript_fetched: failed for %s %s — %s", ticker, quarter_str, exc)


def _turns_to_text(turns: list) -> str:
    """Flatten AV structured turns into a readable transcript string."""
    lines = []
    for turn in turns:
        speaker = turn.get("speaker", "")
        title = turn.get("title", "")
        content = turn.get("content", "")
        header = f"{speaker} ({title})" if title and title != speaker else speaker
        lines.append(f"{header}: {content}")
    return "\n\n".join(lines)


def fetch_transcripts(ticker: str) -> dict:
    """
    Returns:
    {
        "ticker": str,
        "transcripts": {
            "Q4_2025": {
                "quarter": int,       # e.g. 4
                "year": int,          # e.g. 2025
                "date": str | None,
                "text": str,          # flat speaker-joined transcript
                "turns": list         # raw AV turn dicts with per-turn sentiment
            },
            ...
        },
        "fetched_count": int,
        "warning": None | "warning message"
    }
    Never raises — missing transcripts produce an empty dict + warning.
    """
    result: dict = {
        "ticker": ticker.upper(),
        "transcripts": {},
        "fetched_count": 0,
        "warning": None,
    }

    # ── MANUAL TRANSCRIPT OVERRIDE ────────────────────────────────────────────
    # Bug 8: this block must run BEFORE the api_key guard so that manual
    # transcripts work even when numbered ALPHA_VANTAGE_API_KEY_<n> vars are not set.
    # Drop a raw earnings call transcript here to bypass the API entirely.
    # Useful for testing, for tickers AV doesn't cover, or for pasting in
    # a transcript before AV has indexed it (AV typically lags 1–3 days).
    #
    # HOW TO USE:
    #   1. Paste the transcript text into MANUAL_TRANSCRIPTS below as a string.
    #   2. Set the ticker key to uppercase (e.g. "PRCT").
    #   3. Run the pipeline — the manual text will be sent to the LLM instead.
    #   4. Remove or clear the entry when AV starts returning live data.
    #
    # MANUAL_TRANSCRIPTS: dict[ticker -> list of {quarter, year, date, text}]
    MANUAL_TRANSCRIPTS: dict = {
        "PRCT": [
            {
                "quarter": 4,
                "year": 2025,
                "date": "2026-02-25",
                "text": """
                [Transcript
Operator (participant)
Good afternoon, welcome to the PROCEPT BioRobotics fourth quarter 2025 earnings conference call. At this time, all participants are in a listen-only mode. We will be facilitating a question-and-answer session toward the end of today's call. As a reminder, this call is being recorded for replay purposes. I would now like to turn the conference over to Matt Bacso, Vice President, Investor Relations, for a few introductory comments. Please go ahead.

Matt Bacso (VP of Investor Relations)
Good afternoon, and thank you for joining PROCEPT BioRobotics fourth quarter 2025 earnings conference call. Presenting on today's call are Larry Wood, Chief Executive Officer, and Kevin Waters, Chief Financial Officer. Before we begin, I'd like to remind listeners that statements made on this conference call that relate to future plans, events, or performance are forward-looking statements as defined under the Private Securities Litigation Reform Act of 1995. While these forward-looking statements are based on management's current expectations and beliefs, these statements are subject to several risks, uncertainties, assumptions, and other factors that could cause results to differ materially from the expectations expressed on this conference call. These risks and uncertainties are disclosed in more detail in PROCEPT BioRobotics filings with the Securities Exchange Commission, all of which are available online at www.sec.gov.

Listeners are cautioned not to place under reliance on these forward-looking statements, which speak only as of today's date, February 24th, 2026. Except as required by law, PROCEPT BioRobotics undertakes no obligation to update or revise any forward-looking statements to reflect new information, circumstances, or unanticipated events that may arise. During the call, we'll also reference certain financial measures that are not prepared in accordance with GAAP. More information about how we use these non-GAAP financial measures, as well as reconciliations of these measures to their nearest GAAP equivalent, are included in our earnings release. I would like to turn the call over to Larry.

Larry Wood (CEO)
Thanks, Matt. Before discussing our fourth quarter results, I want to share context on progress since joining the company as CEO. When I joined Procept, I outlined an immediate near-term plan for the organization that I believed was critical to positioning the company for its next chapter. It was essential to move with a clear vision, a strong sense of urgency, and a culture grounded in discipline and accountability. Historically, Procept executed effectively in its first chapter of growth. That work created the foundation the company benefits from today. As the company evolves, so do the requirements for success. The next stage of Procept's development requires shifting the operational focus towards increasing procedure volume, expanding margins, and achieving profitability and gaining market share. At the same time, we must deliberately build an organization that supports both near-term performance and long-term sustainable growth.

We recently made two changes to our commercial organization that we believe are strategically important for long-term performance. First, we have realigned our commercial team into an integrated regional structure, where our clinical and sales functions now report to a common regional leader. The new structure creates a single point of accountability at the regional level to ensure clinical and commercial activities are coordinated around customer success and procedure growth. Second, we formed a dedicated launch team by reassigning a small number of our top performers to focus specifically on new system placements. The intent is to drive more consistent launches, reduce variability and activation, and accelerate time to value for customers, because we see launches as a key lever to improving downstream utilization and performance. In the near term, the sales realignment and formulation of the launch team creates some short-term disruption.

Certain account coverage has changed, and temporarily, we have fewer tenured resources in the field as we stand up a launch team. We view this as a normal transition period as teams ramp, establish account relationships, and standardize new operating processes. Importantly, we believe these changes better position us for sustained high growth through clearer leadership, better alignment, and more repeatable launches. We will continue to manage through this transition thoughtfully, and we expect the benefits to build as the organization settles into the new model. Now, turning to fourth quarter results. In the fourth quarter, we completed 12,200 procedures, reflecting approximately 69% annual growth. On the third quarter earnings call, we reduced our previously issued Q4 guidance by 1,000 handpiece units as we reestablished customer inventory targets that we felt were appropriate based on usage volume.

Separate from establishing inventory targets, it became clear as the quarter progressed that accounts had become accustomed to purchasing large quantities of handpieces and receiving bulk discounts in the final weeks of the quarter. I've always believed pricing discipline is foundational to long-term success. At PROCEPT, I've been focused on implementation of handpiece price discipline, and as part of that, we eliminated the historical practice of providing discounts on bulk purchases, particularly at the end of the quarter. Despite customer requests, we remained disciplined and did not allow bulk purchases at a discount. As a result, handpiece unit sales were approximately 80% of procedures in the fourth quarter, and for the first time, procedures exceeded handpieces sold. While this resulted in lower than expected revenue, it delivered a significant improvement in handpiece selling price.

Average fourth quarter selling price was $3,340, or up $140, or approximately 5% sequentially from the third quarter. Historically, handpiece unit sales exceeded procedure volumes by approximately 8%-16%. Based on the last several months, we now expect handpiece unit sales and procedure volumes to be in close alignment on a go-forward basis, with sustained improvement in handpiece average selling prices. These business practice changes resulted in a reduction of our projected 2026 handpiece revenue. The revenue impact is meaningfully offset by the increase in handpiece average selling prices. Based on the combination of these factors, with the short-term disruption associated with the salesforce realignment, we are now resetting 2026 guidance to $390 million-$410 million, representing annual growth of 27%-33%.

Before I turn it over to Kevin to walk through the financials, I want to close by previewing what to expect at our Investor Day tomorrow morning. For the first time since the IPO nearly five years ago, we will provide a more detailed multi-year look at our financial guidance, including more details on 2026 and 2027, our path to profitability, and an update on the WATER IV prostate cancer trial, as well as a vision for our future. I hope to see everyone there. With that, I'll hand it over to Kevin to walk through the financials for the quarter. Kevin?

Kevin Waters (CFO)
Thanks, Larry. Total revenue for the fourth quarter of 2025 was $76.4 million, representing 12% year-over-year growth. U.S. revenue for the quarter was $66.6 million, reflecting 10% growth compared to the prior year period. Turning to U.S. procedures. As noted by Larry, we completed approximately 12,200 U.S. procedures in the fourth quarter of 2025, representing approximately 69% year-over-year growth. handpieces sold totaled 9,400 units at an average selling price of approximately $3,340 during the quarter, reflecting a 5% price increase compared to the third quarter of 2025. Other consumable revenue totaled $2.3 million in the fourth quarter.

As a result, total U.S. handpiece and other consumable revenue was $34 million in the fourth quarter of 2025, representing 16% growth compared to the fourth quarter of 2024. Turning to U.S. robot placements. In the fourth quarter, we sold 65 new HYDROS systems. At the end of 2025, we had an installed base of 718 systems, representing a 42% increase compared to year-end 2024. Total U.S. system revenue was $27.6 million in the fourth quarter, comparable to the prior year period, with systems sold at an Average Selling Price of approximately $425,000. International revenue in the fourth quarter of 2025 was $9.8 million, representing year-over-year growth of 25%. Moving down the income statement.

Gross margin for the fourth quarter of 2025 was 60.6%, compared to 64% in the fourth quarter of 2024. The approximate 450 basis point shortfall compared to fourth quarter guidance was driven primarily by lower-than-expected U.S. consumable revenue, as well as a one-time voluntary field action that contributed approximately 240 basis points of pressure. On a full year basis, 2025 gross margin was 63.7%, compared to 61.1% in 2024. Total operating expenses for the fourth quarter of 2025 were $77.4 million, compared to $63.4 million in the prior year period.

The increase reflects continued investment to support commercial expansion, continued innovation across our BPH platform technology, and increased funding for our WATER IV prostate cancer trial, positioning us to drive long-term growth and expand our clinical and technology leadership. Net loss for the fourth quarter of 2025 was $29.8 million, compared to a net loss of $18.9 million in the fourth quarter of 2024. Adjusted EBITDA was a loss of $19 million in the fourth quarter of 2025, compared to a loss of $10.3 million in the prior year period. Cash, cash equivalents and restricted cash totaled $285 million as of December 31st, 2025, providing a strong balance sheet to support our strategic priorities. Moving to our 2026 financial guidance.

We now expect full year 2026 total revenue to be in the range of approximately $390 million-$410 million, representing growth of approximately 27%-33% compared to 2025. This guidance range assumes international revenue to be in the range of $50 million-$51 million. Additionally, we now expect 2026 total U.S. procedures to be in the range of 60,000-64,000, representing growth of approximately 39%-48%. As Larry noted, the adjustment to our 2026 revenue guidance is driven by a few factors. As a result of our business practice changes, we now expect handpiece unit sales to be closely aligned with procedure volumes, which results in a reduction in 2026 handpiece revenue.

This revenue reduction is meaningfully offset by the increase in U.S. handpiece average selling prices, which we now estimate to be $3,500 in 2026. Our updated guidance incorporates both factors above, in addition to the short-term disruption of our sales organization, as discussed by Larry. Importantly, our 2026 outlook does not change our confidence in the company's long-term growth and profitability trajectory through 2026 and 2027. Turning to gross margins. We expect full year 2026 gross margin to be approximately 65%, which includes $5 million-$6 million of tariff expense, compared to $1.3 million in fiscal 2025, which is an approximate 100 basis point headwind to 2026. Turning to operating expenses. We expect full year 2026 operating expenses to total $350 million, representing a 17% increase compared to 2025.

After considering all relevant factors, we expect full year 2026 Adjusted EBITDA loss to be in the range of $30 million-$17 million. Our revised revenue guidance reflects positive EBITDA in the fourth quarter of 2026 at both the low and high ends of the revenue range. For the first quarter, we expect total U.S. procedures to be in the range of 12,000-12,800, representing growth of 29%-37%. This anticipates the implementation of multiple commercial initiatives designed to drive more durable and sustainable procedure growth. As these initiatives take hold, we expect procedures to accelerate, reaching growth of over 50% in the second half of the year compared to fiscal 2025. We expect total revenues for the first quarter of 2026 of $79 million-$82 million, representing growth of 14%-19%.

Included in our total first quarter revenue guidance is U.S. system revenue of approximately $20 million and $10 million of international revenue. I would now like to pass it back to Larry for closing comments.

Larry Wood (CEO)
Thanks, Kevin. While financial performance in the fourth quarter was lower than anticipated, the changes we have made are critical to driving sustainable high growth and paving a clear path to profitability. We are very excited to share more details on 2026 and beyond at our investor conference tomorrow morning at 8:00 A.M. Eastern. With that, we are happy to take questions. Operator?

Operator (participant)
Thank you. At this time, we will conduct the question-and-answer session. As a reminder, to ask a question, you will need to press star one one on your telephone and wait for your name to be announced. To withdraw your question, please press star one one again. Please stand by while we compile the Q&A roster. Our first question comes from Matthew O'Brien with Piper Sandler. The floor is yours.

Matthew O'Brien (Managing Director and Senior Research Analyst)
Afternoon. Thanks for taking the questions. I think we can ask two. The first one up front here is, you know, just I think, Larry Wood, everybody knew that the quarter was going to be soft on the handpiece side, but the level of softness here just wasn't anticipated. Maybe just talk a little bit more about what unfolded in Q4. Specifically, did you flush, just looking at some of the math, did you flush about 4,000 handpieces in Q4 on the inventory side? I do have a follow-up.

Larry Wood (CEO)
Thanks, Matt. Well, first, I just say we're dealing with two distinctly different dynamics. The first was we had signaled on the Q3 call that we expect there to be some destocking, but that was really about establishing par levels for accounts based on their usage. I think directionally, that number was still pretty sound. The thing that came to light later in the quarter was how much our business practices of allowing bulk purchases at a discount was influencing trans- customer purchase behavior. When we did a deep review of that, I just didn't think it made sense for us on a go-forward to be running that practice and discounting that way. I think without that incentive, customers no longer did they be bulk purchasing, and, you know, that's obviously what contributed to the revenue mix.

I think the big thing is it had two positive structural effects for us. The first one was the obvious one of ASP. We saw our ASP increase to about $3,340 in the quarter. The other thing, it's going to improve our quality and predictability of revenue by aligning shipments more closely with underlying procedure volumes. You know, the health of our business was never going to be defined on customer stocking patterns or bulk purchases. It's always going to be about our procedure growth, that's really what we focused on. Yeah, there was a lot more reduction in inventory. I think our handpiece sales were about, I think, I don't know, 77% of procedure volume.

You know, I think you can do the math on that and get to the number of units that came out. I think the big thing for us is, as we look at 2026, we're now modeling those being at about a one-to-one ratio, and we're modeling an ASP of about $3,500, which is about a 9% improvement over where we were in 2025. These are the structural, foundational, fundamental things that I just feel we have to do to really ensure our path to profitability in the time frames that we want to be.

Matthew O'Brien (Managing Director and Senior Research Analyst)
Okay, appreciate that. As far as the guide goes for 2026, it's obviously back-end loaded. You know, as I'm looking at the Q1 commentary, it's just, it's still, you know, it's the toughest half of the year as far as handpieces go, but, you know, it's pretty modest. It just seemed like the impact on the commercial reorg is still going to be influencing Q1. I guess, why such confidence that you're going to see this benefit towards the back half of the year? You know, because I just don't I'm just hoping we don't have to cut the expectation for the full year again. Thanks so much.

Larry Wood (CEO)
Yeah. No, thanks, Matt. I understand your question completely. We put a range in, you know, to try to give guidance on where we think we're going to be in Q1. I think, you know, the Q1 always starts a little bit slow coming out of the holidays. That's always something that we have, and I think I've seen that in, you know, previous companies as well. I think the other thing, though, is we did just signal that as the sales force matures into the new alignment and as they rebuild relationships with customers, you know, we have people covering different accounts. We just wanted to signal that there's, you know, that's going to take a little bit of time for it to mature. We do think these are going to pay dividends to us.

We do think having people that are just dedicated solely on procedure growth in their territories, and they're no longer distracted by launches, and then having dedicated launch teams, we do feel that that's going to pay benefits, but those are going to show up more in the back half of the year rather than the front half of the year. We're gonna, you know, we're gonna provide a lot more detail tomorrow. We're going to walk across the procedure walk, you know, we're going to be completely transparent about it. I know we've had a talk before about really procedure volumes. We focused on handpiece revenue, but tomorrow, we're going to walk through all of that in detail, and I think give you all the components of it.

I think, you know, you'll be able to make informed decisions about how confident you could be in our plan.

Kevin Waters (CFO)
I just want to follow on to Larry here, Matt, this is Kevin, and we're going to go through this, as Larry mentioned tomorrow, to give a full cohort analysis into your concern or question around the low end of the range. I think we're going to provide everybody with comfort that at the low end of the range, we are only expecting very modest utilization growth in our legacy install base. We're actually going to show you that tomorrow to directly answer kind of your concern that you just brought up.

Matthew O'Brien (Managing Director and Senior Research Analyst)
Okay, thanks so much.

Operator (participant)
Thank you for your question. Our next question comes from the line of Chris Pasquale from Nephron Research. The floor is yours.

Chris Pasquale (Partner and Senior Analyst of Medical Devices and Supplies)
Thanks. It looks like handpiece sales exceeded procedure volumes by a little over 10,000 units over the past three years, including this quarter's drawdown. What gives you confidence that the ratio is going to be one-to-one in 2026? Why shouldn't the rest of that gap need to be closed?

Larry Wood (CEO)
Yeah, thanks for your question. I think there's a couple of things here. You know, when we look at the history here, handpiece sales, you know, have been about 108%-115% of procedure volume, and now we're modeling that at one-to-one. We're modeling it one-to-one, even though that we're going to increase our installed base by a couple of 100 systems that are all going to have to take inventory and take stocking orders and do all those things as we expand our installed base. Even with that, you know, we're modeling it a one-to-one.

You know, based on all of our analysis and assessments, I think there's probably more upside to that number than downside, but I think one-to-one is where we're modeling it at, and that's a significant change from how we've done all of our previous modeling. That actually is probably the biggest impact to the reduction in guidance. If we would have modeled handpiece sales at 110% even of procedures like we historically had, then that would have been worth a little over $20 million, probably $20 million-$22 million. We're able to offset a lot of that with the price increase. Again, I think the long-term health of our business is going to be focusing on procedure growth and having steady, stable revenue.

The other thing I can say is, you know, we made this change in the, in the fourth quarter of, you know, pretty much the last month. We have about eight or nine weeks of runway under this new business practice, and we continue to see now handpiece sales and procedures pretty much flying in formation. I think that's what gives us confidence that the one-to-one ratio is going to be appropriate for 2026.

Chris Pasquale (Partner and Senior Analyst of Medical Devices and Supplies)
Okay. Kevin, you talked about the gross margin impact of a field action in the quarter. Could you just give us some details around what that was and as that impact is contained to the fourth quarter?

Larry Wood (CEO)
Yeah, I'll start with the field action, and here's what it was. It was a one-time, non-recurring field action. There were no patient safety issues. There were no concerns. It had to do with compatibility between the handpiece and between the system itself. What we did was we were just able to go to a field upgrade that just took that issue off the table for us. We've upgraded our systems and made the appropriate changes. That was contained in the fourth quarter. Kevin, do you want to walk through the math on it?

Kevin Waters (CFO)
Yeah, it was, approximately $1.5 million, which was 240 basis points of pressure is the math. As Larry said, one time, and it will not impact us moving forward.

Chris Pasquale (Partner and Senior Analyst of Medical Devices and Supplies)
Thank you.

Operator (participant)
Thank you for your question. Our next question comes from Josh Jennings of TD Cowen. The floor is yours.

Josh Jennings (Managing Director and Senior Analyst)
Hi. Thank you for taking the questions. I was hoping to just get a better understanding of the fourth quarter dynamics and the go-forward outlook, just on ending these end of quarter bulk purchase deals that were offered previously. Are you seeing any customer dissatisfaction? Do you anticipate that some high volume or medium volume and low volume centers will decrease their utilization, at least in the short term, until these higher handpiece prices are digested?

Larry Wood (CEO)
Yeah. Thanks, Josh. We don't anticipate that. We haven't, you know, we haven't seen that. I think there were some customers, frankly, in December that were waiting us out to see if we would bring back these incentives before the end of the quarter. We didn't. We've seen the ordering patterns. You know, certainly in Q1 and even late last year, people were having to reorder to support the cases that we're doing. I don't think it had any utilization or our case volume. We haven't heard anything about that. We're just again, really focused on being disciplined about this. You know, again, we're fairly deep in Q1, and I just don't think that's impacted us.

I think there, you know, was a little bit of a mindset in the company that, you know, if we, if people, you know, took these orders and the idea of bulk discounts isn't unique to PROCEPT or anything else. I think people thought, like, if they have much more handpieces, maybe that would be an incentive for utilization. I just don't think the two are related at all. You know, we're going to continue to drive our procedure growth, and that's going to be our key area of focus. That's why we made the changes to the sales force. We're going to be very disciplined about handpiece pricing, and, you know, we're going to be disciplined about system pricing as well.

Josh Jennings (Managing Director and Senior Analyst)
Understood. You took, you made some comments, Larry, just on the some disruption just in the commercial org or the commercial restructuring. Just wanted to hear about just the stability of the sales force and some of your all-star clinical specialists and reps on the capital side as well. I mean, are, is it relatively stable? Are you seeing any attrition, and are you planning on adding to the team as you move forward to in 2026 and beyond? Thanks for taking the questions.

Larry Wood (CEO)
Yeah. Thanks, Josh. Yeah, no, I think our team's been stable. We haven't seen any higher attrition. When I talk about the disruption, it's not about losing people. You know, I'll just provide a little bit more color on this, and we'll talk about it more tomorrow as well. What we did to create the launch teams is we took some of our most tenured people, some of our most seasoned people, and we moved them over to the launch team because we really want launches to go well. You know, I learned this in my time at Edwards. When we launched, you know, Taparite, and they launched, and they launched well with steady rhythm and steady volume, they just became healthy programs for us.

If somebody launched and they launched poorly, it, you know, it took a long time for them to get up to the projected volumes or wherever we thought they should be. We really want to focus on these launches and make sure they go well, make sure teams have all the support and they deliver, you know, spectacular outcomes for their patients, especially in those first early procedures. In creating those launch teams, though, we took some of our best people out of the utilization team, you know, the procedure support team. In doing that, you know, we backfilled those positions. We have people in place on those, but they have to rebuild relationships with those customers. You don't have somebody that maybe has a long-standing relationship.

We also realigned territories that we think, allow us to better service our customers and drive the growth. Whenever you do that, you know, people have to reestablish relationships and do all those things, and that's just what we're going through now. You know, this isn't anything that's unique to us. When I was at Edwards, and we used to split territories and hire new reps, you had new people calling on unestablished accounts, and it takes time for them to build those relationships. I see this as being very transient, being very normal. We just did, you know, a lot more of it all in one fell swoop rather than, you know, the normal course of business where you're splitting territories periodically. I think we have great people. I think we have people in the right places.

It's just going to be a matter of people maturing and settling into their new accounts that they cover.

Josh Jennings (Managing Director and Senior Analyst)
Appreciate the extra detail. Thank you.

Operator (participant)
Thank you for your question. Our next question comes from the line of Richard Newitter of Truist Securities. The floor is yours.

Richard Newitter (Managing Director and Senior Equity Research Analyst)
Thanks for taking the questions. I have two. The first one, just on systems, I think you had said a $425,000 ASP or blended ASP. You did 65 systems. Can you just tell us what the kind of the greenfields were? Were there any operating leases in there and trade-ins, et cetera? Then for 2026 on systems, I don't think you gave an explicit placement number. I think the street said around 220 something for the year. Doing the math, it would suggest you're basically kind of, or I think that's what you're backing into. Can you confirm that? Then I have a follow-up.

Larry Wood (CEO)
Yeah. I'll start with the pricing. You know, our capital pricing varies a little bit quarter to quarter. It really has to do with our customer mix, whether we're selling into some of the big IDNs or whether, you know, their individual systems are being placed. I, you know, the $425 doesn't reflect any softness in the capital. I think what we're modeling next year is we expect ASP for systems to be flat to up compared to what we saw this year, that's kind of where we are. I think in terms of systems, I think we're modeling greenfields to be very similar to this year. We're going to shed more light on that tomorrow. Kevin, do you have anything to add?

Kevin Waters (CFO)
No, we're going to walk through the different components, Rich, of guidance tomorrow, but your observation around roughly flat system sales with a slight increase in ASP, is a fair assumption.

Richard Newitter (Managing Director and Senior Equity Research Analyst)
Okay. Then, you know Larry, just, starting from the first quarter or fourth quarter of last year even, I know this predates you know, there were some seemingly transient, or it was explained to us, as transient, externalities, things like the hurricane, the impact on solution, et cetera. Then, there were some one-time factors as we moved through the year, and then you, on your last call, obviously prepared us for the destocking or the stocking component and trying to get that right. Seems like there was some discounting. I guess, you know, with respect to kind of where we are today and what you see in the business going forward, it, what can you tell us about the health of the actual underlying demand for procedures?

Is there anything with the reimbursement changes, doctor, usage patterns? You know, is it all in fact self-inflicted type items that are leading to the drawdown here or the lower consumable forecasting? You know, I think there's just been a lot of consecutive kind of noise around procedures, and now we're entering a period where there's a internal self-help factors. You know, how can you get people confident in your visibility, the ability to execute on this new, you know, seemingly reset level, and that there's nothing underlying on demand side or the penetration curve that's just, you know, bumping up against the wall? Thanks.

Larry Wood (CEO)
Yeah, thanks for the question, I understand where you're going with this. Again, you know, one of the things that we never reported on before was actual procedures. We'd always report on handpiece revenue. To provide a new level of transparency, we, you know, externally, we're going to talk about procedures. If you look at our procedure growth, it was almost 70% in the quarter, you know, compared year-over-year. I think the procedure demand, you know, and I'll tell you, even at that number, we're trying to, you know, accelerate well past that and drive further growth beyond that. It was pretty healthy procedure growth. The revenue shortfall wasn't really driven on the procedure side.

It really was about the customer ordering behavior, you know, it was being driven much more than probably we appreciated by these discounts that people had become accustomed to, and we were living this cycle of people stocking up at the end of a quarter and then depleting, going into the next quarter, which was leading to very lumpy sales. You know, again, I reviewed that practice with the team, and we just looked at it hard and said, "I don't think this makes any sense for us." If you look at the ASP that we're modeling for next year, I think that's where we're going to get the benefits from it.

To some degree, you know, I traded off, you know, continuous, you know, this ordering, cycling at discounts for having more ASP and a steady, you know, and steady revenue that's going to mirror procedures. I just think these are foundational, fundamental things that needed to happen. I, you know, I feel very strongly that these things are behind us. We've talked about the sales force reorganization, that I expect to improve our execution around procedure growth. We're going to talk tomorrow about what our value proposition is for Aquablation in the clinical community, and I think we have a compelling story to tell. You know, if you make the investor conference tomorrow or watch online, you know, we're going to provide a lot of detail on that we've never provided before.

I think we, you know, we have a solid strategy, but it all starts with these fundamental pieces, and price is just something that's always a huge part of that. You know, our margins and our path to profitability, those are key areas of focus for us, and the steps that we've taken are the things that I believe are going to drive us to the success and profitability that I think we all want.

Operator (participant)
Thank you for your question. Our next question comes from Brandon Vazquez from William Blair. The floor is yours.

Brandon Vazquez (Research Analyst)
Hey, everyone. Thanks for taking the question. Larry, you know, in a story like this, I mean, ideally, we're trying to put this behind us and, you know, use the analogy of ripping the Band-Aid off in one quarter. I think what investors often try to grapple with here is that meaningful changes to the commercial side, or big inventory changes like this, typically aren't a one quarter, one and done, but it feels like you guys have some of the confidence that, in fact, you're going to just continue growing through the year, despite some of the noise going on, and even some of the externalities that Rich was talking about, that have been impacting the business for a little bit. Maybe you could spend another, like, couple minutes on...

You said it's been a couple of weeks that you guys have been doing some of these new initiatives. Any metrics you can give us on what's already being done in the early days, that's kind of giving you the confidence that this is done, that there's not going to be another thing that we need to change on a go-forward basis?

Larry Wood (CEO)
Sure. Thanks for the question. Well, I'll start with the, you know, the procedure matching to Anthony's revenue. You know, we made those changes in, you know, in the last month, in December of last year. We have pretty, you know, many weeks of run rate now, where we're seeing those two numbers, you know, pretty much align. That's one of the things that gives us confidence that that's behind us. Again, we're gonna increase our installed base by a couple hundred instruments this year, and all of those are going to need inventory to drive.

Even if there was, even if there was a little bit more destocking in our, in our installed base, which I don't have any evidence that there is, we're still going to have all these, all these new systems coming in that are going to need inventory, which is why I said there's probably a little more upside than downside. Again, I think our focus is going to just be strictly on procedure growth, because, you know, the health of our business is never going to be impacted by customer ordering or stocking patterns. It's going to be driven by our execution in the field and by growing procedure volumes. That's why we made the changes to the sales organization, and we made them all at one time, so we can get it behind us. We can get the team moving forward and they can go execute.

We've, you know, aligned the team under a common regional leader now to where we have the focus and we have the accountability and aligned incentives to go drive our growth on the things that matter the most, which again, is going to be procedure growth. I, you know, I understand the question and I understand the comments, but we had to make these changes to drive the organization the way that we need to drive it. I'm building this thing with a multi-year plan in place, not an individual quarter. We just had to stop some of these things that I think were hurting our margins, and I think were encouraging the wrong customer behavior.

That's what we've done, and I feel very confident that the inventory issue is behind us. On the sales organization, I'm, you know, I'm confident that this will pay dividends to us down the road. Again, it's a big organization change. It does take time for those things to settle in as people reestablish those relationships, all of that is factored into our 2026 guidance.

Brandon Vazquez (Research Analyst)
Okay. Switching gears a little bit, but just because this will probably start to come up a lot in investor conversations going into the quarter, of course, I'm sure you guys have heard that a lot of noise around PAE, given the reimbursement there, and a lot of experts doing, or a lot of urologists doing more PAE cases these days. You, you gave the procedure numbers, which is super helpful, but maybe talk to us a little bit what you're seeing in the field and help us bridge, like, you know, you call 10 urologists, and nine out of 10 of them are doing more PAE, yet your procedures are still growing. Kind of give us the lay of the land of how you're seeing Aquablation and PAE playing out in the field? Thanks.

Larry Wood (CEO)
Yeah. No, thank you. You know, we're going to provide more detail on procedure trends at the investor conference tomorrow, but we're still very early in penetrating a market with more than 400,000 surgical BPH procedures annually. Our primary opportunity, improving commercial execution, is going to be consistently taking share. You know, from a competitive standpoint, we continue to think that we offer a very strong value proposition, you know, particularly related to TURP. You know, specific to with respect to PAE, you know, while the site and service economics can be attractive, we're seeing continued variability in clinical durability, and we've also seen more variability in payer coverage. Our current market intelligence suggests that coverage may be more selective over time rather than broader.

As a result, we don't see it changing the long-term competitive dynamic for patients who are appropriate for respective therapy. We're gonna show some data tomorrow, and we're gonna walk through what we think our value proposition is and why we think we're gonna be successful, you know, making inroads from a share perspective into this patient population.

Operator (participant)
Thank you for your question. Our next question comes from Suraj Kalia from Oppenheimer and Co. The floor is yours.

Suraj Kalia (Managing Director and Senior Analyst)
Hi, Larry. Can you hear me all right?

Larry Wood (CEO)
I can hear you fine.

Suraj Kalia (Managing Director and Senior Analyst)
Larry, I want to follow up on Chris's question. Obviously, the math is the math in terms of inventory in the field. I guess, if I could come at it from a different angle, Larry. Look, the board signed off, the audit committee had to sign off on the previous sales process, right? Now, a completely new process has been instituted. My question, Larry, would be, why now? Why couldn't this be staged, and what specific thing has triggered, you know, the audit committee, everyone to say, "Okay, we bless this. This is the path to go, and now is the time to do this?

Larry Wood (CEO)
You know, look, I, one of the things that we've talked about, and we'll show more detail on it tomorrow, is if we look at over the last four or five years, the handpiece revenue was always higher than our procedure volume. If we look at what was happening with pricing was pretty stable during that period of time. I signaled, you know, last year that I thought inventory levels in the field were higher than they needed to be, and that's why we signaled that we thought we would take some of that inventory level down. It wasn't until we were deep in the quarter that I think we started to get an appreciation for just how much these incentives were really driving the customer stocking behavior.

I think, you know, when I look at that, price is such a hard thing to do, and improving margins is such a challenge, and I just thought there's this huge opportunity here. To be at a $3,500 price point in our 2026 plan is a really meaningful upside, but that's not gonna pay dividends just for us in the short term. That's gonna pay dividends over the next several years as we think about our path to profitability and improving our margins. You know, the idea that you would try to, like, whittle these things down and bleed this thing off over many quarters, it was just gonna be a headwind that we, you know, frankly, would have to keep talking about and just gradually do it.

I think we would not have seen the ASP benefit if we would have tried to bleed this off over a long period of time. We just made the decision, and look, we understand completely why it created a revenue shortfall, but the impact that it has to ASP next year is so significant. It, to me, again, building these foundational pieces for the long term, it's just critical. We just took the step. I think we also wanted to recondition our customers that these practices are behind us, and that we're not gonna be doing these things anymore, and so they can just order based on their procedural usage rather than ordering on other things. You know, it...

None of the changes we've made impact our future growth trajectory, and they don't impact our path to profitability. You know, I think they were just the right decisions for us to make. I understand the point that you're making and, you know, sometimes it may look tempting to try to bleed this stuff over time, but then I think you just continue to confuse your customers with these incentive plans, and we just wanted to put that behind us and be done with it.

Suraj Kalia (Managing Director and Senior Analyst)
Fair enough. Larry, my second question: You mentioned customer behavior a couple of times in your, in your remarks. presumably, that is referring to wanting end-of-quarter discounts and whatnot. These customers have been... Their behavior has been primed by PROCEPT's, you know, sales practices, and it is over multiple years, right? Have you all done a sensitivity analysis based on your existing customer base, where, you know, the switch that y'all are turning on or off, it's gonna now change the end customer behavior once again, and almost instantaneously? Thank you for taking my questions.

Larry Wood (CEO)
Yeah, thanks. You know, we've had multiple beats of this, where we've been dealing with it. Again, we did this in December of last year, we made these changes. I think we've had a decent run now where we've been able to evaluate that, and we don't really see any impact or change there, and I don't expect that we will. I think, you know, I speak of in terms of customer conditioning, but, you know, we're a party to that as well. You know, we were, we were offering incentives, we were offering discounts. Customers were taking advantage of those, and it just wasn't a good, healthy practice for us, I don't believe, over the long haul.

I think it's far more beneficial for us to see the impact on ASP, but also to have a stable, reliable ordering pattern and revenue stream. I think those are just the things that we needed to do, and that's the structural impact of this change, but I think it benefits us over the long term.

Operator (participant)
Thank you for your question.

Our next question comes from the line of Michael Sarcone from Jefferies. The floor is yours.

Michael Sarcone (Equity Analyst)
Hey, good afternoon, and thanks for taking the questions. I guess the first one from me, you know, I know you're going to give more detail at the Investor Day tomorrow, but you carved out this team that's focused on the launch process. Can you maybe just help crystallize that? Give us one or two examples of what, you know, what you're attempting to change in the launch process now that'll kind of position you for success.

Larry Wood (CEO)
Sure. Well, here's what happened in the previous org structure was, you know, our team, we just had sort of one field team that was focused on, you know, procedures, and then obviously, we had the capital team as well. In the old process, the capital team, you know, they would sell the instrument, and then at some point, the procedure team gets notified of it. In addition to supporting the installed base, they would have to figure out how to launch this new system, how to provide the support, what doctors wanted to be trained, how they wanted to be trained. They were sort of pulled in multiple different directions. You know, when you think about it this way, you know, you have a capital team that's trying to move capital.

You have the procedure team, you know, which is made up of salespeople and clinical people, but they reported up into different leaders and sort of had their own incentives and their own plans, and their own objectives, and those weren't always aligned. By creating the launch team, it sits under our capital organization so that when the capital team is close to closing on an order, we're already lining up who are the clinicians that need to be trained, what that process is gonna be. We took some of our most tenured people and put them on the team because we want to make sure for every new system that's placed, that they get the, you know, the best support, the best care, so that they have a great launch.

The metric that we're tracking to is, you know, time for PO, the time that they complete, like, their first 10 cases. It's not just getting one case under their belt. We're really trying to drive that repeated excellence and predictability of launches and really running a very standardized playbook, which we didn't really have historically. You know, you had different people doing it differently, and again, they were being pulled from trying to support existing accounts and also trying to launch systems, and in some cases, you know, maybe you're having junior-level people do some of these activities. We have our best people in place to do those things. The impact of that is we have to rebuild those positions on the procedure team and rebuild those relationships and do those things. Again, you know, we think that's gonna pay dividends for us.

We ran a pilot in Q4, and when we ran that pilot, we saw about, you know, a 50% reduction in time to first 10 cases when we did it under the launch team model, which I think is gonna have a lot of impact for us on a go forward. Again, we'll talk more about this tomorrow and go into more detail on it, but these are the foundational pieces that I think we have to get in place. Our goal, you know, is by the, by the end of the year, that everybody's launching in a launch team model.

Michael Sarcone (Equity Analyst)
Very helpful, Larry. Thanks for the color there. I guess, second one for me is, you know, I'll echo the sentiment from other folks here. 70% procedure growth is pretty impressive. I mean, can you give us any color on, you know, how that's split out between maybe older cohorts of existing customers versus newer cohorts?

Larry Wood (CEO)
Yeah, thanks. Yeah, while, you know, while the growth number was pretty good, I will tell you, we have much more ambitious goals than that, and that's again, why we made some of these changes, because we want to drive and accelerate that. In terms of where the growth comes from, I will tell you there's, it's highly variable, and, you know, we'll provide a little bit more color on some of our insights tomorrow, but there's just not an easy one answer, you know. It's not to say that every customer is a snowflake, but there's not as much commonality as maybe one would think.

We're gonna talk about that tomorrow, and again, we're gonna be really transparent tomorrow, walking people through our strategy, through the changes we've made, why we believe they're gonna benefit us, and what we're gonna do differently on a go forward that, you know, hopefully will give people confidence in our strategy and our long-term outcome.

Michael Sarcone (Equity Analyst)
Got it. Thank you.

Operator (participant)
Thank you for your question. Our next question comes from the line of Mason Carrico from Stephens Inc. The floor is yours.

Speaker 15
Hi, good afternoon. This is Ben on for Mason. Are you in light of some of the recent changes that you've discussed today, could you update us on maybe your IDN level strategy? Are you planning to lean more heavily into these negotiations in 2026? Is there any opportunity for maybe some bulk system placements in the 2026 guide?

Larry Wood (CEO)
You know, I don't know that anything really changes year-over-year. You know, we always, you know, are focused on, you know, we have our team that focuses really on IDNs, we have teams focusing on new greenfield placements. I don't know that anything really materially is going to change from last year to next year, we're gonna talk broadly about our capital strategy tomorrow and try to shed maybe a little bit more light on that. I don't think there's any massive changes from last year to this year.

Speaker 15
Okay, great. Thank you for that. You've previously noted that maybe the Aquablation improved outcome story may not be as widely understood by patients today. Are there any patient activation initiatives you plan to launch in 2026 to maybe help drive this messaging?

Larry Wood (CEO)
Well, if you're interested in that, then you're definitely gonna want to tune in tomorrow. We have a very specific plan and strategy about making the clinical case, both to patients and to clinicians about the value proposition of Aquablation. One of the things that I may really want to stress is, you know, I think when some people hear patient activation, they think it's just about getting people off the sideline. You know, there's about 400,000 people a year that get an invasive procedure for their, you know, for their BPH. We're only about, you know, 2025, about 10% penetrated into that group. We have a lot of headroom just in taking share from the patients that are already being treated.

You go beyond that, there's a whole other funnel of people who are on drugs and other things that we'll shed light on tomorrow. Our near-term execution is all going to be focused on moving share. I think, you know, certainly the patient education and the physician education is going to be a key component of that. I think the other thing that people hear a lot of time is when they hear patient activation, they think Super Bowl commercials and, you know, millions of dollars are spent, and that's not anywhere in the ballpark that we're in. We're very focused on our path to profitability, and the programs that we have are not going to be of that scale. What's really good about this market for us is it's very easy for us to target and identify men with BPH.

It's much simpler than, for example, my old world, where you're looking for, you know, the 5% of the people over the age of 80 that have nodular heart disease. We know exactly who these people are, so you don't have to cast as wide a net to be able to target the people with BPH. We can do much more targeted education programs that I think are going to be impactful.

Speaker 15
Perfect. Thanks for taking the question.

Operator (participant)
Thanks for your question. Our next question comes from Stephanie Piazzola from Bank of America. The floor is yours.

Stephanie Piazzola (VP of Equity Research and Medical Technology)
Hi, thanks for taking the question. I'm sure we'll get more detail tomorrow, but if there's anything you could share now on how to think about that, step up to 62,000 U.S. procedures in 2026 versus the Q4 run rate was a little under 50,000. Just wanted to clarify on the ASP uplift that you expect this year, is that, just a result of the change in the customer ordering practices or something else, too?

Larry Wood (CEO)
Yeah, thanks. I'll take your second question first. Yeah, the ASP pickup is just by not offering incentives or discounts for end of the quarter purchases. By eliminating that practice, we've already seen the impact on our ASP, and we expect that to continue. On the procedure walk, we are going to go into detail on that tomorrow, but it's going to be a combination. You know, I think the biggest drivers of it, frankly, are going to be, you know, the new systems that we're adding, the benefits of the launch team, the growth that we're going to get from that. We don't have, you know, massive uptake, you know, in our installed base.

There is obviously going to be some growth that comes there, we know it's going to take a little bit of time for some of our programs to take hold. We're going to go out in a detailed walk tomorrow to walk through kind of the foots and takes on how we take our procedure total from what we had in 2025 to what we had in 2026.

Stephanie Piazzola (VP of Equity Research and Medical Technology)
Got it. Thank you. Then just on the sales force realignment and some of the potential disruption there, you know, how do we think about where you are in that process and how much is left to go? You know, how do we think about the disruption turning to a benefit and when that happens?

Larry Wood (CEO)
Yeah, thanks. All of the changes structurally in the organization have been made. So those are all in made, those are all in place. We rolled them out at our sales meeting in January. All of that work has been done. Everybody has their account targets, everybody has their quotas, everybody has their revised, you know, incentive plans. All of that work has been done. Just again, I don't want to overstate things. It's not like every single customer got a new rep. A lot of things did hold over from the old as we realigned territories.

You know, we did pull some people out for the launch team, but it wasn't like 30% of our field force or anything. All of these things, you know, have some impact. They do create some headwinds for us. I do believe that the organization matures, having a team of people, their only focus is improving utilization in our installed base, I think is going to pay dividends for us and ensuring that there's streamlined incentives between the commercial team, you know, the sales team and the clinical team, and having them report to a common leader in that region is going to drive a lot more focus. It's going to drive a lot more accountability and ultimately improve our execution and performance.

Operator (participant)
Thank you for your question. Our next question comes from the line of Danielle Antalffy from UBS. The floor is yours.

Danielle Antalffy (Senior Analyst)
Hey, good afternoon, guys. Thanks so much for taking the question. I imagine we are gonna get this more for this tomorrow, but Larry, I'm just curious, in the six months or so that you've been there, you know, how much of a heavy lift do you think the market development component is here? I appreciate you've talked a lot about the sales force realignment and adjustments there, but just from a pure market education perspective, what's the plan? I mean, as much as you feel like saying on this call versus tomorrow, and how much of that is going to be part of this procedure volume bridge and the long-term plan?

Larry Wood (CEO)
Yeah. Thanks, Danielle. You know, here's what I will say. The company historically has really been focused on placing systems and then working through the people that acquired the systems to make sure that they knew how to do the procedure and they delivered good outcomes with the, with the system. I think the team did a great job on that. In terms of marketing programs and in terms of awareness and in terms of those things, the value proposition, if I'm just real frank about it, none of that work was done. You know, if I pretended to be a patient, and I went online and tried to find information on BPH therapy, I couldn't even find Aquablation on WebMD going through what I think a patient would normally do for search terms or any of those things.

There's just some very basic fundamental work that had never been done, that had never really made our value proposition case to patients. Doing these things and getting on WebMD and getting in social media and doing the comparison, how our procedure compares in terms of outcomes and durability, and, you know, the things that matter most to patients, you know, we have to, we have to do that work, but this is always a build. It's never a light switch. I mean, you've lived this Abbott journey, you know, during my entire time at Edwards, and it's a continual build that you have to do. There's just so much basic work that can be done quickly that I think is going to make a difference.

I think, you know, we're going to spend, a fair amount of time on that tomorrow during the investor conference, and I hope you're there. I think when you see the work that we're doing, you see the value proposition we have, I think we're going to make a compelling case to clinicians and also to patients.

Danielle Antalffy (Senior Analyst)
Okay, that's helpful. I, again, don't want to front run to tomorrow, but, you know, one thing we heard in our diligence in speaking to docs was some level of appetite to have the ability to do this in the ASC. I know you guys aren't ready for that yet, but just from a capacity perspective, is that something that could be part of the long-term plan? Anything you can say about that? Thank you so much.

Larry Wood (CEO)
Yeah, thanks, Danielle. Certainly, for the long-term plan, that's going to become part of our story as we go through time. I think that that's very fair. I don't think it's something that's so much of a near-term thing for us. The other thing, I'll just address it, and we're going to talk about it tomorrow, is we have people saying, like, are you going to cover cases forever? You know, because that's been our model we've done historically. We'll provide an update on that as well, on how we think these things evolve over time, and that can improve our efficiency and again, improve our ability to execute.

Danielle Antalffy (Senior Analyst)
Thank you.

Operator (participant)
Thank you for your question. Our next question comes from Mike Kratky from SVB Leerink.

Mike Kratky (Senior Research Analyst)
Hey, everyone. Thanks for taking my questions. I wanted to follow up on Chris's question again earlier. I mean, if handpiece is sold, has consistently been above procedure volumes every single quarter for the last three years, outside of the fourth quarter, I mean, wouldn't your customers still have a pretty substantial buildup of handpieces that they have available that they need to work through? You know, when you talk about this one-to-one ratio, can you just help us understand why that is. You know, you have confidence that that's going to be the case, and was there anything in the voluntary field action that might have impacted that?

Larry Wood (CEO)
Yeah. Well, I'll start with the second part. There was nothing in the field action that had any impact on this one way or the other. Completely separate event that didn't have any impact. In terms of the handpieces, what I can tell you is customers still need to maintain inventory levels. You know, nobody's sitting there with one, you know, with one handpiece on the shelf, so they need to continue to carry inventory levels. It's just a matter of what inventory levels are they carrying.

I think what was happening with the, you know, incentive plans is people would stock way much, way more inventory than they wanted, and then they would burn it down in the first couple of months of a quarter, and then they would repeat the process and reorder again and take advantage of these incentives. I think, you know, we eliminated that. Now what we've seen is a settling into where accounts are just carrying the inventory levels that they feel are appropriate based on their usage and based on whatever their inventory policies are. We modeled next year at one-to-one. That's what has been actually happening as we look at the last several weeks since we made these policy changes or practice changes, I guess. That's what gives us confidence on a go forward.

The other thing is, again, we're going to install a couple hundred systems next year, and they're all going to have to take stocking orders, and they're all going to have to, you know, establish inventory levels as well, and we're still modeling it at a one-to-one. Those are the things that give us confidence next year that a one-to-one ratio is going to be in line.

Mike Kratky (Senior Research Analyst)
Got it. Maybe just the last one on my side, but can you provide any additional color on the cadence of OpEx and your sales force expansion, or SG&A throughout the year?

Kevin Waters (CFO)
Yeah, but on the cadence of OpEx, maybe I'll just point to what our EBITDA guidance implies. We had said that both the low and the high end of guidance will be EBITDA positive in the fourth quarter. We're forecasting an EBITDA loss in Q1 of somewhere in the $20 million range, which would put OpEx somewhere between $85 million-$88 million in the first quarter. You just build from there. Again, we're going to go through kind of that walk tomorrow as well.

Mike Kratky (Senior Research Analyst)
Understood. Thanks, Kevin.

Kevin Waters (CFO)
Yep.

Operator (participant)
Thank you for your question. Our last question comes from Nathan Treybeck from Wells Fargo. The floor is yours.

Nathan Treybeck (Equity Analyst)
Great. Thanks for taking the question. It sounds like handpiece sales have exceeded procedure volumes by a wide margin for a long time. I guess, can you talk about what this implies for actual utilization levels at your accounts? I guess, how would you put that into context, you know, the monthly utilization numbers that, you know, the company gave in the past for other BPH surgical procedures?

Larry Wood (CEO)
Yeah, you know, Utilization is highly variable, and I think, again, what we're focused on is procedure growth. I probably can't go as deep on the history here. I don't know. Kevin, do you have anything you want to add?

Kevin Waters (CFO)
I think if you look at it's been relatively consistent over the last 3 years. Just to maybe put a number that's been thrown around a few times, to highlight, I think it is correct if we're at a one-to-one ratio.

You would see about 11,000 units out in the field. Remember, we're adding over 200 systems in 2026, which would put, given current procedure trends, average customer inventory just a little over one month to seven weeks, which we feel really comfortable with.

Nathan Treybeck (Equity Analyst)
On your capital funnel, I think last time you mentioned there might have been more scrutiny of budgets. It sounds like you're expecting flattish system placements in 2026. I guess, talk about the level of price sensitivity you're seeing in, you know, the accounts that you're pursuing now, and I guess, the willingness to place an occlusion system, you know, with just a BPH indication. Thanks.

Larry Wood (CEO)
Yeah, well, we actually had a very strong capital quarter in Q4. We, we had 65 systems, which is an all-time high for us, and so I think that supports, you know, that we continue to see demand. Now, just the natural nature of capital, you know, the fourth quarter tends to be, you know, our biggest quarter every year, and we are modeling about the same number of placements in 2026 as what we had in 2025, and we'll provide more detail on that. We're actually modeling ASP to be flat or up from in 2026 from where they were in 2025. You know, we continue to see good demand for...

We think the capital market, you know, I don't want to say it's ever easy, but I don't think there's anything structurally that's changing from 2025 to 2026 that would impact our ability to execute our plan.

Operator (participant)
Thank you for that question. At this time, that does conclude the question-and-answer session. I would now like to turn it back to Matt Bacso, CEO, for closing remarks.

Larry Wood (CEO)
Thanks, operator. I appreciate everyone's time today going through Q&A and listening to the Q4 call. I just want to remind everybody that we are hosting our Analyst Day tomorrow in New York at 8:00 A.M. Eastern, and please show up a little early. There will be breakfast provided, but we will start promptly at 8:00 A.M. Hope to see you there. Thank you.

Operator (participant)
Thank you for your participation in today's conference. This does conclude the program. You may now disconnect]
                """,
            }
        ],
    }

    if ticker.upper() in MANUAL_TRANSCRIPTS:
        for entry in MANUAL_TRANSCRIPTS[ticker.upper()]:
            key = f"Q{entry['quarter']}_{entry['year']}"
            result["transcripts"][key] = entry
        result["fetched_count"] = len(result["transcripts"])
        return result
    # ─────────────────────────────────────────────────────────────────────────

    api_keys = _get_av_api_keys()
    if not api_keys:
        result["warning"] = "ALPHA_VANTAGE_API_KEY_1 etc. not set — transcripts unavailable"
        return result

    # Load today's AV count from Supabase once (session cache populated here)
    _load_av_session_count()
    remaining_before = av_requests_remaining()
    if remaining_before <= AV_BUDGET_WARNING_THRESHOLD:
        logger.warning(
            "fetch_transcripts(%s): AV budget low — %d requests remaining today",
            ticker, remaining_before,
        )
    av_calls_made = 0  # track how many new API calls this invocation makes

    quarters_to_try = _get_quarters_to_try(ticker=ticker)
    try:
        # Probe quarters in descending order; collect up to 2 transcripts.
        for quarter_str in quarters_to_try:
            if result["fetched_count"] >= 2:
                break

            year_int = int(quarter_str[:4])
            quarter_int = int(quarter_str[5])
            key = f"Q{quarter_int}_{year_int}"

            # ── ticker_events cache check — skip AV call if already fetched ──
            cached = _transcript_cached(ticker.upper(), quarter_str)
            if cached:
                result["transcripts"][key] = cached
                result["fetched_count"] += 1
                logger.info(
                    "fetch_transcripts(%s): cache hit %s — skipped AV call", ticker, key
                )
                continue

            # ── AV daily budget check ─────────────────────────────────────────
            if not _av_request_allowed():
                result["warning"] = (
                    f"Alpha Vantage daily limit ({_get_av_daily_limit()} requests) reached for "
                    f"{datetime.date.today().isoformat()}. Remaining quarters not fetched."
                )
                break

            # Try each API key in order until one succeeds
            success = False
            for api_key in api_keys:
                resp = requests.get(
                    AV_BASE,
                    params={
                        "function": "EARNINGS_CALL_TRANSCRIPT",
                        "symbol": ticker.upper(),
                        "quarter": quarter_str,
                        "apikey": api_key,
                    },
                    timeout=15,
                )
                try:
                    resp.raise_for_status()
                    data = resp.json()
                    # Check for rate limit or invalid key
                    if "Information" in data or "Note" in data:
                        msg = data.get("Information") or data.get("Note", "")
                        logger.warning("Alpha Vantage key %s: %s", api_key[:8], msg[:120])
                        continue  # try next key
                    # Success
                    av_calls_made += 1
                    _av_session["count"] += 1
                    success = True
                    break
                except Exception as exc:
                    logger.warning("Alpha Vantage key %s failed: %s", api_key[:8], str(exc))
                    continue
            if not success:
                result["warning"] = "All Alpha Vantage API keys failed or rate limited"
                break

            turns = data.get("transcript")
            if not turns:
                # No transcript for this quarter — try next
                time.sleep(1)
                continue

            event_date = data.get("date")
            result["transcripts"][key] = {
                "quarter": quarter_int,
                "year": year_int,
                "date": event_date,
                "text": _turns_to_text(turns),
                "turns": turns,  # structured turns with per-turn sentiment
            }
            result["fetched_count"] += 1

            # Mark fetched in ticker_events so future runs use cache
            _mark_transcript_fetched(ticker.upper(), quarter_str, event_date)

            time.sleep(1)  # stay within free-tier rate limits

        if result["fetched_count"] == 0 and result["warning"] is None:
            result["warning"] = f"No transcripts found for {ticker.upper()} in last {len(quarters_to_try)} quarters"

    except Exception as exc:
        result["warning"] = str(exc)

    # Persist updated AV count to Supabase if any new API calls were made
    if av_calls_made > 0:
        _persist_av_count(_av_session["count"])
        logger.info(
            "fetch_transcripts(%s): made %d AV call(s); %d remaining today",
            ticker, av_calls_made, av_requests_remaining(),
        )

    return result
