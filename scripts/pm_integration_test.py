"""
PM Agent End-to-End Integration Test
=====================================
Seeds 4 controlled scenarios into Supabase, fires one PM cycle via the API,
then asserts that a pm_decisions row was written for each scenario category.

Usage:
  # Terminal 1: start backend (fresh restart clears fingerprint state)
  source .venv/bin/activate && uvicorn backend.main:app --reload

  # Terminal 2: run within first 5 min of server start (avoid APScheduler race)
  source .venv/bin/activate && python scripts/pm_integration_test.py

Cost: 1 real Claude API call (claude-sonnet-4-6, budget_tokens=8000). Not free.

Known flakes:
  - APScheduler race: if the 5-min cron fires before your manual trigger,
    seeded state gets consumed. Restart server + re-run.
  - Market hours: outside 9:30-16:00 ET, NEW_ENTRY may get QUEUED_FOR_OPEN
    instead of PENDING_HUMAN — both are valid and assertions cover both.
  - CRISIS dedup: if a prior CRISIS row already exists for the alert_id,
    it won't re-fire. Pre-run cleanup prevents this.
"""

import os
import sys
import time
import json
from datetime import date, datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
import supabase as sb

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
PORTFOLIO_VALUE = 25000.0

# Ticker sentinels — all prefixed ZPM so cleanup is safe
MEMO_TICKER = "ZPMT01"
EXIT_TICKER = "ZPME01"
EARNINGS_TICKER = "ZPMP01"
CRISIS_TICKER = "ZPMK01"
ALL_TICKERS = [MEMO_TICKER, EXIT_TICKER, EARNINGS_TICKER, CRISIS_TICKER]

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"


def ok(msg: str):
    print(f"{GREEN}✓ {msg}{RESET}")


def fail(msg: str):
    print(f"{RED}✗ {msg}{RESET}")


def warn(msg: str):
    print(f"{YELLOW}⚠ {msg}{RESET}")


def header(msg: str):
    print(f"\n{BOLD}{msg}{RESET}")


def get_client():
    return sb.create_client(SUPABASE_URL, SUPABASE_KEY)


# ── Phase 1: Cleanup ──────────────────────────────────────────────────────────

def cleanup(client):
    header("Phase 1: Pre-run cleanup")
    for table in ("memos", "positions", "risk_alerts", "pm_decisions"):
        try:
            resp = client.table(table).delete().like("ticker", "ZPM%").execute()
            count = len(resp.data or [])
            ok(f"Deleted {count} stale row(s) from {table}")
        except Exception as exc:
            warn(f"Cleanup {table} failed — {exc}")

    # Reset pm_config halt state
    try:
        client.table("pm_config").update({
            "daily_loss_halt_triggered": False,
            "halted_until": None,
        }).eq("id", 1).execute()
    except Exception as exc:
        warn(f"pm_config halt reset failed — {exc}")


# ── Phase 2: Seed ─────────────────────────────────────────────────────────────

def seed(client):
    header("Phase 2: Seeding test data")
    today = date.today().isoformat()
    now = datetime.now(timezone.utc).isoformat()

    # 2a. macro_briefings — Risk-On regime
    try:
        client.table("macro_briefings").upsert({
            "date": today,
            "regime": "Risk-On",
            "regime_confidence": 8,
            "regime_score": 75,
            "growth_score": 0.6,
            "inflation_score": -0.1,
            "fed_score": 0.2,
            "stress_score": -0.3,
            "qualitative_summary": "Integration test seed — Risk-On regime.",
            "portfolio_guidance": "Deploy capital into quality growth names.",
            "sector_tilts": {"Technology": 1, "Healthcare": 0},
            "briefing_json": {
                "regime": "Risk-On",
                "regime_confidence": 8,
                "qualitative_summary": "Integration test seed — Risk-On regime.",
            },
        }, on_conflict="date").execute()
        ok("Seeded macro_briefings (Risk-On)")
    except Exception as exc:
        warn(f"macro_briefings seed failed — {exc}")

    # 2b. pm_config — supervised mode, no halt
    try:
        client.table("pm_config").upsert({
            "id": 1,
            "mode": "supervised",
            "cycle_interval_seconds": 300,
            "daily_loss_halt_triggered": False,
            "halted_until": None,
            "daily_research_count": 0,
            "daily_research_date": today,
            "updated_at": now,
        }, on_conflict="id").execute()
        ok("Seeded pm_config (mode=supervised, halt=False)")
    except Exception as exc:
        warn(f"pm_config seed failed — {exc}")

    # 2c. memo ZPMT01 — NEW_ENTRY trigger
    memo_json = {
        "ticker": MEMO_TICKER,
        "verdict": "LONG",
        "conviction_score": 7.5,
        "conviction_score_rationale": "Strong FCF growth, underpenetrated market, low analyst coverage.",
        "variant_perception": (
            "Market prices ZPMT01 as a cyclical; we see it as a recurring-revenue "
            "SaaS pivot that will re-rate on next two quarters of ARR disclosure."
        ),
        "repricing_catalyst": (
            "Q2 earnings (est. 45 days) — first public ARR disclosure expected "
            "to show 80%+ gross retention."
        ),
        "valuation_note": "Trades at 8x NTM revenue vs. peer median 14x. DCF base case implies 40% upside.",
        "cash_runway_months": 24,
        "sector": "Technology",
        "market_cap_m": 450,
        "price_target": 28.0,
        "key_risks": [
            "Customer concentration >30% in top 3 accounts",
            "Sales cycle elongation in SMB",
        ],
    }
    try:
        client.table("memos").insert({
            "ticker": MEMO_TICKER,
            "date": today,
            "verdict": "LONG",
            "conviction_score": 7.5,
            "status": "PENDING_PM_REVIEW",
            "memo_json": memo_json,  # pass dict directly — supabase-py serialises JSONB
            "raw_docs": {},
        }).execute()
        ok(f"Seeded memo {MEMO_TICKER} (PENDING_PM_REVIEW)")
    except Exception as exc:
        fail(f"memo seed failed — {exc}")
        sys.exit(1)

    # 2d. position ZPME01 — EXIT_TRIM trigger (stop proximity 2.8% away)
    try:
        client.table("positions").insert({
            "ticker": EXIT_TICKER,
            "direction": "LONG",
            "status": "OPEN",
            "conviction_score": 6.5,
            "kelly_fraction": 0.05,
            "dollar_size": 1250.0,
            "share_count": 125,
            "size_label": "small",
            "pct_of_portfolio": 0.05,
            "entry_price": 11.0,
            "current_price": 10.0,
            "stop_tier1": 9.72,   # 2.8% below current_price — within 3% trigger
            "stop_tier2": 9.35,
            "stop_tier3": 9.00,
            "stop_loss_price": 9.72,
            "sizing_rationale": "Integration test — stop proximity seed",
            "sector": "Industrials",
            "regime_at_sizing": "Risk-On",
            "opened_at": now,
        }).execute()
        ok(f"Seeded position {EXIT_TICKER} (OPEN, stop proximity 2.8%)")
    except Exception as exc:
        fail(f"position {EXIT_TICKER} seed failed — {exc}")
        sys.exit(1)

    # 2e. position ZPMP01 — PRE_EARNINGS trigger (earnings in 7 days)
    earnings_date = (date.today() + timedelta(days=7)).isoformat()
    try:
        client.table("positions").insert({
            "ticker": EARNINGS_TICKER,
            "direction": "LONG",
            "status": "OPEN",
            "conviction_score": 7.0,
            "kelly_fraction": 0.05,
            "dollar_size": 1250.0,
            "share_count": 62,
            "size_label": "small",
            "pct_of_portfolio": 0.05,
            "entry_price": 18.0,
            "current_price": 20.0,
            "stop_tier1": 16.5,
            "stop_tier2": 15.3,
            "stop_tier3": 14.4,
            "stop_loss_price": 16.5,
            "next_earnings_date": earnings_date,
            "sizing_rationale": "Integration test — pre-earnings seed",
            "sector": "Healthcare",
            "regime_at_sizing": "Risk-On",
            "opened_at": now,
        }).execute()
        ok(f"Seeded position {EARNINGS_TICKER} (OPEN, earnings in 7 days: {earnings_date})")
    except Exception as exc:
        fail(f"position {EARNINGS_TICKER} seed failed — {exc}")
        sys.exit(1)

    # 2f. risk_alert — CRISIS trigger (CRITICAL, unresolved)
    try:
        client.table("risk_alerts").insert({
            "ticker": CRISIS_TICKER,
            "tier": 3,
            "severity": "CRITICAL",
            "trigger": "Integration test: simulated portfolio drawdown exceeding threshold.",
            "regime": "Risk-On",
            "resolved": False,
        }).execute()
        ok(f"Seeded risk_alert {CRISIS_TICKER} (CRITICAL, unresolved)")
    except Exception as exc:
        fail(f"risk_alert seed failed — {exc}")
        sys.exit(1)


# ── Phase 3: Health check ─────────────────────────────────────────────────────

def check_backend():
    header("Phase 3: Backend health check")
    try:
        resp = requests.get(f"{BACKEND_URL}/docs", timeout=5)
        if resp.status_code == 200:
            ok(f"Backend healthy at {BACKEND_URL}")
        else:
            fail(f"Backend returned {resp.status_code} — is uvicorn running?")
            sys.exit(1)
    except requests.ConnectionError:
        fail(f"Cannot connect to {BACKEND_URL}. Start uvicorn first:\n  uvicorn backend.main:app --reload")
        sys.exit(1)


# ── Phase 4: Trigger cycle ────────────────────────────────────────────────────

def trigger_cycle():
    header("Phase 4: Triggering PM cycle")
    warn("APScheduler race window: run within first 5 min of server start to avoid cron interference.")
    url = f"{BACKEND_URL}/pm/cycle/run"
    params = {"portfolio_value": PORTFOLIO_VALUE}
    print(f"  POST {url}?portfolio_value={PORTFOLIO_VALUE}")

    resp = requests.post(url, params=params, timeout=300)
    if resp.status_code != 200:
        fail(f"Cycle endpoint returned {resp.status_code}: {resp.text[:300]}")
        sys.exit(1)

    data = resp.json()
    print(f"\n  Raw cycle response:")
    print(json.dumps(data, indent=2))

    if data.get("skipped"):
        fail(f"Cycle was SKIPPED — reason: {data.get('reason')}")
        fail("Possible causes: state fingerprint matched last cycle, empty portfolio path, or halt active.")
        fail("Fix: restart uvicorn (clears fingerprint) and re-run immediately.")
        sys.exit(1)

    ok(f"Cycle completed — cycle_id={data.get('cycle_id')}, items_evaluated={data.get('items_evaluated')}")
    return data


# ── Phase 5: Assert ───────────────────────────────────────────────────────────

def assert_results(client, cycle_response):
    header("Phase 5: Assertions")
    time.sleep(3)  # allow Supabase writes to propagate

    decisions_resp = (
        client.table("pm_decisions")
        .select("*")
        .in_("ticker", ALL_TICKERS + [None])
        .order("timestamp", desc=True)
        .limit(50)
        .execute()
    )
    all_decisions = decisions_resp.data or []

    # Fetch CRISIS separately (ticker may be null or ZPMK01)
    crisis_resp = (
        client.table("pm_decisions")
        .select("*")
        .eq("category", "CRISIS")
        .order("timestamp", desc=True)
        .limit(10)
        .execute()
    )
    crisis_decisions = crisis_resp.data or []

    passed = 0
    failed_count = 0

    def check(cond: bool, label: str, detail: str = ""):
        nonlocal passed, failed_count
        if cond:
            ok(f"PASS {label}")
            passed += 1
        else:
            fail(f"FAIL {label}" + (f" — {detail}" if detail else ""))
            failed_count += 1

    # A: NEW_ENTRY evaluated for ZPMT01
    new_entry_rows = [d for d in all_decisions if d.get("category") == "NEW_ENTRY" and d.get("ticker") == MEMO_TICKER]
    check(
        bool(new_entry_rows),
        f"A: NEW_ENTRY decision exists for {MEMO_TICKER}",
        f"found {len(new_entry_rows)} matching rows",
    )
    if new_entry_rows:
        valid_statuses = {"PENDING_HUMAN", "DEFERRED", "BLOCKED", "QUEUED_FOR_OPEN", "SENT_TO_EXECUTION"}
        actual_status = new_entry_rows[0].get("execution_status")
        check(
            actual_status in valid_statuses,
            f"A: execution_status '{actual_status}' is in valid set {valid_statuses}",
        )
        print(f"     Claude decision: {new_entry_rows[0].get('decision')} | confidence: {new_entry_rows[0].get('confidence')}")
        print(f"     Reasoning: {(new_entry_rows[0].get('reasoning') or '')[:200]}...")

    # B: EXIT_TRIM evaluated for ZPME01
    exit_rows = [d for d in all_decisions if d.get("category") == "EXIT_TRIM" and d.get("ticker") == EXIT_TICKER]
    check(
        bool(exit_rows),
        f"B: EXIT_TRIM decision exists for {EXIT_TICKER}",
        f"found {len(exit_rows)} matching rows",
    )
    if exit_rows:
        print(f"     Claude decision: {exit_rows[0].get('decision')} | confidence: {exit_rows[0].get('confidence')}")

    # C: PRE_EARNINGS evaluated for ZPMP01
    earnings_rows = [d for d in all_decisions if d.get("category") == "PRE_EARNINGS" and d.get("ticker") == EARNINGS_TICKER]
    check(
        bool(earnings_rows),
        f"C: PRE_EARNINGS decision exists for {EARNINGS_TICKER}",
        f"found {len(earnings_rows)} matching rows",
    )
    if earnings_rows:
        print(f"     Claude decision: {earnings_rows[0].get('decision')} | confidence: {earnings_rows[0].get('confidence')}")

    # D: CRISIS evaluated (ticker may be ZPMK01 or None)
    recent_crisis = [
        d for d in crisis_decisions
        if d.get("ticker") == CRISIS_TICKER or d.get("ticker") is None
    ]
    check(
        bool(recent_crisis),
        "D: CRISIS decision exists",
        f"found {len(recent_crisis)} CRISIS rows",
    )
    if recent_crisis:
        print(f"     Claude decision: {recent_crisis[0].get('decision')} | confidence: {recent_crisis[0].get('confidence')}")

    # Cycle-level checks
    items_evaluated = cycle_response.get("items_evaluated", 0)
    check(
        items_evaluated >= 1,
        f"Cycle items_evaluated={items_evaluated} >= 1",
    )

    print(f"\n  Result: {passed} passed, {failed_count} failed")
    return failed_count == 0


# ── Phase 6: Cleanup ──────────────────────────────────────────────────────────

def post_cleanup(client):
    header("Phase 6: Post-run cleanup")
    cleanup(client)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{BOLD}PM Agent Integration Test{RESET}")
    print(f"Backend: {BACKEND_URL}")
    print(f"Portfolio value: ${PORTFOLIO_VALUE:,.0f}")
    print(f"Tickers: {ALL_TICKERS}")

    if not SUPABASE_URL or not SUPABASE_KEY:
        fail("SUPABASE_URL / SUPABASE_KEY not set in .env")
        sys.exit(1)

    client = get_client()

    cleanup(client)
    seed(client)
    check_backend()
    cycle_response = trigger_cycle()
    success = assert_results(client, cycle_response)
    post_cleanup(client)

    print()
    if success:
        print(f"{GREEN}{BOLD}ALL ASSERTIONS PASSED{RESET}")
        sys.exit(0)
    else:
        print(f"{RED}{BOLD}SOME ASSERTIONS FAILED — review logs above{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
