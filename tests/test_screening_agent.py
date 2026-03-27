"""
Unit tests for backend/agents/screening_agent.py

All Supabase calls are mocked via make_supabase_mock() from conftest.py.
No live API calls, no network traffic.

Coverage:
  _read_regime        — happy path, unknown regime, empty result, exception
  _store_results      — upsert contract, batching, beneish_flag sanitization,
                        empty list, client unavailable, batch failure isolation
  _queue_top_n        — skip recently processed, score ordering, n cap,
                        watchlist update, client unavailable, memo fetch error
  _fetch_form4        — insider buy True/False, exception handling, all tickers
  _score_ticker       — required keys, per-scorer exception isolation
  run_screening       — regime override, empty universe, ScreeningAgentError,
                        qualified filtering, form4 cap at 200, store failure
"""

from datetime import date
from unittest.mock import MagicMock, patch, call

import pytest

from tests.conftest import make_supabase_mock
from backend.agents.screening_agent import (
    _read_regime,
    _store_results,
    _queue_top_n_for_research,
    _fetch_form4_for_candidates,
    _score_ticker,
    run_screening,
    ScreeningAgentError,
    _TOP_N_FOR_RESEARCH,
    _RESEARCH_SKIP_DAYS,
)
from backend.screener.scorer import ScreenerResult
from backend.screener.universe import UniverseCandidate

_PATCH_CLIENT = "backend.agents.screening_agent._get_client"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(ticker, score=8.0, excluded=False, beneish_flag="CLEAN") -> ScreenerResult:
    return ScreenerResult(
        ticker=ticker,
        composite_score=0.0 if excluded else score,
        quality_score=score,
        value_score=score,
        momentum_score=score,
        beneish_flag=beneish_flag,
        excluded=excluded,
    )


def _make_candidate(ticker, sector="SaaS") -> UniverseCandidate:
    return UniverseCandidate(ticker=ticker, market_cap_m=500.0, sector=sector, adv_k=1000.0)


def _raw_data(ticker):
    """Minimal raw_data dict that _score_ticker expects."""
    return {
        "ticker": ticker,
        "fmp": {},
        "polygon_financials": {"results": []},
        "price_history": [],
        "yf_info": {},
    }


# ===========================================================================
# _read_regime
# ===========================================================================

def test_read_regime_returns_value_from_supabase():
    mock = make_supabase_mock(execute_data=[{"regime": "Risk-Off"}])
    with patch(_PATCH_CLIENT, return_value=mock):
        assert _read_regime() == "Risk-Off"


@pytest.mark.parametrize("regime", ["Risk-On", "Risk-Off", "Transitional", "Stagflation"])
def test_read_regime_accepts_all_four_valid_regimes(regime):
    mock = make_supabase_mock(execute_data=[{"regime": regime}])
    with patch(_PATCH_CLIENT, return_value=mock):
        assert _read_regime() == regime


def test_read_regime_unknown_string_falls_back_to_risk_on():
    mock = make_supabase_mock(execute_data=[{"regime": "Bear Market"}])
    with patch(_PATCH_CLIENT, return_value=mock):
        assert _read_regime() == "Risk-On"


def test_read_regime_empty_result_returns_risk_on():
    mock = make_supabase_mock(execute_data=[])
    with patch(_PATCH_CLIENT, return_value=mock):
        assert _read_regime() == "Risk-On"


def test_read_regime_supabase_exception_returns_risk_on():
    with patch(_PATCH_CLIENT, side_effect=RuntimeError("no connection")):
        assert _read_regime() == "Risk-On"


def test_read_regime_execute_exception_returns_risk_on():
    mock = make_supabase_mock()
    mock.execute.side_effect = Exception("timeout")
    with patch(_PATCH_CLIENT, return_value=mock):
        assert _read_regime() == "Risk-On"


def test_read_regime_queries_correct_table_with_desc_order():
    """Verify the query chain: table → select → order(desc=True) → limit(1)."""
    mock = make_supabase_mock(execute_data=[{"regime": "Transitional"}])
    with patch(_PATCH_CLIENT, return_value=mock):
        _read_regime()
    mock.table.assert_called_with("macro_briefings")
    mock.select.assert_called_with("regime")
    mock.order.assert_called_with("created_at", desc=True)
    mock.limit.assert_called_with(1)


# ===========================================================================
# _store_results
# ===========================================================================

def test_store_results_calls_upsert_with_on_conflict():
    mock = make_supabase_mock()
    results = [_make_result("AAPL")]
    with patch(_PATCH_CLIENT, return_value=mock):
        _store_results(results, date(2026, 3, 29), "Risk-On")
    _, kwargs = mock.upsert.call_args
    assert kwargs.get("on_conflict") == "run_date,ticker"


def test_store_results_upserted_row_has_expected_keys():
    mock = make_supabase_mock()
    results = [_make_result("MSFT")]
    with patch(_PATCH_CLIENT, return_value=mock):
        _store_results(results, date(2026, 3, 29), "Risk-On")
    batch = mock.upsert.call_args[0][0]
    row = batch[0]
    for key in (
        "run_date", "ticker", "composite_score", "quality_score", "value_score",
        "momentum_score", "rank", "market_cap_m", "adv_k", "sector", "regime",
        "beneish_m_score", "beneish_flag", "insider_signal", "raw_factors",
        "queued_for_research",
    ):
        assert key in row, f"Missing key in upserted row: {key}"


def test_store_results_run_date_is_iso_string():
    mock = make_supabase_mock()
    results = [_make_result("GOOG")]
    with patch(_PATCH_CLIENT, return_value=mock):
        _store_results(results, date(2026, 3, 29), "Risk-On")
    batch = mock.upsert.call_args[0][0]
    assert batch[0]["run_date"] == "2026-03-29"


def test_store_results_batches_250_rows_into_three_calls():
    mock = make_supabase_mock()
    results = [_make_result(f"T{i:03d}") for i in range(250)]
    with patch(_PATCH_CLIENT, return_value=mock):
        _store_results(results, date(2026, 3, 29), "Risk-On")
    assert mock.upsert.call_count == 3  # 100 + 100 + 50


def test_store_results_invalid_beneish_flag_is_sanitized_to_none():
    mock = make_supabase_mock()
    r = _make_result("BAD", beneish_flag="SOME_INVALID_FLAG")
    with patch(_PATCH_CLIENT, return_value=mock):
        _store_results([r], date(2026, 3, 29), "Risk-On")
    batch = mock.upsert.call_args[0][0]
    assert batch[0]["beneish_flag"] is None


def test_store_results_valid_beneish_flags_are_preserved():
    mock = make_supabase_mock()
    for flag in ("EXCLUDED", "FLAGGED", "CLEAN", "INSUFFICIENT_DATA"):
        r = _make_result("T", beneish_flag=flag)
        with patch(_PATCH_CLIENT, return_value=mock):
            _store_results([r], date(2026, 3, 29), "Risk-On")
        batch = mock.upsert.call_args[0][0]
        assert batch[0]["beneish_flag"] == flag


def test_store_results_empty_list_does_not_call_upsert():
    mock = make_supabase_mock()
    with patch(_PATCH_CLIENT, return_value=mock):
        _store_results([], date(2026, 3, 29), "Risk-On")
    mock.upsert.assert_not_called()


def test_store_results_client_unavailable_returns_without_raising():
    with patch(_PATCH_CLIENT, side_effect=RuntimeError("no db")):
        # Must not raise
        _store_results([_make_result("AAPL")], date(2026, 3, 29), "Risk-On")


def test_store_results_one_batch_failure_continues_to_next_batch():
    mock = make_supabase_mock()
    # Make execute raise on first call only
    mock.execute.side_effect = [Exception("batch 0 failed"), MagicMock(data=[])]
    results = [_make_result(f"T{i:03d}") for i in range(150)]
    with patch(_PATCH_CLIENT, return_value=mock):
        _store_results(results, date(2026, 3, 29), "Risk-On")
    # Both batches attempted even though first raised
    assert mock.upsert.call_count == 2


# ===========================================================================
# _queue_top_n_for_research
# ===========================================================================

def test_queue_top_n_skips_recently_approved_ticker():
    mock = make_supabase_mock(execute_data=[{"ticker": "AAPL"}])
    qualified = [
        _make_result("AAPL", score=9.0),
        _make_result("MSFT", score=8.5),
        _make_result("GOOG", score=8.0),
    ]
    with patch(_PATCH_CLIENT, return_value=mock):
        result = _queue_top_n_for_research(qualified, date(2026, 3, 29))
    assert "AAPL" not in result
    assert "MSFT" in result


def test_queue_top_n_skips_recently_watchlisted_ticker():
    mock = make_supabase_mock(execute_data=[{"ticker": "TSLA"}])
    qualified = [_make_result("TSLA", score=9.0), _make_result("NVDA", score=8.0)]
    with patch(_PATCH_CLIENT, return_value=mock):
        result = _queue_top_n_for_research(qualified, date(2026, 3, 29))
    assert "TSLA" not in result
    assert "NVDA" in result


def test_queue_top_n_returns_at_most_top_n():
    mock = make_supabase_mock(execute_data=[])
    # 10 qualified tickers — only TOP_N_FOR_RESEARCH should be queued
    qualified = [_make_result(f"T{i}", score=9.0 - i * 0.1) for i in range(10)]
    with patch(_PATCH_CLIENT, return_value=mock):
        result = _queue_top_n_for_research(qualified, date(2026, 3, 29))
    assert len(result) <= _TOP_N_FOR_RESEARCH


def test_queue_top_n_selects_by_descending_score():
    mock = make_supabase_mock(execute_data=[])
    # Pass qualified out of order — result should be sorted by score desc
    qualified = [
        _make_result("LOW",  score=7.1),
        _make_result("HIGH", score=9.5),
        _make_result("MED",  score=8.0),
    ]
    with patch(_PATCH_CLIENT, return_value=mock):
        result = _queue_top_n_for_research(qualified, date(2026, 3, 29), n=2)
    assert result[0] == "HIGH"
    assert result[1] == "MED"
    assert "LOW" not in result


def test_queue_top_n_empty_qualified_returns_empty_and_no_writes():
    mock = make_supabase_mock(execute_data=[])
    with patch(_PATCH_CLIENT, return_value=mock):
        result = _queue_top_n_for_research([], date(2026, 3, 29))
    assert result == []
    mock.update.assert_not_called()


def test_queue_top_n_client_unavailable_returns_empty():
    with patch(_PATCH_CLIENT, side_effect=RuntimeError("no db")):
        result = _queue_top_n_for_research(
            [_make_result("AAPL")], date(2026, 3, 29)
        )
    assert result == []


def test_queue_top_n_memo_fetch_exception_still_queues():
    """If the memos query raises, we proceed with an empty skip set."""
    mock = make_supabase_mock(execute_data=[])
    mock.execute.side_effect = Exception("memos unavailable")
    qualified = [_make_result("SAFE", score=8.0)]
    with patch(_PATCH_CLIENT, return_value=mock):
        result = _queue_top_n_for_research(qualified, date(2026, 3, 29))
    # Should still return the ticker (no skip set, no watchlist update error)
    assert "SAFE" in result


def test_queue_top_n_updates_watchlist_with_queued_tickers():
    mock = make_supabase_mock(execute_data=[])
    qualified = [_make_result("AAPL", score=9.0)]
    run_date = date(2026, 3, 29)
    with patch(_PATCH_CLIENT, return_value=mock):
        _queue_top_n_for_research(qualified, run_date)
    # update → in_ → eq → execute should have been called
    mock.update.assert_called_once()
    mock.in_.assert_called()
    mock.eq.assert_called_with("run_date", run_date.isoformat())


# ===========================================================================
# _fetch_form4_for_candidates
# ===========================================================================

_PATCH_FORM4 = "backend.fetchers.form4_fetcher.fetch_form4"


def test_fetch_form4_nonempty_result_sets_insider_buy_true():
    with patch(_PATCH_FORM4, return_value=[{"transaction": "P", "amount": 5000}]):
        result = _fetch_form4_for_candidates(["AAPL"])
    assert result["AAPL"]["insider_buy"] is True


def test_fetch_form4_empty_result_sets_insider_buy_false():
    with patch(_PATCH_FORM4, return_value=[]):
        result = _fetch_form4_for_candidates(["MSFT"])
    assert result["MSFT"]["insider_buy"] is False


def test_fetch_form4_exception_sets_insider_buy_false_and_does_not_raise():
    with patch(_PATCH_FORM4, side_effect=Exception("EDGAR timeout")):
        result = _fetch_form4_for_candidates(["GOOG"])
    assert result["GOOG"]["insider_buy"] is False


def test_fetch_form4_processes_all_tickers():
    tickers = ["AAA", "BBB", "CCC"]
    with patch(_PATCH_FORM4, return_value=[]):
        result = _fetch_form4_for_candidates(tickers)
    assert set(result.keys()) == set(tickers)


# ===========================================================================
# _score_ticker
# ===========================================================================

def test_score_ticker_returns_all_required_keys():
    raw = _raw_data("AAPL")
    result = _score_ticker("AAPL", raw)
    for key in ("ticker", "quality", "value", "momentum", "beneish", "fmp", "form4"):
        assert key in result


def test_score_ticker_quality_exception_returns_empty_dict():
    with patch("backend.agents.screening_agent.score_quality", side_effect=ValueError("bad")):
        result = _score_ticker("AAPL", _raw_data("AAPL"))
    assert result["quality"] == {}


def test_score_ticker_value_exception_returns_empty_dict():
    with patch("backend.agents.screening_agent.score_value", side_effect=ValueError("bad")):
        result = _score_ticker("AAPL", _raw_data("AAPL"))
    assert result["value"] == {}


def test_score_ticker_momentum_exception_returns_empty_dict():
    with patch("backend.agents.screening_agent.score_momentum", side_effect=ValueError("bad")):
        result = _score_ticker("AAPL", _raw_data("AAPL"))
    assert result["momentum"] == {}


def test_score_ticker_beneish_exception_returns_insufficient_data():
    with patch("backend.agents.screening_agent.compute_beneish", side_effect=Exception("bad")):
        result = _score_ticker("AAPL", _raw_data("AAPL"))
    assert result["beneish"]["gate_result"] == "INSUFFICIENT_DATA"


def test_score_ticker_all_scorers_raise_never_propagates():
    with (
        patch("backend.agents.screening_agent.score_quality",   side_effect=Exception()),
        patch("backend.agents.screening_agent.score_value",     side_effect=Exception()),
        patch("backend.agents.screening_agent.score_momentum",  side_effect=Exception()),
        patch("backend.agents.screening_agent.compute_beneish", side_effect=Exception()),
    ):
        result = _score_ticker("AAPL", _raw_data("AAPL"))
    assert isinstance(result, dict)


# ===========================================================================
# run_screening — orchestration
# ===========================================================================

_PATCH_BUILD    = "backend.agents.screening_agent.build_universe"
_PATCH_BATCH    = "backend.agents.screening_agent._batch_fetch_ticker_data"
_PATCH_SCORE    = "backend.agents.screening_agent._score_ticker"
_PATCH_FORM4_FN = "backend.agents.screening_agent._fetch_form4_for_candidates"
_PATCH_COMP     = "backend.agents.screening_agent.compute_composite"
_PATCH_STORE    = "backend.agents.screening_agent._store_results"
_PATCH_QUEUE    = "backend.agents.screening_agent._queue_top_n_for_research"
_PATCH_REGIME   = "backend.agents.screening_agent._read_regime"


def _mock_score_ticker_clean(ticker, raw_data):
    """Default _score_ticker stub: CLEAN beneish, has gross_margin."""
    return {
        "ticker":   ticker,
        "quality":  {"raw_values": {"gross_margin": 0.6}},
        "value":    {"raw_values": {}},
        "momentum": {"raw_values": {}, "short_interest_bonus": 0.0},
        "beneish":  {"gate_result": "CLEAN", "m_score": -3.0, "missing_fields": []},
        "fmp":      {},
        "form4":    {"insider_buy": False},
    }


def test_run_screening_regime_override_skips_read_regime():
    with (
        patch(_PATCH_BUILD,    return_value=[_make_candidate("AAPL")]),
        patch(_PATCH_BATCH,    return_value={"AAPL": _raw_data("AAPL")}),
        patch(_PATCH_SCORE,    side_effect=_mock_score_ticker_clean),
        patch(_PATCH_FORM4_FN, return_value={}),
        patch(_PATCH_COMP,     return_value=[_make_result("AAPL", score=8.0)]),
        patch(_PATCH_STORE),
        patch(_PATCH_QUEUE,    return_value=["AAPL"]),
        patch(_PATCH_REGIME)   as mock_regime,
    ):
        run_screening(regime="Stagflation")
    mock_regime.assert_not_called()


def test_run_screening_reads_regime_when_not_provided():
    with (
        patch(_PATCH_BUILD,    return_value=[_make_candidate("AAPL")]),
        patch(_PATCH_BATCH,    return_value={"AAPL": _raw_data("AAPL")}),
        patch(_PATCH_SCORE,    side_effect=_mock_score_ticker_clean),
        patch(_PATCH_FORM4_FN, return_value={}),
        patch(_PATCH_COMP,     return_value=[]) as mock_comp,
        patch(_PATCH_STORE),
        patch(_PATCH_QUEUE,    return_value=[]),
        patch(_PATCH_REGIME,   return_value="Stagflation") as mock_regime,
    ):
        run_screening()
    mock_regime.assert_called_once()
    _, _, passed_regime = mock_comp.call_args[0]
    assert passed_regime == "Stagflation"


def test_run_screening_empty_universe_returns_empty():
    with (
        patch(_PATCH_BUILD,  return_value=[]),
        patch(_PATCH_BATCH)  as mock_batch,
    ):
        result = run_screening(regime="Risk-On")
    assert result == []
    mock_batch.assert_not_called()


def test_run_screening_build_failure_raises_screening_agent_error():
    with (
        patch(_PATCH_BUILD, side_effect=RuntimeError("polygon down")),
    ):
        with pytest.raises(ScreeningAgentError):
            run_screening(regime="Risk-On")


def test_run_screening_returns_only_qualified_tickers():
    """Only tickers with composite_score >= 7.0 and not excluded are returned."""
    with (
        patch(_PATCH_BUILD,    return_value=[_make_candidate("GOOD"), _make_candidate("POOR")]),
        patch(_PATCH_BATCH,    return_value={
            "GOOD": _raw_data("GOOD"), "POOR": _raw_data("POOR"),
        }),
        patch(_PATCH_SCORE,    side_effect=_mock_score_ticker_clean),
        patch(_PATCH_FORM4_FN, return_value={}),
        patch(_PATCH_COMP,     return_value=[
            _make_result("GOOD", score=8.5),
            _make_result("POOR", score=5.0),
        ]),
        patch(_PATCH_STORE),
        patch(_PATCH_QUEUE, return_value=["GOOD"]),
    ):
        result = run_screening(regime="Risk-On")
    tickers = [r["ticker"] for r in result]
    assert "GOOD" in tickers
    assert "POOR" not in tickers


def test_run_screening_excluded_tickers_not_in_return_value():
    excluded = _make_result("FROD", excluded=True)
    with (
        patch(_PATCH_BUILD,    return_value=[_make_candidate("FROD")]),
        patch(_PATCH_BATCH,    return_value={"FROD": _raw_data("FROD")}),
        patch(_PATCH_SCORE,    side_effect=_mock_score_ticker_clean),
        patch(_PATCH_FORM4_FN, return_value={}),
        patch(_PATCH_COMP,     return_value=[excluded]),
        patch(_PATCH_STORE),
        patch(_PATCH_QUEUE,    return_value=[]),
    ):
        result = run_screening(regime="Risk-On")
    assert result == []


def test_run_screening_form4_candidates_capped_at_200():
    """Universe of 300 tickers: form4 fetch must receive at most 200."""
    universe = [_make_candidate(f"T{i:03d}") for i in range(300)]
    raw_map  = {c.ticker: _raw_data(c.ticker) for c in universe}

    def _score_with_margin(ticker, raw_data):
        s = _mock_score_ticker_clean(ticker, raw_data)
        return s  # all have gross_margin ≠ None

    with (
        patch(_PATCH_BUILD,    return_value=universe),
        patch(_PATCH_BATCH,    return_value=raw_map),
        patch(_PATCH_SCORE,    side_effect=_score_with_margin),
        patch(_PATCH_FORM4_FN) as mock_form4,
        patch(_PATCH_COMP,     return_value=[]),
        patch(_PATCH_STORE),
        patch(_PATCH_QUEUE,    return_value=[]),
    ):
        mock_form4.return_value = {}
        run_screening(regime="Risk-On")

    candidates_passed = mock_form4.call_args[0][0]
    assert len(candidates_passed) <= 200


def test_run_screening_excluded_tickers_not_in_form4_candidates():
    """Beneish EXCLUDED tickers must not be passed to fetch_form4."""
    universe = [_make_candidate("CLEAN"), _make_candidate("FROD")]
    raw_map  = {c.ticker: _raw_data(c.ticker) for c in universe}

    def _mock_score(ticker, raw_data):
        if ticker == "FROD":
            return {
                **_mock_score_ticker_clean(ticker, raw_data),
                "beneish": {"gate_result": "EXCLUDED", "m_score": -1.0, "missing_fields": []},
                "quality": {"raw_values": {"gross_margin": 0.4}},
            }
        return _mock_score_ticker_clean(ticker, raw_data)

    with (
        patch(_PATCH_BUILD,    return_value=universe),
        patch(_PATCH_BATCH,    return_value=raw_map),
        patch(_PATCH_SCORE,    side_effect=_mock_score),
        patch(_PATCH_FORM4_FN) as mock_form4,
        patch(_PATCH_COMP,     return_value=[]),
        patch(_PATCH_STORE),
        patch(_PATCH_QUEUE,    return_value=[]),
    ):
        mock_form4.return_value = {}
        run_screening(regime="Risk-On")

    candidates_passed = mock_form4.call_args[0][0]
    assert "FROD" not in candidates_passed
    assert "CLEAN" in candidates_passed


def test_run_screening_store_failure_does_not_abort():
    """_store_results failure must not prevent the function from returning results."""
    with (
        patch(_PATCH_BUILD,    return_value=[_make_candidate("AAPL")]),
        patch(_PATCH_BATCH,    return_value={"AAPL": _raw_data("AAPL")}),
        patch(_PATCH_SCORE,    side_effect=_mock_score_ticker_clean),
        patch(_PATCH_FORM4_FN, return_value={}),
        patch(_PATCH_COMP,     return_value=[_make_result("AAPL", score=8.0)]),
        patch(_PATCH_STORE,    side_effect=Exception("disk full")),
        patch(_PATCH_QUEUE,    return_value=["AAPL"]),
    ):
        result = run_screening(regime="Risk-On")
    # Returns qualified results despite store failure
    assert isinstance(result, list)
