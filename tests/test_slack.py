"""
Quick smoke test for the Slack notification system.
Run from repo root: python test_slack.py

Requires SLACK_WEBHOOK_URL in .env (or set in shell).
Fires one message per event type — check your Slack channel.
"""

import sys
import os

# Ensure repo root is on the path
sys.path.insert(0, os.path.dirname(__file__))

from backend.notifications.events import notify_event

tests = [
    ("MACRO_BRIEFING_COMPLETE", {
        "regime": "Risk-On",
        "confidence": 8.2,
        "portfolio_guidance": "Bias long, favour high-beta SaaS and Industrials. Standard stops.",
    }),
    ("REGIME_CHANGED", {
        "previous_regime": "Transitional",
        "new_regime": "Risk-On",
        "confidence": 8.2,
        "regime_score": 72.0,
    }),
    ("SCREENING_COMPLETE", {
        "qualified_count": 12,
        "universe_size": 800,
        "regime": "Risk-On",
        "date": "2026-04-11",
        "top_tickers": [
            {"ticker": "ATNI", "score": 8.4},
            {"ticker": "NUVL", "score": 7.9},
            {"ticker": "JANX", "score": 7.6},
        ],
    }),
    ("RESEARCH_QUEUED", {
        "tickers": ["ATNI", "NUVL", "JANX"],
    }),
    ("RESEARCH_MEMO_COMPLETED", {
        "ticker": "ATNI",
        "verdict": "LONG",
        "conviction_score": 8.1,
        "sector": "Industrials",
        "price_target": 42.50,
    }),
    ("PORTFOLIO_SIZING_GENERATED", {
        "ticker": "ATNI",
        "size_label": "medium",
        "dollar_size": 5000.00,
        "pct_of_portfolio": 0.05,
        "conviction_score": 8.1,
        "stop_loss_price": 35.20,
        "regime": "Risk-On",
    }),
    ("POSITION_APPROVED", {
        "ticker": "ATNI",
        "size_label": "medium",
        "dollar_size": 5000.00,
        "share_count": 120,
        "entry_price": 41.67,
    }),
    ("ORDER_PLACED", {
        "ticker": "ATNI",
        "order_type": "LIMIT",
        "qty": 120,
        "limit_price": 41.67,
        "ibkr_order_id": 98765,
    }),
    ("ORDER_FILLED", {
        "ticker": "ATNI",
        "fill_qty": 120,
        "fill_price": 41.70,
        "fill_type": "FULL",
        "slippage_bps": 7.2,
        "commission": 1.00,
    }),
    ("CORRELATION_FLAG", {
        "ticker": "NUVL",
        "rule": "Rule 2 — Sector Concentration",
        "size_before": "medium",
        "size_after": "micro",
        "note": "3 Healthcare positions already >25% gross",
    }),
    ("POSITION_REJECTED", {
        "ticker": "JANX",
        "position_id": "abc-123",
    }),
    ("ORDER_TIMEOUT", {
        "ticker": "NUVL",
        "filled_qty": 40,
        "requested_qty": 100,
        "order_type": "VWAP_30",
    }),
    ("ORCHESTRATOR_MODE_CHANGE", {
        "mode": "AUTONOMOUS",
        "changed_by": "user",
    }),
    ("EXECUTION_CYCLE_COMPLETE", {
        "orders_placed": 2,
        "orders_filled": 1,
        "orders_partial": 1,
        "orders_timeout": 0,
        "orders_error": 0,
    }),
    ("RISK_BREACH", {
        "ticker": "ATNI",
        "trigger": "Tier 2 Sector stop triggered: Industrials at -12.3% avg P&L",
        "regime": "Risk-On",
    }),
    ("RISK_CRITICAL", {
        "ticker": None,
        "trigger": "Tier 3 Portfolio stop triggered: portfolio at -5.2% P&L",
        "regime": "Risk-Off",
    }),
    ("DAILY_LOSS_HALT", {
        "drawdown_pct": 10.4,
        "halted_until": "2026-04-11T23:59:59+00:00",
    }),
    ("IBKR_CONNECTION_ERROR", {
        "ticker": "NUVL",
        "error": "Connection refused — TWS not running on port 7497",
    }),
    ("ORDER_ERROR", {
        "ticker": "JANX",
        "error": "Placement error: order rejected by IBKR — insufficient buying power",
    }),
    ("CRISIS_MODE", {
        "daily_loss_pct": 10.4,
        "duration": "4 hours",
    }),
    ("EXECUTION_BLOCKED", {
        "critical_count": "2",
    }),
]

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    webhook = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook:
        print("ERROR: SLACK_WEBHOOK_URL is not set in .env — nothing will be sent.")
        print("Add it to .env and re-run.")
        sys.exit(1)

    # Allow running a single event: python test_slack.py REGIME_CHANGED
    filter_event = sys.argv[1].upper() if len(sys.argv) > 1 else None
    filtered = [(et, p) for et, p in tests if not filter_event or et == filter_event]

    if not filtered:
        print(f"Unknown event type: {filter_event}")
        print("Available:", ", ".join(et for et, _ in tests))
        sys.exit(1)

    print(f"Sending {len(filtered)} notification(s) to Slack...\n")
    for event_type, payload in filtered:
        print(f"  → {event_type}")
        notify_event(event_type, payload)

    print("\nDone. Check your Slack channel.")
