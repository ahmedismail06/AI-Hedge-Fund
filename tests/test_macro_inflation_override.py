"""Smoke tests for the inflation-engine → macro-pipeline override path.

After the engine was wired into the live path, the layered InflationScoreResult
becomes authoritative for `inflation_score`. The LLM still produces growth/fed/
stress/regime/qualitative fields, but `inflation_score` is force-overridden
post-LLM with the engine's value, and `regime_score` is recomputed
deterministically from the prompt's documented formula so the persisted row
stays internally consistent.
"""

from __future__ import annotations

from datetime import date
from typing import Optional
from unittest.mock import patch

import pytest

from backend.agents import macro_agent
from backend.macro.inflation_engine.types import (
    Contribution,
    InflationScoreResult,
    LayerOutput,
)


def _fake_engine_result(score: float = 0.55, confidence: float = 0.72) -> InflationScoreResult:
    layer = LayerOutput(
        score=score,
        confidence=confidence,
        contributors=[
            Contribution(
                indicator="cpi_yoy",
                raw_value=3.95,
                signal=0.6,
                weight=0.4,
                confidence=confidence,
                age_days=14,
            )
        ],
        notes=[],
    )
    return InflationScoreResult(
        score=score,
        confidence=confidence,
        layers={
            "structural": layer,
            "momentum": layer,
            "market_expectations": layer,
            "surprise": layer,
        },
        contributors_top5=layer.contributors,
        aggregate_method="static",
        stale_warnings=[],
        diagnostics={},
    )


def test_recompute_regime_score_matches_prompt_formula() -> None:
    """The Python helper must match the formula stated in MACRO_SYSTEM_PROMPT Step 5."""
    # growth=0.67, inflation=0.60, fed=0.10, stress=-0.10 → 54.475
    score = macro_agent._recompute_regime_score(0.67, 0.60, 0.10, -0.10)
    assert score == pytest.approx(54.475, abs=1e-3)


def test_recompute_regime_score_clamped_to_bounds() -> None:
    assert macro_agent._recompute_regime_score(1.0, -1.0, 1.0, -1.0) == 100.0
    assert macro_agent._recompute_regime_score(-1.0, 1.0, -1.0, 1.0) == 0.0


def test_format_inflation_engine_block_when_unavailable_signals_fallback() -> None:
    block = macro_agent._format_inflation_engine_for_llm(None)
    assert "INFLATION ENGINE OUTPUT (AUTHORITATIVE):" in block
    assert "[UNAVAILABLE" in block


def test_format_inflation_engine_block_renders_score_and_layers() -> None:
    result = _fake_engine_result(score=0.42, confidence=0.65)
    block = macro_agent._format_inflation_engine_for_llm(result)
    assert "score      = +0.420" in block
    assert "confidence = 0.65" in block
    # all four layers represented
    for name in ("structural", "momentum", "market_expectations", "surprise"):
        assert name in block
    # at least one top contributor rendered
    assert "cpi_yoy" in block


def _fake_llm_response(inflation_score: float, regime_score: float) -> dict:
    """A minimal, valid LLM response shape used by run_macro_pipeline."""
    return {
        "growth_score": 0.67,
        "inflation_score": inflation_score,
        "fed_score": 0.10,
        "stress_score": -0.10,
        "regime": "Constructive",
        "regime_score": regime_score,
        "regime_confidence": 7.0,
        "fed_tone": 0.0,
        "fed_tone_rationale": "FOMC text unavailable",
        "override_flag": False,
        "override_reason": None,
        "sector_tilts": [
            {"sector": "SaaS", "tilt": "neutral", "rationale": "test"},
            {"sector": "Healthcare", "tilt": "neutral", "rationale": "test"},
            {"sector": "Industrials", "tilt": "overweight", "rationale": "test"},
        ],
        "qualitative_summary": "Test summary.",
        "key_themes": ["t1", "t2"],
        "portfolio_guidance": "Test guidance.",
        "upcoming_events": [
            {"date": "2026-06-10", "event": "CPI", "relevance": "test"},
        ],
    }


@patch.object(macro_agent, "_store_briefing", return_value="test-row-id")
@patch.object(macro_agent, "_read_previous_regime", return_value="Transitional")
@patch.object(macro_agent, "_call_llm")
@patch.object(macro_agent, "_compute_inflation_engine")
@patch.object(macro_agent, "get_fed_text", return_value="")
@patch.object(macro_agent, "fetch_commodity_block")
@patch.object(macro_agent, "fetch_market_block")
@patch.object(macro_agent, "fetch_fred_block")
def test_run_macro_pipeline_overrides_inflation_with_engine_value(
    mock_fred,
    mock_market,
    mock_commodity,
    _mock_fed_text,
    mock_engine,
    mock_call_llm,
    _mock_prev,
    _mock_store,
) -> None:
    """End-to-end: the persisted briefing's inflation_score comes from the engine,
    not the LLM, and regime_score is recomputed from the deterministic formula."""
    from backend.macro.indicators.fred_fetcher import FredBlock
    from backend.macro.indicators.market_fetcher import MarketBlock
    from backend.macro.inflation_engine.ingestion.market_inputs import MarketCommoditiesBlock

    # Minimal fetcher stubs — values are unused because we mock the engine.
    mock_fred.return_value = FredBlock(raw_values={})
    mock_market.return_value = MarketBlock(
        vix=None, dxy=None, spx_price=None, spx_sma_200=None, spx_pct_above_sma=None,
    )
    mock_commodity.return_value = MarketCommoditiesBlock.all_none()

    engine_inflation = 0.55
    mock_engine.return_value = _fake_engine_result(score=engine_inflation, confidence=0.72)

    # LLM returns a deliberately-wrong inflation_score and a stale regime_score.
    # The override path must replace both.
    mock_call_llm.return_value = _fake_llm_response(
        inflation_score=0.20,  # diverges from engine 0.55 by 0.35 (above warn threshold)
        regime_score=70.0,     # stale — will be recomputed
    )

    briefing = macro_agent.run_macro_pipeline()

    # Engine value wins
    assert briefing.inflation_score == pytest.approx(engine_inflation)

    # regime_score recomputed from prompt formula using engine inflation
    expected = macro_agent._recompute_regime_score(0.67, engine_inflation, 0.10, -0.10)
    assert briefing.regime_score == pytest.approx(expected)

    # Other LLM fields preserved
    assert briefing.regime == "Constructive"
    assert briefing.regime_confidence == 7.0
    assert briefing.growth_score == pytest.approx(0.67)


@patch.object(macro_agent, "_store_briefing", return_value="test-row-id")
@patch.object(macro_agent, "_read_previous_regime", return_value="Transitional")
@patch.object(macro_agent, "_call_llm")
@patch.object(macro_agent, "_compute_inflation_engine", return_value=None)
@patch.object(macro_agent, "get_fed_text", return_value="")
@patch.object(macro_agent, "fetch_commodity_block")
@patch.object(macro_agent, "fetch_market_block")
@patch.object(macro_agent, "fetch_fred_block")
def test_run_macro_pipeline_falls_back_to_llm_inflation_when_engine_fails(
    mock_fred,
    mock_market,
    mock_commodity,
    _mock_fed_text,
    _mock_engine,
    mock_call_llm,
    _mock_prev,
    _mock_store,
) -> None:
    """If the engine fails (returns None), the LLM-derived inflation_score is kept
    and regime_score is NOT recomputed — the LLM stays authoritative."""
    from backend.macro.indicators.fred_fetcher import FredBlock
    from backend.macro.indicators.market_fetcher import MarketBlock
    from backend.macro.inflation_engine.ingestion.market_inputs import MarketCommoditiesBlock

    mock_fred.return_value = FredBlock(raw_values={})
    mock_market.return_value = MarketBlock(
        vix=None, dxy=None, spx_price=None, spx_sma_200=None, spx_pct_above_sma=None,
    )
    mock_commodity.return_value = MarketCommoditiesBlock.all_none()

    mock_call_llm.return_value = _fake_llm_response(
        inflation_score=0.33,
        regime_score=58.0,
    )

    briefing = macro_agent.run_macro_pipeline()

    # LLM value preserved (engine unavailable)
    assert briefing.inflation_score == pytest.approx(0.33)
    assert briefing.regime_score == pytest.approx(58.0)
