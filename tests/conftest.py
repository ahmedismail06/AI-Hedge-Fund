"""
Shared test infrastructure for the screening system test suite.

Provides:
  make_supabase_mock  — factory for a chainable Supabase client mock
  integration mark    — deselect with: pytest -m "not integration"
"""

import pytest
from unittest.mock import MagicMock


def make_supabase_mock(execute_data=None):
    """
    Return a MagicMock that mimics the supabase-py fluent query API.

    All chainable methods (.table, .select, .order, etc.) return the same
    mock object so arbitrarily long chains always resolve.  `.execute()`
    returns a result whose `.data` attribute is set to *execute_data*
    (defaults to an empty list).

    Usage::

        mock = make_supabase_mock(execute_data=[{"regime": "Risk-Off"}])
        with patch("backend.agents.screening_agent._get_client",
                   return_value=mock):
            result = _read_regime()
        assert result == "Risk-Off"
    """
    client = MagicMock()
    execute_result = MagicMock()
    execute_result.data = execute_data if execute_data is not None else []

    for method in (
        "table", "select", "insert", "update", "upsert", "delete",
        "order", "limit", "eq", "in_", "gte", "lte", "neq",
    ):
        getattr(client, method).return_value = client

    client.execute.return_value = execute_result
    return client


# ---------------------------------------------------------------------------
# pytest markers
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: requires live API keys — deselect with -m 'not integration'",
    )
