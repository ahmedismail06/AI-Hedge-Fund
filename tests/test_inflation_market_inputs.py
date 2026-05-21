"""Tests for ingestion/market_inputs.py — Polygon commodity ETF fetcher.

All tests mock the Polygon HTTP call — no real network requests are made.

Coverage
--------
- All 5 prices extracted correctly from a valid bulk-snapshot response.
- Graceful degradation: Polygon HTTP error → all-None block, no exception.
- Graceful degradation: missing POLYGON_API_KEY → all-None block, no exception.
- Partial response: some tickers missing → missing tickers are None.
- Price extraction: prefers day.c over prevDay.c when both present.
- Price extraction: falls back to prevDay.c when day.c is absent/zero.
- MarketCommoditiesBlock.all_none() constructor.
- fetched_at is always a datetime.
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from backend.macro.inflation_engine.ingestion.market_inputs import (
    MarketCommoditiesBlock,
    _extract_price,
    fetch_commodity_block,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ticker_entry(ticker: str, day_close: float = None, prev_close: float = None) -> dict:
    """Build a Polygon-style snapshot ticker entry."""
    entry = {"ticker": ticker}
    if day_close is not None:
        entry["day"] = {"c": day_close}
    if prev_close is not None:
        entry["prevDay"] = {"c": prev_close}
    return entry


def _mock_polygon_response(tickers_data: list[dict]) -> MagicMock:
    """Build a mock requests.Response for the Polygon bulk snapshot."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"tickers": tickers_data}
    mock_resp.raise_for_status.return_value = None
    return mock_resp


# ---------------------------------------------------------------------------
# _extract_price (unit tests)
# ---------------------------------------------------------------------------


def test_extract_price_prefers_day_close():
    entry = _make_ticker_entry("USO", day_close=75.5, prev_close=74.0)
    assert _extract_price(entry) == pytest.approx(75.5)


def test_extract_price_falls_back_to_prev_day():
    entry = _make_ticker_entry("USO", prev_close=74.0)
    assert _extract_price(entry) == pytest.approx(74.0)


def test_extract_price_zero_day_falls_back():
    """day.c = 0 should be treated as unavailable; fall back to prevDay.c."""
    entry = {"ticker": "USO", "day": {"c": 0}, "prevDay": {"c": 74.0}}
    assert _extract_price(entry) == pytest.approx(74.0)


def test_extract_price_no_data_returns_none():
    entry = {"ticker": "USO"}
    assert _extract_price(entry) is None


def test_extract_price_missing_close_key():
    entry = {"ticker": "USO", "day": {"o": 75.0}, "prevDay": {"o": 74.0}}
    assert _extract_price(entry) is None


# ---------------------------------------------------------------------------
# MarketCommoditiesBlock.all_none()
# ---------------------------------------------------------------------------


def test_all_none_constructor():
    block = MarketCommoditiesBlock.all_none()
    assert block.wti_oil_price is None
    assert block.brent_oil_price is None
    assert block.gasoline_price is None
    assert block.copper_price is None
    assert block.commodity_composite is None
    assert isinstance(block.fetched_at, datetime)


# ---------------------------------------------------------------------------
# fetch_commodity_block — happy path
# ---------------------------------------------------------------------------


@patch.dict(os.environ, {"POLYGON_API_KEY": "test-key"})
@patch("backend.macro.inflation_engine.ingestion.market_inputs.requests.get")
def test_fetch_all_five_prices(mock_get):
    """All 5 tickers returned correctly."""
    tickers_data = [
        _make_ticker_entry("USO",  day_close=75.5),
        _make_ticker_entry("BNO",  day_close=80.0),
        _make_ticker_entry("UGA",  day_close=55.5),
        _make_ticker_entry("CPER", day_close=24.5),
        _make_ticker_entry("DBC",  day_close=22.5),
    ]
    mock_get.return_value = _mock_polygon_response(tickers_data)

    block = fetch_commodity_block()

    assert block.wti_oil_price == pytest.approx(75.5)
    assert block.brent_oil_price == pytest.approx(80.0)
    assert block.gasoline_price == pytest.approx(55.5)
    assert block.copper_price == pytest.approx(24.5)
    assert block.commodity_composite == pytest.approx(22.5)
    assert isinstance(block.fetched_at, datetime)


@patch.dict(os.environ, {"POLYGON_API_KEY": "test-key"})
@patch("backend.macro.inflation_engine.ingestion.market_inputs.requests.get")
def test_fetch_uses_prevday_fallback(mock_get):
    """When day.c absent, prevDay.c is used."""
    tickers_data = [
        _make_ticker_entry("USO", prev_close=73.0),
        _make_ticker_entry("BNO", prev_close=78.0),
        _make_ticker_entry("UGA", prev_close=54.0),
        _make_ticker_entry("CPER", prev_close=23.0),
        _make_ticker_entry("DBC", prev_close=21.0),
    ]
    mock_get.return_value = _mock_polygon_response(tickers_data)

    block = fetch_commodity_block()
    assert block.wti_oil_price == pytest.approx(73.0)
    assert block.copper_price == pytest.approx(23.0)


# ---------------------------------------------------------------------------
# fetch_commodity_block — graceful degradation
# ---------------------------------------------------------------------------


@patch.dict(os.environ, {"POLYGON_API_KEY": "test-key"})
@patch("backend.macro.inflation_engine.ingestion.market_inputs.requests.get")
def test_polygon_http_error_returns_all_none(mock_get):
    """Polygon HTTP error → all-None block, no exception raised."""
    mock_get.side_effect = Exception("Connection refused")

    block = fetch_commodity_block()

    assert block.wti_oil_price is None
    assert block.brent_oil_price is None
    assert block.gasoline_price is None
    assert block.copper_price is None
    assert block.commodity_composite is None


@patch.dict(os.environ, {"POLYGON_API_KEY": "test-key"})
@patch("backend.macro.inflation_engine.ingestion.market_inputs.requests.get")
def test_http_status_error_returns_all_none(mock_get):
    """HTTP non-200 status → all-None block."""
    import requests as req_lib
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = req_lib.exceptions.HTTPError("403 Forbidden")
    mock_get.return_value = mock_resp

    block = fetch_commodity_block()
    assert block.wti_oil_price is None
    assert block.commodity_composite is None


def test_missing_api_key_returns_all_none(monkeypatch):
    """Missing POLYGON_API_KEY → all-None block, no exception."""
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    block = fetch_commodity_block()
    assert block.wti_oil_price is None
    assert block.brent_oil_price is None


# ---------------------------------------------------------------------------
# fetch_commodity_block — partial response
# ---------------------------------------------------------------------------


@patch.dict(os.environ, {"POLYGON_API_KEY": "test-key"})
@patch("backend.macro.inflation_engine.ingestion.market_inputs.requests.get")
def test_partial_response_missing_tickers(mock_get):
    """Only some tickers in response; missing ones are None."""
    tickers_data = [
        _make_ticker_entry("USO", day_close=75.0),
        # BNO, UGA, CPER, DBC all missing from response
    ]
    mock_get.return_value = _mock_polygon_response(tickers_data)

    block = fetch_commodity_block()
    assert block.wti_oil_price == pytest.approx(75.0)
    assert block.brent_oil_price is None
    assert block.gasoline_price is None
    assert block.copper_price is None
    assert block.commodity_composite is None


@patch.dict(os.environ, {"POLYGON_API_KEY": "test-key"})
@patch("backend.macro.inflation_engine.ingestion.market_inputs.requests.get")
def test_empty_tickers_list_all_none(mock_get):
    """Empty tickers list in response → all-None block."""
    mock_get.return_value = _mock_polygon_response([])

    block = fetch_commodity_block()
    assert block.wti_oil_price is None
    assert block.copper_price is None


# ---------------------------------------------------------------------------
# One bulk call only
# ---------------------------------------------------------------------------


@patch.dict(os.environ, {"POLYGON_API_KEY": "test-key"})
@patch("backend.macro.inflation_engine.ingestion.market_inputs.requests.get")
def test_only_one_http_call_made(mock_get):
    """Confirm the function makes exactly one bulk HTTP call, not 5 separate calls."""
    mock_get.return_value = _mock_polygon_response([])

    fetch_commodity_block()
    assert mock_get.call_count == 1


@patch.dict(os.environ, {"POLYGON_API_KEY": "test-key"})
@patch("backend.macro.inflation_engine.ingestion.market_inputs.requests.get")
def test_bulk_call_includes_all_tickers(mock_get):
    """The bulk call URL should include all 5 ticker symbols."""
    mock_get.return_value = _mock_polygon_response([])
    fetch_commodity_block()
    call_args = mock_get.call_args
    url = call_args[0][0] if call_args[0] else call_args.kwargs.get("url", "")
    for ticker in ["USO", "BNO", "UGA", "CPER", "DBC"]:
        assert ticker in url, f"Expected ticker {ticker} in bulk URL: {url}"
