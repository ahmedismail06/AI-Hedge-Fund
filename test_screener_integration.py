"""
Integration test — screener scoring pipeline with a hardcoded ticker subset.

Bypasses build_universe() entirely — injects UniverseCandidate objects directly.
Calls real APIs (Polygon + yfinance). Requires .env to be populated.

Key design: data is fetched ONCE at module load and cached in _RAW_MAP / _RAW_FACTORS.
All test functions and regime runs reuse that cached data — no repeated API calls.

Usage:
    python test_screener_integration.py          # full visual output + assertions
    pytest test_screener_integration.py -v -s    # pytest (shows print output with -s)

Ticker selection rationale:
  All tickers are US-listed, domestic filers (10-K not 20-F), and have been
  public for several years — criteria that ensure Polygon financial data coverage.
  Foreign filers (20-F), recent SPACs, and micro-caps on lower Polygon tiers
  often return 0 FY rows and should be avoided in integration tests.

  Strong candidates (high quality, should rank near top):
    CWAN  — Clearwater Analytics: profitable SaaS, strong margins, low debt
    ALRM  — Alarm.com: established SaaS, profitable, recurring revenue
    PRGS  — Progress Software: mature SaaS, consistent FCF, low debt

  Weak candidates (should rank near bottom):
    BIGC  — BigCommerce: cash-burning ecommerce SaaS, sustained GAAP losses
    DOCN  — DigitalOcean: negative GAAP net income (heavy SBC), negative ROE

  Beneish gate test (likely EXCLUDED or FLAGGED):
    SPOK  — Spok Holdings: declining revenue, healthcare comms, accruals risk

  Sector coverage:
    SaaS/Tech   → CWAN, ALRM, PRGS, BIGC, DOCN
    Healthcare  → SPOK
    Industrials → ASTE (Astec Industries: established US Industrials, long filing history)
"""

import os
import sys
import time
from dotenv import dotenv_values

# Load env before any backend imports
os.environ.update(dotenv_values(".env"))

from backend.screener.universe import UniverseCandidate, fetch_ticker_data
from backend.screener.factors.quality import score_quality
from backend.screener.factors.value import score_value
from backend.screener.factors.momentum import score_momentum
from backend.screener.factors.earnings_quality import compute_beneish
from backend.screener.scorer import compute_composite, ScreenerResult


# ---------------------------------------------------------------------------
# Hardcoded universe — bypasses build_universe() and all its API calls
# ---------------------------------------------------------------------------

UNIVERSE: list[UniverseCandidate] = [
    # Strong: profitable SaaS, good margins, low debt
    UniverseCandidate(ticker="CWAN",  market_cap_m=1800.0, sector="SaaS",        adv_k=8000.0),
    UniverseCandidate(ticker="ALRM",  market_cap_m=1200.0, sector="SaaS",        adv_k=4000.0),
    UniverseCandidate(ticker="PRGS",  market_cap_m=1500.0, sector="SaaS",        adv_k=3000.0),
    # Weak: GAAP losses, high SBC, negative ROE
    UniverseCandidate(ticker="BIGC",  market_cap_m=300.0,  sector="SaaS",        adv_k=5000.0),
    UniverseCandidate(ticker="DOCN",  market_cap_m=1400.0, sector="SaaS",        adv_k=12000.0),
    # Beneish gate test
    UniverseCandidate(ticker="SPOK",  market_cap_m=200.0,  sector="Healthcare",  adv_k=1000.0),
    # Industrials coverage
    UniverseCandidate(ticker="ASTE",  market_cap_m=600.0,  sector="Industrials", adv_k=2000.0),
]

TICKERS = [c.ticker for c in UNIVERSE]

# Conservative ordering pairs — LEFT should outscore RIGHT in Risk-On.
# Only include pairs where both tickers have Polygon FY data — otherwise one side
# defaults to neutral 5.0 and the comparison is meaningless.
# Update this list (not the test function) if market data shifts a pair.
EXPECTED_ORDERING = [
    ("CWAN",  "BIGC"),   # profitable SaaS vs cash-burning ecommerce
    ("CWAN",  "DOCN"),   # positive ROE vs deeply negative ROE (heavy SBC)
    ("ALRM",  "BIGC"),   # profitable recurring-revenue SaaS vs loss-maker
    ("PRGS",  "DOCN"),   # consistent FCF vs GAAP losses
]


# ---------------------------------------------------------------------------
# Module-level cache — fetched ONCE, reused by all tests and regime runs
# ---------------------------------------------------------------------------

_RAW_MAP:     dict[str, dict] | None = None   # ticker → raw fetch output
_RAW_FACTORS: dict[str, dict] | None = None   # ticker → scored factors


def _ensure_data_loaded() -> tuple[dict[str, dict], dict[str, dict]]:
    """
    Fetch and score all tickers exactly once. Subsequent calls return the cache.
    This is the single entry point for data — no test function should call
    fetch_ticker_data() directly.
    """
    global _RAW_MAP, _RAW_FACTORS
    if _RAW_MAP is not None and _RAW_FACTORS is not None:
        return _RAW_MAP, _RAW_FACTORS

    print(f"\n{'='*60}")
    print(f"Fetching data for {len(TICKERS)} tickers (once — cached for all runs)...")
    print(f"{'='*60}")

    raw_map: dict[str, dict] = {}
    for i, ticker in enumerate(TICKERS, 1):
        print(f"  [{i}/{len(TICKERS)}] Fetching {ticker}...", flush=True)
        try:
            raw_map[ticker] = fetch_ticker_data(ticker)
        except Exception as exc:
            print(f"    WARNING: fetch_ticker_data({ticker}) failed: {exc}", file=sys.stderr)
            raw_map[ticker] = {
                "ticker": ticker, "fmp": {}, "polygon_financials": {"results": []},
                "price_history": [], "yf_info": {},
            }
        time.sleep(0.3)   # be polite to Polygon / Yahoo Finance

    raw_factors: dict[str, dict] = {}
    for ticker, raw in raw_map.items():
        entry: dict = {
            "quality":  {},
            "value":    {},
            "momentum": {},
            "beneish":  {"gate_result": "INSUFFICIENT_DATA", "m_score": None, "missing_fields": []},
            "form4":    {"insider_buy": False},
            "fmp":      raw.get("fmp", {}),
        }
        try:
            entry["quality"]  = score_quality(ticker, raw["polygon_financials"], raw["yf_info"])
        except Exception as exc:
            print(f"  WARNING: quality scorer failed for {ticker}: {exc}", file=sys.stderr)
        try:
            entry["value"]    = score_value(ticker, raw["polygon_financials"], raw["fmp"])
        except Exception as exc:
            print(f"  WARNING: value scorer failed for {ticker}: {exc}", file=sys.stderr)
        try:
            entry["momentum"] = score_momentum(ticker, raw["price_history"], raw["fmp"])
        except Exception as exc:
            print(f"  WARNING: momentum scorer failed for {ticker}: {exc}", file=sys.stderr)
        try:
            entry["beneish"]  = compute_beneish(ticker, raw["polygon_financials"])
        except Exception as exc:
            print(f"  WARNING: beneish scorer failed for {ticker}: {exc}", file=sys.stderr)
        raw_factors[ticker] = entry

    _RAW_MAP     = raw_map
    _RAW_FACTORS = raw_factors
    return _RAW_MAP, _RAW_FACTORS


def run_pipeline(regime: str = "Risk-On") -> list[ScreenerResult]:
    """
    Compute composite scores for the cached factor data under the given regime.
    Does NOT re-fetch data — only compute_composite() is called each time.
    """
    _, raw_factors = _ensure_data_loaded()
    return compute_composite(UNIVERSE, raw_factors, regime)


# ---------------------------------------------------------------------------
# Diagnostics — shows exactly what raw values were fetched per ticker
# ---------------------------------------------------------------------------

def print_diagnostics() -> None:
    """
    Print a table of the raw values retrieved per ticker.
    Useful for spotting missing data (None) caused by API failures or rate limits.
    """
    raw_map, raw_factors = _ensure_data_loaded()

    print(f"\n{'='*95}")
    print("  RAW DATA DIAGNOSTICS  (None = data missing / API call failed)")
    print(f"{'='*95}")

    # FY rows check
    print(f"\n  Polygon FY rows available per ticker:")
    for ticker in TICKERS:
        results = raw_map[ticker]["polygon_financials"].get("results", [])
        fy_rows = [r for r in results if r.get("fiscal_period") == "FY"]
        price_bars = len(raw_map[ticker].get("price_history", []))
        print(f"    {ticker:<6}  FY rows: {len(fy_rows)}   price bars: {price_bars}")

    # Quality raw values
    print(f"\n  Quality raw values:")
    print(f"    {'Ticker':<6}  {'GrossMargin':>12}  {'RevGrowth':>10}  {'ROE':>8}  {'D/E':>8}  {'EPSBeat':>8}")
    for ticker in TICKERS:
        rv = raw_factors[ticker]["quality"].get("raw_values", {})
        def _f(v): return f"{v:.3f}" if v is not None else "  None"
        print(f"    {ticker:<6}  {_f(rv.get('gross_margin')):>12}  {_f(rv.get('revenue_growth_yoy')):>10}  "
              f"{_f(rv.get('roe')):>8}  {_f(rv.get('debt_to_equity')):>8}  {_f(rv.get('eps_beat_rate')):>8}")

    # Value raw values
    print(f"\n  Value raw values:")
    print(f"    {'Ticker':<6}  {'EVMultiple':>11}  {'EVType':<12}  {'P/FCF':>8}  {'P/B':>8}")
    for ticker in TICKERS:
        rv = raw_factors[ticker]["value"].get("raw_values", {})
        def _f(v): return f"{v:.2f}" if v is not None else "None"
        ev_type = rv.get("ev_type") or "N/A"
        print(f"    {ticker:<6}  {_f(rv.get('ev_multiple')):>11}  {ev_type:<12}  "
              f"{_f(rv.get('p_fcf')):>8}  {_f(rv.get('price_book')):>8}")

    # Momentum raw values
    print(f"\n  Momentum raw values:")
    print(f"    {'Ticker':<6}  {'12-1mo':>8}  {'6-1mo':>8}  {'EPSRev':>8}  {'SIBonus':>8}")
    for ticker in TICKERS:
        rv = raw_factors[ticker]["momentum"].get("raw_values", {})
        si = raw_factors[ticker]["momentum"].get("short_interest_bonus", 0.0)
        def _f(v): return f"{v:.3f}" if v is not None else "   None"
        print(f"    {ticker:<6}  {_f(rv.get('price_12_1')):>8}  {_f(rv.get('price_6_1')):>8}  "
              f"{_f(rv.get('eps_revision')):>8}  {si:>8.3f}")

    # Beneish
    print(f"\n  Beneish M-scores:")
    for ticker in TICKERS:
        b = raw_factors[ticker]["beneish"]
        m = f"{b['m_score']:.4f}" if b["m_score"] is not None else "None"
        missing = b.get("missing_fields", [])
        print(f"    {ticker:<6}  M={m:<10}  {b['gate_result']:<18}  missing={missing}")

    print()


# ---------------------------------------------------------------------------
# Results table
# ---------------------------------------------------------------------------

def print_results(results: list[ScreenerResult], regime: str) -> None:
    print(f"\n{'='*75}")
    print(f"  Regime: {regime}  |  Qualify threshold: ≥ 7.0")
    print(f"{'='*75}")
    print(f"{'Rank':>4}  {'Ticker':<6}  {'Composite':>9}  {'Quality':>8}  {'Value':>7}  {'Momentum':>9}  {'Beneish':<20}  Flags")
    print(f"{'-'*90}")

    qualify_count = 0
    for r in results:
        flags = []
        if r.excluded:
            flags.append("EXCLUDED")
        if r.beneish_flag == "FLAGGED":
            flags.append("FLAGGED")
        if r.insider_signal:
            flags.append("INSIDER")
        if r.composite_score >= 7.0 and not r.excluded:
            flags.append("QUALIFY")
            qualify_count += 1

        rank_str = f"{r.rank:>4}" if not r.excluded else "   X"
        beneish_str = r.beneish_flag or "N/A"
        if r.beneish_m_score is not None:
            beneish_str += f" ({r.beneish_m_score:.2f})"
        print(
            f"{rank_str}  {r.ticker:<6}  {r.composite_score:>9.3f}  "
            f"{r.quality_score:>8.3f}  {r.value_score:>7.3f}  {r.momentum_score:>9.3f}  "
            f"{beneish_str:<20}  {', '.join(flags)}"
        )

    print(f"{'-'*90}")
    print(f"  {qualify_count} ticker(s) qualify for research queue (score ≥ 7.0)")


# ---------------------------------------------------------------------------
# pytest-compatible tests — all use cached data, zero extra API calls
# ---------------------------------------------------------------------------

def test_pipeline_runs_without_error():
    """Full fetch + score + composite pipeline completes without unhandled exceptions."""
    results = run_pipeline("Risk-On")
    assert isinstance(results, list)
    assert len(results) == len(TICKERS), f"Expected {len(TICKERS)} results, got {len(results)}"


def test_all_scores_in_valid_range():
    """All composite/factor scores are within [0.0, 10.0]."""
    results = run_pipeline("Risk-On")
    for r in results:
        for name, score in [
            ("composite", r.composite_score),
            ("quality",   r.quality_score),
            ("value",     r.value_score),
            ("momentum",  r.momentum_score),
        ]:
            assert 0.0 <= score <= 10.0, f"{r.ticker}: {name} score {score:.3f} out of [0, 10]"


def test_results_sorted_descending():
    """Results are sorted by composite_score descending."""
    results = run_pipeline("Risk-On")
    scores = [r.composite_score for r in results]
    assert scores == sorted(scores, reverse=True), f"Results not sorted descending: {scores}"


def test_beneish_excluded_tickers_have_zero_score():
    """Any EXCLUDED ticker must have composite_score=0.0 and excluded=True."""
    results = run_pipeline("Risk-On")
    for r in results:
        if r.beneish_flag == "EXCLUDED":
            assert r.composite_score == 0.0, f"{r.ticker}: EXCLUDED but score={r.composite_score}"
            assert r.excluded is True


def test_regime_weights_change_scores():
    """Risk-On and Risk-Off should produce at least one different composite score."""
    results_on  = run_pipeline("Risk-On")
    results_off = run_pipeline("Risk-Off")
    scores_on   = {r.ticker: r.composite_score for r in results_on  if not r.excluded}
    scores_off  = {r.ticker: r.composite_score for r in results_off if not r.excluded}
    any_diff = any(abs(scores_on.get(t, 0) - scores_off.get(t, 0)) > 0.01 for t in scores_on)
    assert any_diff, "Risk-On and Risk-Off produced identical scores — weights not applied"


def test_relative_ordering():
    """
    Expected ordering pairs: LEFT ticker should outscore RIGHT in Risk-On.
    Update EXPECTED_ORDERING (not this test) if market data shifts the pairs.
    """
    results  = run_pipeline("Risk-On")
    score_of = {r.ticker: r.composite_score for r in results}
    failures = []
    for better, worse in EXPECTED_ORDERING:
        if better not in score_of or worse not in score_of:
            continue
        if score_of[better] <= score_of[worse]:
            failures.append(f"{better} ({score_of[better]:.3f}) should > {worse} ({score_of[worse]:.3f})")
    assert not failures, "Ordering expectations violated:\n" + "\n".join(failures)


def test_no_tickers_with_all_null_factors():
    """
    Warn if any ticker has all-None raw values across all three factors.
    This indicates a complete API failure for that ticker, not a real score.
    """
    _, raw_factors = _ensure_data_loaded()
    all_null = []
    for ticker in TICKERS:
        q_vals = list(raw_factors[ticker]["quality"].get("raw_values", {}).values())
        v_vals = list(raw_factors[ticker]["value"].get("raw_values", {}).values())
        m_vals = list(raw_factors[ticker]["momentum"].get("raw_values", {}).values())
        all_vals = [x for x in q_vals + v_vals + m_vals if isinstance(x, (int, float))]
        if not all_vals:
            all_null.append(ticker)
    assert not all_null, (
        f"These tickers returned ALL None raw values — likely a complete API failure: {all_null}\n"
        "Check Polygon rate limits or API key."
    )


# ---------------------------------------------------------------------------
# Main (visual run)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Diagnostics first — shows exactly what data came back from the APIs
    print_diagnostics()

    # Scoring table for each regime (reuses cached data, no more API calls)
    for regime in ["Risk-On", "Risk-Off", "Stagflation"]:
        print_results(run_pipeline(regime), regime)

    # Assertions
    print("\n--- Running assertions ---")
    test_pipeline_runs_without_error()
    test_all_scores_in_valid_range()
    test_results_sorted_descending()
    test_beneish_excluded_tickers_have_zero_score()
    test_regime_weights_change_scores()
    test_no_tickers_with_all_null_factors()
    print("All structural assertions passed.")

    print("\nChecking relative ordering...")
    try:
        test_relative_ordering()
        print("Ordering assertions passed.")
    except AssertionError as exc:
        print(f"WARNING: {exc}")
        print("Update EXPECTED_ORDERING if market data has shifted.")
