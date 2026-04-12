"""
Central event dispatcher for Slack notifications.

Usage in any agent:
    from backend.notifications.events import notify_event
    notify_event("RESEARCH_MEMO_COMPLETED", {"ticker": "AAPL", "verdict": "LONG", ...})

notify_event() is fire-and-forget — never raises, never blocks agent execution.
If SLACK_WEBHOOK_URL is unset, calls are silently skipped (debug log only).
"""

import logging

from backend.notifications.slack import (
    COLOR_CRITICAL,
    COLOR_INFO,
    COLOR_SUCCESS,
    COLOR_WARNING,
    post_slack,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def notify_event(event_type: str, payload: dict) -> None:
    """
    Dispatch a Slack notification for the given event type.

    Args:
        event_type: One of the EVENT_* constants defined below.
        payload:    Dict with event-specific fields (see each formatter).
    """
    try:
        formatter = _FORMATTERS.get(event_type)
        if formatter is None:
            logger.warning("notify_event: unknown event_type '%s'", event_type)
            return
        title, fields, color = formatter(payload)
        post_slack(title=title, fields=fields, color=color)
    except Exception as exc:  # noqa: BLE001
        logger.warning("notify_event('%s') failed: %s", event_type, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Formatters  (one per event type)
# Each returns (title: str, fields: list[dict], color: str)
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_regime_changed(p: dict):
    return (
        f"Macro Regime Changed: {p.get('previous_regime')} → {p.get('new_regime')}",
        [
            {"title": "New Regime",      "value": str(p.get("new_regime", "—")),      "short": True},
            {"title": "Previous",        "value": str(p.get("previous_regime", "—")), "short": True},
            {"title": "Confidence",      "value": str(p.get("confidence", "—")),      "short": True},
            {"title": "Regime Score",    "value": str(p.get("regime_score", "—")),    "short": True},
        ],
        COLOR_INFO,
    )


def _fmt_macro_briefing_complete(p: dict):
    return (
        f"Macro Briefing Complete — {p.get('regime', '—')}",
        [
            {"title": "Regime",           "value": str(p.get("regime", "—")),            "short": True},
            {"title": "Confidence",       "value": str(p.get("confidence", "—")),        "short": True},
            {"title": "Portfolio Guidance","value": str(p.get("portfolio_guidance", "—")),"short": False},
        ],
        COLOR_INFO,
    )


def _fmt_screening_complete(p: dict):
    top = p.get("top_tickers", [])
    top_str = ", ".join(
        f"{t['ticker']} ({t['score']:.1f})" if isinstance(t, dict) else str(t)
        for t in top[:5]
    ) or "—"
    return (
        f"Screening Complete — {p.get('qualified_count', 0)} tickers qualified",
        [
            {"title": "Regime",          "value": str(p.get("regime", "—")),           "short": True},
            {"title": "Qualified",       "value": str(p.get("qualified_count", 0)),     "short": True},
            {"title": "Universe Size",   "value": str(p.get("universe_size", "—")),     "short": True},
            {"title": "Date",            "value": str(p.get("date", "—")),              "short": True},
            {"title": "Top Tickers",     "value": top_str,                              "short": False},
        ],
        COLOR_INFO,
    )


def _fmt_research_queued(p: dict):
    tickers = p.get("tickers", [])
    return (
        f"Research Queued — {len(tickers)} ticker(s)",
        [
            {"title": "Tickers",  "value": ", ".join(tickers) or "—", "short": False},
        ],
        COLOR_INFO,
    )


def _fmt_research_memo_completed(p: dict):
    verdict = p.get("verdict", "—")
    conviction = p.get("conviction_score", "—")
    price_target = p.get("price_target")
    fields = [
        {"title": "Verdict",     "value": str(verdict),     "short": True},
        {"title": "Conviction",  "value": f"{conviction}/10" if conviction != "—" else "—", "short": True},
        {"title": "Sector",      "value": str(p.get("sector", "—")), "short": True},
    ]
    if price_target:
        fields.append({"title": "Price Target", "value": f"${price_target:.2f}", "short": True})
    color = COLOR_SUCCESS if verdict == "LONG" else COLOR_WARNING if verdict == "SHORT" else COLOR_INFO
    return (
        f"Research Memo Completed: {p.get('ticker', '—')}",
        fields,
        color,
    )


def _fmt_portfolio_sizing_generated(p: dict):
    return (
        f"Portfolio Sizing Generated: {p.get('ticker', '—')}",
        [
            {"title": "Size Label",      "value": str(p.get("size_label", "—")),        "short": True},
            {"title": "Dollar Size",     "value": f"${p.get('dollar_size', 0):,.0f}",   "short": True},
            {"title": "% of Portfolio",  "value": f"{p.get('pct_of_portfolio', 0)*100:.1f}%", "short": True},
            {"title": "Conviction",      "value": f"{p.get('conviction_score', '—')}/10","short": True},
            {"title": "Regime",          "value": str(p.get("regime", "—")),            "short": True},
            {"title": "Stop Loss",       "value": f"${p.get('stop_loss_price', '—')}",  "short": True},
        ],
        COLOR_INFO,
    )


def _fmt_correlation_flag(p: dict):
    return (
        f"Correlation Flag Fired: {p.get('ticker', '—')}",
        [
            {"title": "Rule",        "value": str(p.get("rule", "—")),          "short": True},
            {"title": "Size Before", "value": str(p.get("size_before", "—")),   "short": True},
            {"title": "Size After",  "value": str(p.get("size_after", "—")),    "short": True},
            {"title": "Note",        "value": str(p.get("note", "—")),          "short": False},
        ],
        COLOR_WARNING,
    )


def _fmt_position_approved(p: dict):
    return (
        f"Position Approved: {p.get('ticker', '—')}",
        [
            {"title": "Size Label",  "value": str(p.get("size_label", "—")),      "short": True},
            {"title": "Dollar Size", "value": f"${p.get('dollar_size', 0):,.0f}", "short": True},
            {"title": "Shares",      "value": str(p.get("share_count", "—")),     "short": True},
            {"title": "Entry Price", "value": f"${p.get('entry_price', '—')}",    "short": True},
        ],
        COLOR_SUCCESS,
    )


def _fmt_position_rejected(p: dict):
    return (
        f"Position Rejected: {p.get('ticker', '—')}",
        [
            {"title": "Position ID", "value": str(p.get("position_id", "—")), "short": False},
        ],
        COLOR_INFO,
    )


def _fmt_order_placed(p: dict):
    return (
        f"Order Placed: {p.get('ticker', '—')}",
        [
            {"title": "Order Type",  "value": str(p.get("order_type", "—")),  "short": True},
            {"title": "Qty",         "value": str(p.get("qty", "—")),         "short": True},
            {"title": "Limit Price", "value": str(p.get("limit_price", "—")), "short": True},
            {"title": "IBKR Order",  "value": str(p.get("ibkr_order_id", "—")),"short": True},
        ],
        COLOR_INFO,
    )


def _fmt_order_filled(p: dict):
    slippage = p.get("slippage_bps")
    fields = [
        {"title": "Fill Qty",    "value": str(p.get("fill_qty", "—")),    "short": True},
        {"title": "Fill Price",  "value": f"${p.get('fill_price', '—')}", "short": True},
        {"title": "Fill Type",   "value": str(p.get("fill_type", "FULL")),"short": True},
    ]
    if slippage is not None:
        fields.append({"title": "Slippage", "value": f"{slippage:.1f} bps", "short": True})
    if p.get("commission"):
        fields.append({"title": "Commission", "value": f"${p['commission']:.2f}", "short": True})
    return (
        f"Order Filled: {p.get('ticker', '—')}",
        fields,
        COLOR_SUCCESS,
    )


def _fmt_order_timeout(p: dict):
    filled = p.get("filled_qty", 0)
    requested = p.get("requested_qty", "—")
    return (
        f"Order Timeout: {p.get('ticker', '—')}",
        [
            {"title": "Filled",    "value": str(filled),    "short": True},
            {"title": "Requested", "value": str(requested), "short": True},
            {"title": "Order Type","value": str(p.get("order_type", "—")), "short": True},
        ],
        COLOR_WARNING,
    )


def _fmt_order_error(p: dict):
    return (
        f"Order Error: {p.get('ticker', '—')}",
        [
            {"title": "Error", "value": str(p.get("error", "—")), "short": False},
        ],
        COLOR_CRITICAL,
    )


def _fmt_execution_blocked(p: dict):
    return (
        "Execution Cycle Blocked by CRITICAL Alert",
        [
            {"title": "Unresolved CRITICAL Alerts", "value": str(p.get("critical_count", "—")), "short": False},
        ],
        COLOR_CRITICAL,
    )


def _fmt_ibkr_connection_error(p: dict):
    return (
        f"IBKR Connection Error: {p.get('ticker', '—')}",
        [
            {"title": "Error", "value": str(p.get("error", "—")), "short": False},
        ],
        COLOR_CRITICAL,
    )


def _fmt_execution_cycle_complete(p: dict):
    return (
        "Execution Cycle Complete",
        [
            {"title": "Placed",   "value": str(p.get("orders_placed", 0)),  "short": True},
            {"title": "Filled",   "value": str(p.get("orders_filled", 0)),  "short": True},
            {"title": "Partial",  "value": str(p.get("orders_partial", 0)), "short": True},
            {"title": "Timeout",  "value": str(p.get("orders_timeout", 0)), "short": True},
            {"title": "Error",    "value": str(p.get("orders_error", 0)),   "short": True},
        ],
        COLOR_INFO,
    )


def _fmt_orchestrator_mode_change(p: dict):
    new_mode = p.get("mode", "—")
    color = COLOR_WARNING if new_mode == "AUTONOMOUS" else COLOR_INFO
    return (
        f"Orchestrator Mode Changed: {new_mode}",
        [
            {"title": "Mode",    "value": str(new_mode),                     "short": True},
            {"title": "Changed By","value": str(p.get("changed_by", "user")),"short": True},
        ],
        color,
    )


def _fmt_daily_loss_halt(p: dict):
    return (
        "Daily Loss Halt Activated",
        [
            {"title": "Drawdown",      "value": f"{p.get('drawdown_pct', 0):.2f}%",  "short": True},
            {"title": "Halted Until",  "value": str(p.get("halted_until", "—")),     "short": True},
        ],
        COLOR_CRITICAL,
    )


def _fmt_crisis_mode(p: dict):
    return (
        "CRISIS MODE — New Entries Halted",
        [
            {"title": "Daily Loss",    "value": f"{p.get('daily_loss_pct', 0):.2f}%","short": True},
            {"title": "Duration",      "value": str(p.get("duration", "4 hours")),   "short": True},
        ],
        COLOR_CRITICAL,
    )


def _fmt_risk_critical(p: dict):
    return (
        f"[CRITICAL] {p.get('trigger', '—')}",
        [
            {"title": "Ticker", "value": str(p.get("ticker", "portfolio")), "short": True},
            {"title": "Regime", "value": str(p.get("regime", "—")),        "short": True},
        ],
        COLOR_CRITICAL,
    )


def _fmt_risk_breach(p: dict):
    return (
        f"[BREACH] {p.get('trigger', '—')}",
        [
            {"title": "Ticker", "value": str(p.get("ticker", "portfolio")), "short": True},
            {"title": "Regime", "value": str(p.get("regime", "—")),        "short": True},
        ],
        COLOR_WARNING,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Formatter registry
# ─────────────────────────────────────────────────────────────────────────────

_FORMATTERS = {
    # Macro
    "REGIME_CHANGED":             _fmt_regime_changed,
    "MACRO_BRIEFING_COMPLETE":    _fmt_macro_briefing_complete,
    # Screener
    "SCREENING_COMPLETE":         _fmt_screening_complete,
    "RESEARCH_QUEUED":            _fmt_research_queued,
    # Research
    "RESEARCH_MEMO_COMPLETED":    _fmt_research_memo_completed,
    # Portfolio
    "PORTFOLIO_SIZING_GENERATED": _fmt_portfolio_sizing_generated,
    "CORRELATION_FLAG":           _fmt_correlation_flag,
    "POSITION_APPROVED":          _fmt_position_approved,
    "POSITION_REJECTED":          _fmt_position_rejected,
    # Execution
    "ORDER_PLACED":               _fmt_order_placed,
    "ORDER_FILLED":               _fmt_order_filled,
    "ORDER_TIMEOUT":              _fmt_order_timeout,
    "ORDER_ERROR":                _fmt_order_error,
    "EXECUTION_BLOCKED":          _fmt_execution_blocked,
    "IBKR_CONNECTION_ERROR":      _fmt_ibkr_connection_error,
    "EXECUTION_CYCLE_COMPLETE":   _fmt_execution_cycle_complete,
    # Orchestrator
    "ORCHESTRATOR_MODE_CHANGE":   _fmt_orchestrator_mode_change,
    "DAILY_LOSS_HALT":            _fmt_daily_loss_halt,
    "CRISIS_MODE":                _fmt_crisis_mode,
    # Risk (legacy path — also used by notifier.py refactor)
    "RISK_CRITICAL":              _fmt_risk_critical,
    "RISK_BREACH":                _fmt_risk_breach,
}
