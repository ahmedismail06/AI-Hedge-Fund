"""
Alpha Vantage Transcript Coverage Test
---------------------------------------
Tests whether the free Alpha Vantage EARNINGS_CALL_TRANSCRIPT endpoint has
coverage for US micro/small-cap tickers (the $50M–$2B, ≤5 analyst universe).

Usage:
    export ALPHA_VANTAGE_API_KEY_1=key1
    export ALPHA_VANTAGE_API_KEY_2=key2
    export ALPHA_VANTAGE_API_KEY_3=key3
    export ALPHA_VANTAGE_API_KEY_4=key4
    export ALPHA_VANTAGE_API_KEY_5=key5
    python testing_av_transcripts.py

Free tier limit: 25 requests/day. This script makes 1 request per ticker.
Get a free key at: https://www.alphavantage.co/support/#api-key
"""

import os
import time
import requests
from dotenv import load_dotenv
AV_BASE = "https://www.alphavantage.co/query"

# ── Test universe ──────────────────────────────────────────────────────────────
# Mix of micro/small-caps across SaaS, Healthcare, Industrials — the target
# universe for this system. Chosen specifically because they are under-covered
# (≤5 analysts) and span a range of market caps within the $50M–$2B band.
TEST_TICKERS = [
    ("HIMS",  "Hims & Hers Health",      "Healthcare",   "~$2B"),
    ("CLOV",  "Clover Health",           "Healthcare",   "~$500M"),
    ("PCVX",  "Vaxcyte",                 "Healthcare",   "~$4B"),   # slightly above range — good stress test
    ("MAPS",  "WM Technology",           "SaaS",         "~$200M"),
    ("BRZE",  "Braze",                   "SaaS",         "~$4B"),   # slightly above — coverage stress test
    ("TMDX",  "TransMedics Group",       "Healthcare",   "~$1.5B"),
    ("XMTR",  "Xometry",                 "Industrials",  "~$600M"),
    ("SHYF",  "The Shyft Group",         "Industrials",  "~$300M"),
    ("KTOS",  "Kratos Defense",          "Industrials",  "~$3B"),   # slightly above — coverage stress test
    ("NVCR",  "NovaCure",                "Healthcare",   "~$1.2B"),
]

DELAY_SECONDS = 15  # stay well within free tier rate limits

# Try these quarters in order, most recent first (today is 2026-03-15)
QUARTERS_TO_TRY = ["2025Q4", "2025Q3", "2025Q2", "2025Q1"]


def fetch_transcript(ticker: str, api_keys: list[str]) -> tuple[dict, str | None]:
    """
    Tries QUARTERS_TO_TRY in order and returns the first successful response.
    Tries each API key in order if one fails.
    Returns (response_dict, quarter_string) — quarter is None if nothing found.
    """
    for quarter in QUARTERS_TO_TRY:
        for api_key in api_keys:
            params = {
                "function": "EARNINGS_CALL_TRANSCRIPT",
                "symbol": ticker,
                "quarter": quarter,
                "apikey": api_key,
            }
            try:
                resp = requests.get(AV_BASE, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()

                # Stop immediately on rate limit / bad key — no point trying more quarters
                if "Information" in data or "Note" in data:
                    continue  # try next key

                # If there's actual transcript content, return it
                if data and "transcript" in data:
                    return data, quarter

            except Exception:
                continue  # try next key

        time.sleep(2)  # small pause between quarter probes

    return {}, None


def evaluate_response(ticker: str, data: dict, quarter_found: str | None) -> dict:
    """
    Inspects the response and returns a coverage summary.
    AV returns a single transcript object (not a list) when a quarter is specified.
    """
    result = {
        "ticker": ticker,
        "status": None,
        "most_recent_quarter": quarter_found,
        "transcript_length_chars": 0,
        "has_sentiment": False,
        "rate_limited": False,
        "error": None,
        "raw_keys": list(data.keys()),
    }

    # Alpha Vantage rate limit or invalid key
    if "Information" in data or "Note" in data:
        msg = data.get("Information") or data.get("Note", "")
        result["status"] = "RATE_LIMITED_OR_INVALID_KEY"
        result["rate_limited"] = True
        result["error"] = msg
        return result

    # No data / not covered
    if not data or "transcript" not in data:
        result["status"] = "NOT_COVERED"
        return result

    # Has data — AV returns a single object with a "transcript" key (string of full text)
    result["status"] = "COVERED"
    transcript_text = data.get("transcript", "")
    result["transcript_length_chars"] = len(transcript_text)

    # Check for sentiment data (some AV responses include per-sentence sentiment)
    result["has_sentiment"] = "sentiment" in data or any(
        k != "transcript" and "sentiment" in str(v)
        for k, v in data.items()
    )

    return result


def main():
    load_dotenv()
    api_keys = []
    for i in range(1, 6):  # Load up to 5 keys
        key = os.getenv(f"ALPHA_VANTAGE_API_KEY_{i}", "").strip()
        if key:
            api_keys.append(key)
    
    if not api_keys:
        # Fallback to single key
        single_key = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
        api_keys = [single_key] if single_key else []
    
    if not api_keys:
        print("ERROR: ALPHA_VANTAGE_API_KEY or ALPHA_VANTAGE_API_KEY_1 etc. not set.")
        print("  Get free keys at https://www.alphavantage.co/support/#api-key")
        print("  Then run:")
        print("    export ALPHA_VANTAGE_API_KEY_1=key1")
        print("    export ALPHA_VANTAGE_API_KEY_2=key2")
        print("    etc.")
        return

    print("Alpha Vantage Transcript Coverage Test")
    print(f"Testing {len(TEST_TICKERS)} tickers | {DELAY_SECONDS}s delay between requests\n")
    print(f"{'Ticker':<8} {'Company':<28} {'Sector':<14} {'Status':<22} {'Quarter':<12} {'Text Length'}")
    print("-" * 100)

    results = []
    for ticker, company, sector, mktcap in TEST_TICKERS:
        try:
            data, quarter_found = fetch_transcript(ticker, api_keys)
            r = evaluate_response(ticker, data, quarter_found)
        except Exception as e:
            r = {
                "ticker": ticker,
                "status": "HTTP_ERROR",
                "quarters_available": 0,
                "most_recent_quarter": None,
                "transcript_length_chars": 0,
                "has_sentiment": False,
                "rate_limited": False,
                "error": str(e),
                "raw_keys": [],
            }

        results.append((r, company, sector, mktcap))

        status_display = r["status"]
        if r["rate_limited"]:
            status_display = "⚠ RATE LIMITED"
        elif r["status"] == "COVERED":
            status_display = "✅ COVERED"
        elif r["status"] == "NOT_COVERED":
            status_display = "❌ NOT COVERED"

        char_len = f"{r['transcript_length_chars']:,}" if r["transcript_length_chars"] else "—"
        recent = r["most_recent_quarter"] or "—"

        print(
            f"{ticker:<8} {company:<28} {sector:<14} {status_display:<22} "
            f"{recent:<12} {char_len}"
        )

        if r["rate_limited"]:
            print(f"\n⚠  Hit rate limit after {len(results)} requests. Stopping early.")
            print(f"   Message: {r['error'][:100]}")
            break

        if ticker != TEST_TICKERS[-1][0]:
            time.sleep(DELAY_SECONDS)

    # ── Summary ────────────────────────────────────────────────────────────────
    covered = [r for r, *_ in results if r["status"] == "COVERED"]
    not_covered = [r for r, *_ in results if r["status"] == "NOT_COVERED"]
    rate_limited = [r for r, *_ in results if r["rate_limited"]]

    print("\n" + "=" * 100)
    print(f"SUMMARY: {len(covered)}/{len(results)} tickers covered")
    if covered:
        avg_len = sum(r["transcript_length_chars"] for r in covered) // len(covered)
        print(f"  Avg transcript length: {avg_len:,} chars")
        print(f"  Sentiment data included: {any(r['has_sentiment'] for r in covered)}")
    if not_covered:
        print(f"  Not covered: {', '.join(r['ticker'] for r in not_covered)}")
    if rate_limited:
        print(f"  ⚠ Rate limited before completing all tickers — re-run tomorrow or upgrade key")

    print("\nVERDICT:")
    coverage_rate = len(covered) / max(len(results) - len(rate_limited), 1)
    if coverage_rate >= 0.8:
        print("  ✅ Alpha Vantage has strong coverage for this universe. Safe to use as primary source.")
    elif coverage_rate >= 0.5:
        print("  ⚠  Alpha Vantage has partial coverage. Build the fallback chain (AV → EDGAR → manual).")
    else:
        print("  ❌ Alpha Vantage coverage is too patchy for this universe. Stick with API Ninjas or pay for FMP.")


if __name__ == "__main__":
    main()
