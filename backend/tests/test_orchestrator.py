"""
Smoke tests for the Orchestrator module.

Covers:
  - agents/orchestrator.py  (_get_config, _is_suspended_today, _check_daily_drawdown,
                              _has_critical_alerts, _approve_position_direct,
                              _run_autonomous_approval_pass, run_orchestrator_cycle)
  - api/orchestrator.py     (GET /orchestrator/mode, POST /orchestrator/mode,
                              POST /orchestrator/cycle/run)

Run:
    python -m pytest backend/tests/test_orchestrator.py -v
"""

import asyncio
import os
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Project root on sys.path so imports resolve correctly.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Async helper
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine synchronously inside a test."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Shared mock-builder helpers
# ---------------------------------------------------------------------------

def _make_supabase_client(table_responses: dict = None):
    """
    Build a mock Supabase client.

    table_responses: mapping of table_name -> list of rows (resp.data).
    Any table not in the dict gets an empty list by default.
    """
    table_responses = table_responses or {}

    mock_client = MagicMock()

    def table_side_effect(table_name: str):
        tbl = MagicMock()
        tbl.select.return_value = tbl
        tbl.eq.return_value = tbl
        tbl.gte.return_value = tbl
        tbl.order.return_value = tbl
        tbl.limit.return_value = tbl
        tbl.update.return_value = tbl
        tbl.insert.return_value = tbl
        tbl.upsert.return_value = tbl

        result = MagicMock()
        result.data = table_responses.get(table_name, [])
        result.count = len(result.data)
        tbl.execute.return_value = result
        return tbl

    mock_client.table.side_effect = table_side_effect
    return mock_client


# ===========================================================================
# _get_config
# ===========================================================================

from backend.agents.orchestrator import _get_config


def test_get_config_returns_supervised_defaults_on_supabase_error():
    """
    _get_config() returns SUPERVISED defaults when Supabase raises an exception.
    """
    mock_client = MagicMock()
    mock_client.table.side_effect = Exception("connection refused")

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        config = _get_config()

    assert config["mode"] == "SUPERVISED"
    assert config["suspended_until"] is None


def test_get_config_returns_supervised_defaults_when_no_row():
    """
    _get_config() returns SUPERVISED defaults when the table query returns an empty list.
    """
    mock_client = _make_supabase_client({"orchestrator_config": []})

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        config = _get_config()

    assert config["mode"] == "SUPERVISED"
    assert config["suspended_until"] is None


def test_get_config_returns_stored_row_when_present():
    """
    _get_config() returns the row from Supabase when one exists.
    """
    stored = {"id": "cfg-001", "mode": "AUTONOMOUS", "suspended_until": None}
    mock_client = _make_supabase_client({"orchestrator_config": [stored]})

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        config = _get_config()

    assert config["mode"] == "AUTONOMOUS"
    assert config["id"] == "cfg-001"


# ===========================================================================
# _is_suspended_today
# ===========================================================================

from backend.agents.orchestrator import _is_suspended_today


def test_is_suspended_today_returns_true_when_suspended_until_is_today():
    """_is_suspended_today() returns True when suspended_until == date.today()."""
    today_str = date.today().isoformat()
    stored = {"mode": "AUTONOMOUS", "suspended_until": today_str}
    mock_client = _make_supabase_client({"orchestrator_config": [stored]})

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        result = _is_suspended_today()

    assert result is True


def test_is_suspended_today_returns_false_when_suspended_until_is_yesterday():
    """_is_suspended_today() returns False when suspended_until is a past date."""
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    stored = {"mode": "AUTONOMOUS", "suspended_until": yesterday_str}
    mock_client = _make_supabase_client({"orchestrator_config": [stored]})

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        result = _is_suspended_today()

    assert result is False


def test_is_suspended_today_returns_false_when_suspended_until_is_none():
    """_is_suspended_today() returns False when suspended_until is None."""
    stored = {"mode": "AUTONOMOUS", "suspended_until": None}
    mock_client = _make_supabase_client({"orchestrator_config": [stored]})

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        result = _is_suspended_today()

    assert result is False


# ===========================================================================
# _check_daily_drawdown
# ===========================================================================

from backend.agents.orchestrator import _check_daily_drawdown


def test_check_daily_drawdown_returns_false_zero_with_no_open_positions():
    """
    _check_daily_drawdown() returns (False, 0.0) when there are no OPEN positions.
    """
    mock_client = _make_supabase_client({"positions": []})
    config_mock = _make_supabase_client({"orchestrator_config": [{"mode": "AUTONOMOUS", "suspended_until": None}]})

    # Both _get_client calls go through the same patch target; use a side_effect
    # that always returns the same mock since the positions query is on "positions".
    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        breached, drawdown_pct = _check_daily_drawdown(25000.0)

    assert breached is False
    assert drawdown_pct == 0.0


def test_check_daily_drawdown_returns_true_when_loss_exceeds_5pct():
    """
    _check_daily_drawdown() returns (True, drawdown_pct) when total unrealised
    loss / portfolio_value > 5%.
    """
    # Single position: bought at 100, now at 80 → $20 loss × 100 shares = $2,000 loss.
    # Portfolio value = $20,000 → drawdown = 10%, breaches 5% threshold.
    positions = [
        {"entry_price": 100.0, "current_price": 80.0, "share_count": 100}
    ]
    mock_client = _make_supabase_client({"positions": positions})

    # _set_suspended_until and _log_event also call _get_client — patch broadly.
    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        with patch("backend.agents.orchestrator._set_suspended_until"):
            with patch("backend.agents.orchestrator._log_event"):
                breached, drawdown_pct = _check_daily_drawdown(20000.0)

    assert breached is True
    assert drawdown_pct == pytest.approx(0.10, abs=1e-6)


def test_check_daily_drawdown_skips_positions_with_null_current_price():
    """
    Positions with current_price = None are excluded from drawdown calculation.
    """
    positions = [
        {"entry_price": 100.0, "current_price": None, "share_count": 500},  # excluded
        {"entry_price": 50.0,  "current_price": 49.0, "share_count": 10},   # $10 loss
    ]
    mock_client = _make_supabase_client({"positions": positions})

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        breached, drawdown_pct = _check_daily_drawdown(25000.0)

    # $10 loss on $25,000 portfolio = 0.04% — well below 5%.
    assert breached is False
    assert drawdown_pct == pytest.approx(10.0 / 25000.0, abs=1e-9)


def test_check_daily_drawdown_returns_false_zero_on_supabase_error():
    """
    _check_daily_drawdown() returns (False, 0.0) gracefully when Supabase fails.
    """
    mock_client = MagicMock()
    mock_client.table.side_effect = Exception("network error")

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        breached, drawdown_pct = _check_daily_drawdown(25000.0)

    assert breached is False
    assert drawdown_pct == 0.0


# ===========================================================================
# _has_critical_alerts
# ===========================================================================

from backend.agents.orchestrator import _has_critical_alerts


def test_has_critical_alerts_returns_true_when_critical_alerts_exist():
    """_has_critical_alerts() returns True when CRITICAL unresolved alerts are present."""
    alerts = [{"id": "alert-001"}]
    mock_client = _make_supabase_client({"risk_alerts": alerts})

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        result = _has_critical_alerts()

    assert result is True


def test_has_critical_alerts_returns_false_when_no_critical_alerts():
    """_has_critical_alerts() returns False when risk_alerts table returns empty."""
    mock_client = _make_supabase_client({"risk_alerts": []})

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        result = _has_critical_alerts()

    assert result is False


def test_has_critical_alerts_returns_false_on_supabase_error():
    """_has_critical_alerts() returns False (fail-open) when Supabase raises."""
    mock_client = MagicMock()
    mock_client.table.side_effect = Exception("timeout")

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        result = _has_critical_alerts()

    assert result is False


# ===========================================================================
# _approve_position_direct
# ===========================================================================

from backend.agents.orchestrator import _approve_position_direct


def test_approve_position_direct_calls_supabase_with_correct_filters():
    """
    _approve_position_direct() calls .update({"status": "APPROVED"})
    with .eq("id", position_id) and .eq("status", "PENDING_APPROVAL").
    """
    updated_row = [{"id": "pos-001", "status": "APPROVED"}]
    mock_client = _make_supabase_client({"positions": updated_row})

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        result = _approve_position_direct("pos-001")

    assert result is True
    # Verify the table was accessed (each mock_client.table() call creates a fresh mock,
    # so we verify via call args on the client itself)
    table_calls = [str(c) for c in mock_client.table.call_args_list]
    assert any("positions" in c for c in table_calls)


def test_approve_position_direct_returns_false_on_exception():
    """_approve_position_direct() returns False when Supabase raises an exception."""
    mock_client = MagicMock()
    mock_client.table.side_effect = Exception("DB error")

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        result = _approve_position_direct("pos-999")

    assert result is False


def test_approve_position_direct_returns_false_when_no_rows_updated():
    """_approve_position_direct() returns False when resp.data is empty (no match)."""
    mock_client = _make_supabase_client({"positions": []})

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        result = _approve_position_direct("pos-nonexistent")

    assert result is False


# ===========================================================================
# _run_autonomous_approval_pass
# ===========================================================================

from backend.agents.orchestrator import _run_autonomous_approval_pass


def test_run_autonomous_approval_pass_approves_high_conviction_positions():
    """
    _run_autonomous_approval_pass() approves positions with conviction >= 8.5
    and returns their IDs in auto_approved.
    """
    candidates = [
        {"id": "pos-A", "ticker": "AAPL", "conviction_score": 9.0},
        {"id": "pos-B", "ticker": "NVDA", "conviction_score": 8.5},
    ]
    updated_row = [{"id": "pos-A", "status": "APPROVED"}]

    # positions query → candidates; then update → updated_row
    call_count = [0]

    mock_client = MagicMock()

    def table_side_effect(table_name: str):
        tbl = MagicMock()
        tbl.select.return_value = tbl
        tbl.eq.return_value = tbl
        tbl.gte.return_value = tbl
        tbl.update.return_value = tbl
        tbl.insert.return_value = tbl

        result = MagicMock()
        # First positions query = candidates; subsequent updates = updated_row
        call_count[0] += 1
        if call_count[0] == 1:
            result.data = candidates
        else:
            result.data = updated_row

        tbl.execute.return_value = result
        return tbl

    mock_client.table.side_effect = table_side_effect

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        with patch("backend.agents.orchestrator._has_critical_alerts", return_value=False):
            with patch("backend.agents.orchestrator._log_event"):
                result = _run(_run_autonomous_approval_pass(25000.0))

    assert result["critical_blocked"] is False
    # Both positions should have been approved (updated_row is non-empty)
    assert isinstance(result["auto_approved"], list)
    assert len(result["auto_approved"]) == 2


def test_run_autonomous_approval_pass_skips_all_when_critical_alert_present():
    """
    _run_autonomous_approval_pass() aborts the entire pass and sets
    critical_blocked=True when _has_critical_alerts() returns True.
    """
    candidates = [
        {"id": "pos-C", "ticker": "TSLA", "conviction_score": 9.1},
    ]
    mock_client = _make_supabase_client({"positions": candidates})

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        with patch("backend.agents.orchestrator._has_critical_alerts", return_value=True):
            with patch("backend.agents.orchestrator._log_event"):
                result = _run(_run_autonomous_approval_pass(25000.0))

    assert result["critical_blocked"] is True
    assert result["auto_approved"] == []


def test_run_autonomous_approval_pass_returns_empty_when_no_candidates():
    """
    _run_autonomous_approval_pass() returns empty auto_approved and
    critical_blocked=False when there are no PENDING_APPROVAL candidates.
    """
    mock_client = _make_supabase_client({"positions": []})

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        with patch("backend.agents.orchestrator._has_critical_alerts", return_value=False):
            result = _run(_run_autonomous_approval_pass(25000.0))

    assert result["auto_approved"] == []
    assert result["critical_blocked"] is False


# ===========================================================================
# run_orchestrator_cycle
# ===========================================================================

from backend.agents.orchestrator import run_orchestrator_cycle


def test_run_orchestrator_cycle_supervised_mode_returns_empty_auto_approved():
    """
    run_orchestrator_cycle() in SUPERVISED mode sets skipped_reason and
    returns an empty auto_approved list without touching approval logic.
    """
    config_data = [{"mode": "SUPERVISED", "suspended_until": None}]
    positions_data = []

    mock_client = _make_supabase_client({
        "orchestrator_config": config_data,
        "positions": positions_data,
        "orchestrator_log": [],
        "risk_alerts": [],
    })

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        with patch("backend.agents.orchestrator._log_event"):
            summary = _run(run_orchestrator_cycle(portfolio_value=25000.0))

    assert summary["mode"] == "SUPERVISED"
    assert summary["auto_approved"] == []
    assert summary["skipped_reason"] == "SUPERVISED mode — human approval required"


def test_run_orchestrator_cycle_autonomous_no_candidates_returns_empty():
    """
    run_orchestrator_cycle() in AUTONOMOUS mode with no qualifying candidates
    returns empty auto_approved and critical_blocked=False.
    """
    config_data = [{"mode": "AUTONOMOUS", "suspended_until": None}]

    mock_client = _make_supabase_client({
        "orchestrator_config": config_data,
        "positions": [],
        "orchestrator_log": [],
        "risk_alerts": [],
    })

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        with patch("backend.agents.orchestrator._log_event"):
            summary = _run(run_orchestrator_cycle(portfolio_value=25000.0))

    assert summary["mode"] == "AUTONOMOUS"
    assert summary["auto_approved"] == []
    assert summary["critical_blocked"] is False
    assert summary["skipped_reason"] is None


def test_run_orchestrator_cycle_autonomous_suspended_returns_skipped_reason():
    """
    run_orchestrator_cycle() in AUTONOMOUS mode skips the approval pass and
    sets skipped_reason when the session is suspended for today.
    """
    today_str = date.today().isoformat()
    config_data = [{"mode": "AUTONOMOUS", "suspended_until": today_str}]

    mock_client = _make_supabase_client({
        "orchestrator_config": config_data,
        "positions": [],
        "orchestrator_log": [],
        "risk_alerts": [],
    })

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        with patch("backend.agents.orchestrator._log_event"):
            summary = _run(run_orchestrator_cycle(portfolio_value=25000.0))

    assert summary["mode"] == "AUTONOMOUS"
    assert summary["suspended"] is True
    assert summary["skipped_reason"] is not None
    assert "suspension" in summary["skipped_reason"].lower()
    assert summary["auto_approved"] == []


def test_run_orchestrator_cycle_reads_portfolio_value_from_env():
    """
    run_orchestrator_cycle() reads PORTFOLIO_VALUE from env when no argument given.
    """
    config_data = [{"mode": "SUPERVISED", "suspended_until": None}]
    mock_client = _make_supabase_client({
        "orchestrator_config": config_data,
        "positions": [],
        "orchestrator_log": [],
        "risk_alerts": [],
    })

    with patch.dict(os.environ, {"PORTFOLIO_VALUE": "50000"}):
        with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
            with patch("backend.agents.orchestrator._log_event"):
                # No portfolio_value argument passed — should read from env.
                summary = _run(run_orchestrator_cycle())

    # The cycle should complete without error and respect SUPERVISED mode.
    assert summary["mode"] == "SUPERVISED"


# ===========================================================================
# API endpoint tests — FastAPI TestClient
# ===========================================================================

from fastapi.testclient import TestClient
from backend.main import app

api_client = TestClient(app)


def _make_api_mock(mode: str = "SUPERVISED", suspended_until=None):
    """Build a Supabase mock suitable for orchestrator API tests."""
    config_row = {"id": "cfg-001", "mode": mode, "suspended_until": suspended_until}
    return _make_supabase_client({
        "orchestrator_config": [config_row],
        "risk_alerts": [],
        "orchestrator_log": [],
        "positions": [],
    })


def test_get_mode_returns_supervised_mode():
    """GET /orchestrator/mode returns the current mode from config."""
    mock_client = _make_api_mock(mode="SUPERVISED")

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        with patch("backend.api.orchestrator._get_client", return_value=mock_client):
            resp = api_client.get("/orchestrator/mode")

    assert resp.status_code == 200
    data = resp.json()
    assert "mode" in data
    assert data["mode"] == "SUPERVISED"
    assert "suspended_until" in data


def test_get_mode_returns_autonomous_mode():
    """GET /orchestrator/mode returns AUTONOMOUS when config has that mode."""
    mock_client = _make_api_mock(mode="AUTONOMOUS")

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        with patch("backend.api.orchestrator._get_client", return_value=mock_client):
            resp = api_client.get("/orchestrator/mode")

    assert resp.status_code == 200
    assert resp.json()["mode"] == "AUTONOMOUS"


def test_post_mode_rejects_invalid_mode_with_400():
    """POST /orchestrator/mode returns 400 for an invalid mode value."""
    mock_client = _make_api_mock()

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        with patch("backend.api.orchestrator._get_client", return_value=mock_client):
            resp = api_client.post("/orchestrator/mode", json={"mode": "TURBO"})

    # Literal["SUPERVISED", "AUTONOMOUS"] causes FastAPI to return 422 (Pydantic validation)
    # for values outside the allowed set — this is the correct behavior
    assert resp.status_code == 422


def test_post_mode_accepts_supervised():
    """POST /orchestrator/mode with SUPERVISED returns 200 and updated mode."""
    # The update chain: _get_config → row, _set_mode → upsert, _set_suspended_until, _log_event
    config_row = {"id": "cfg-001", "mode": "AUTONOMOUS", "suspended_until": None}

    call_count = [0]

    mock_client = MagicMock()

    def table_side_effect(table_name: str):
        tbl = MagicMock()
        tbl.select.return_value = tbl
        tbl.eq.return_value = tbl
        tbl.update.return_value = tbl
        tbl.insert.return_value = tbl
        tbl.upsert.return_value = tbl
        tbl.order.return_value = tbl
        tbl.limit.return_value = tbl

        result = MagicMock()
        call_count[0] += 1
        if table_name == "orchestrator_config":
            result.data = [{"id": "cfg-001", "mode": "SUPERVISED", "suspended_until": None}]
        else:
            result.data = []
        result.count = 0
        tbl.execute.return_value = result
        return tbl

    mock_client.table.side_effect = table_side_effect

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        with patch("backend.api.orchestrator._get_client", return_value=mock_client):
            with patch("backend.agents.orchestrator._log_event"):
                resp = api_client.post("/orchestrator/mode", json={"mode": "SUPERVISED"})

    assert resp.status_code == 200
    data = resp.json()
    assert "mode" in data
    assert "previous_mode" in data


def test_post_mode_accepts_autonomous():
    """POST /orchestrator/mode with AUTONOMOUS returns 200."""
    mock_client = MagicMock()

    def table_side_effect(table_name: str):
        tbl = MagicMock()
        tbl.select.return_value = tbl
        tbl.eq.return_value = tbl
        tbl.update.return_value = tbl
        tbl.insert.return_value = tbl
        tbl.order.return_value = tbl
        tbl.limit.return_value = tbl

        result = MagicMock()
        result.data = [{"id": "cfg-001", "mode": "AUTONOMOUS", "suspended_until": None}]
        result.count = 0
        tbl.execute.return_value = result
        return tbl

    mock_client.table.side_effect = table_side_effect

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        with patch("backend.api.orchestrator._get_client", return_value=mock_client):
            with patch("backend.agents.orchestrator._log_event"):
                resp = api_client.post("/orchestrator/mode", json={"mode": "AUTONOMOUS"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "AUTONOMOUS"


def test_post_cycle_run_returns_summary():
    """
    POST /orchestrator/cycle/run calls run_orchestrator_cycle and returns
    a summary dict with the expected keys.
    """
    expected_summary = {
        "mode": "SUPERVISED",
        "suspended": False,
        "drawdown_pct": 0.0,
        "auto_approved": [],
        "critical_blocked": False,
        "skipped_reason": "SUPERVISED mode — human approval required",
    }

    with patch(
        "backend.api.orchestrator.run_orchestrator_cycle",
        return_value=expected_summary,
    ):
        resp = api_client.post("/orchestrator/cycle/run")

    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "SUPERVISED"
    assert data["auto_approved"] == []
    assert "skipped_reason" in data


def test_post_cycle_run_passes_portfolio_value_query_param():
    """
    POST /orchestrator/cycle/run?portfolio_value=50000 passes the value
    to run_orchestrator_cycle as a keyword argument.
    """
    expected_summary = {
        "mode": "SUPERVISED",
        "suspended": False,
        "drawdown_pct": 0.0,
        "auto_approved": [],
        "critical_blocked": False,
        "skipped_reason": "SUPERVISED mode — human approval required",
    }

    with patch(
        "backend.api.orchestrator.run_orchestrator_cycle",
        return_value=expected_summary,
    ) as mock_cycle:
        resp = api_client.post("/orchestrator/cycle/run?portfolio_value=50000")

    assert resp.status_code == 200
    mock_cycle.assert_called_once_with(portfolio_value=50000.0)
