"""
Smoke tests for the Orchestrator module.

Covers:
  - agents/orchestrator.py  (_get_config, _has_critical_alerts, run_orchestrator_cycle)
  - api/orchestrator.py     (GET /orchestrator/mode, POST /orchestrator/mode,
                              POST /orchestrator/cycle/run)

Run:
    python -m pytest backend/tests/test_orchestrator.py -v
"""

import asyncio
import os
import sys
from datetime import date
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

try:
    from fastapi.testclient import TestClient
    from backend.main import app
    api_client = TestClient(app)
    _API_AVAILABLE = True
except Exception:
    api_client = None
    _API_AVAILABLE = False

_skip_api = pytest.mark.skipif(not _API_AVAILABLE, reason="backend app not importable (broker dependency missing)")


def _make_api_mock(mode: str = "SUPERVISED", suspended_until=None):
    """Build a Supabase mock suitable for orchestrator API tests."""
    config_row = {"id": "cfg-001", "mode": mode, "suspended_until": suspended_until}
    return _make_supabase_client({
        "orchestrator_config": [config_row],
        "risk_alerts": [],
        "orchestrator_log": [],
        "positions": [],
    })


@_skip_api
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


@_skip_api
def test_get_mode_returns_autonomous_mode():
    """GET /orchestrator/mode returns AUTONOMOUS when config has that mode."""
    mock_client = _make_api_mock(mode="AUTONOMOUS")

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        with patch("backend.api.orchestrator._get_client", return_value=mock_client):
            resp = api_client.get("/orchestrator/mode")

    assert resp.status_code == 200
    assert resp.json()["mode"] == "AUTONOMOUS"


@_skip_api
def test_post_mode_rejects_invalid_mode_with_400():
    """POST /orchestrator/mode returns 400 for an invalid mode value."""
    mock_client = _make_api_mock()

    with patch("backend.agents.orchestrator._get_client", return_value=mock_client):
        with patch("backend.api.orchestrator._get_client", return_value=mock_client):
            resp = api_client.post("/orchestrator/mode", json={"mode": "TURBO"})

    # Literal["SUPERVISED", "AUTONOMOUS"] causes FastAPI to return 422 (Pydantic validation)
    # for values outside the allowed set — this is the correct behavior
    assert resp.status_code == 422


@_skip_api
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


@_skip_api
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


@_skip_api
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


@_skip_api
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
