"""
Smoke tests for backend/models/macro_briefing.py

Coverage:
- MacroBriefing instantiates with all required fields
- regime_changed=True with previous_regime set validates correctly
- Invalid regime string raises ValidationError
- regime_confidence defaults to 0.0 when omitted
- IndicatorScore validates and rejects invalid signal values
- SectorTilt validates and rejects invalid tilt values
- MacroBriefing with sector_tilts and upcoming_events included
- Edge case: empty list fields (indicator_scores, key_themes)
"""

import pytest
from pydantic import ValidationError

from backend.models.macro_briefing import (
    MacroBriefing,
    IndicatorScore,
    SectorTilt,
    UpcomingEvent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_briefing(**overrides) -> dict:
    """Return keyword arguments for a minimal valid MacroBriefing."""
    base = {
        "date": "2026-03-30",
        "regime": "Risk-On",
        "regime_score": 72.5,
        "override_flag": False,
        "indicator_scores": [
            IndicatorScore(name="VIX", value=13.5, signal="bullish", note="calm markets"),
        ],
        "qualitative_summary": "Economic conditions remain broadly supportive for equities.",
        "key_themes": ["Strong labor market", "Cooling inflation"],
        "portfolio_guidance": "Maintain full equity exposure; favour cyclicals.",
    }
    base.update(overrides)
    return base


# ===========================================================================
# MacroBriefing — basic validation
# ===========================================================================

def test_macro_briefing_valid_minimal():
    """Full valid MacroBriefing with all required fields instantiates without error."""
    briefing = MacroBriefing(**_minimal_briefing())
    assert briefing.regime == "Risk-On"
    assert briefing.date == "2026-03-30"
    assert briefing.regime_score == 72.5
    assert briefing.override_flag is False


def test_macro_briefing_regime_changed_true_with_previous_regime():
    """regime_changed=True with a valid previous_regime validates correctly."""
    briefing = MacroBriefing(**_minimal_briefing(
        regime="Risk-Off",
        regime_changed=True,
        previous_regime="Risk-On",
    ))
    assert briefing.regime_changed is True
    assert briefing.previous_regime == "Risk-On"
    assert briefing.regime == "Risk-Off"


def test_macro_briefing_invalid_regime_raises_validation_error():
    """Invalid regime string raises ValidationError."""
    with pytest.raises(ValidationError):
        MacroBriefing(**_minimal_briefing(regime="Bull-Market"))


def test_macro_briefing_invalid_previous_regime_raises_validation_error():
    """Invalid previous_regime string raises ValidationError."""
    with pytest.raises(ValidationError):
        MacroBriefing(**_minimal_briefing(previous_regime="Bear-Market"))


def test_macro_briefing_regime_confidence_defaults_to_zero():
    """regime_confidence defaults to 0.0 when not provided."""
    briefing = MacroBriefing(**_minimal_briefing())
    assert briefing.regime_confidence == 0.0


def test_macro_briefing_regime_confidence_set_explicitly():
    """regime_confidence is stored correctly when provided."""
    briefing = MacroBriefing(**_minimal_briefing(regime_confidence=8.5))
    assert briefing.regime_confidence == 8.5


def test_macro_briefing_all_four_regime_values_accepted():
    """All four valid regime strings are accepted."""
    for regime in ("Risk-On", "Risk-Off", "Transitional", "Stagflation"):
        briefing = MacroBriefing(**_minimal_briefing(regime=regime))
        assert briefing.regime == regime


def test_macro_briefing_regime_changed_defaults_to_false():
    """regime_changed defaults to False when omitted."""
    briefing = MacroBriefing(**_minimal_briefing())
    assert briefing.regime_changed is False


def test_macro_briefing_override_reason_optional():
    """override_reason is optional and defaults to None."""
    briefing = MacroBriefing(**_minimal_briefing())
    assert briefing.override_reason is None


def test_macro_briefing_override_reason_stored_when_provided():
    """override_reason is stored when provided alongside override_flag=True."""
    briefing = MacroBriefing(**_minimal_briefing(
        override_flag=True,
        override_reason="Fed speech indicated imminent pivot",
    ))
    assert briefing.override_flag is True
    assert briefing.override_reason == "Fed speech indicated imminent pivot"


def test_macro_briefing_growth_inflation_fed_stress_scores_default_to_zero():
    """Dimensional score fields default to 0.0 when not provided."""
    briefing = MacroBriefing(**_minimal_briefing())
    assert briefing.growth_score == 0.0
    assert briefing.inflation_score == 0.0
    assert briefing.fed_score == 0.0
    assert briefing.stress_score == 0.0


def test_macro_briefing_dimensional_scores_stored_when_provided():
    """Dimensional score fields are stored correctly when explicitly provided."""
    briefing = MacroBriefing(**_minimal_briefing(
        growth_score=0.75,
        inflation_score=0.1,
        fed_score=0.5,
        stress_score=-0.3,
    ))
    assert briefing.growth_score == 0.75
    assert briefing.inflation_score == 0.1
    assert briefing.fed_score == 0.5
    assert briefing.stress_score == -0.3


# ===========================================================================
# MacroBriefing — optional nested fields
# ===========================================================================

def test_macro_briefing_with_sector_tilts():
    """MacroBriefing with sector_tilts list validates correctly."""
    tilts = [
        SectorTilt(sector="SaaS", tilt="overweight", rationale="Strong recurring revenue"),
        SectorTilt(sector="Healthcare", tilt="neutral", rationale="Mixed pricing signals"),
    ]
    briefing = MacroBriefing(**_minimal_briefing(sector_tilts=tilts))
    assert len(briefing.sector_tilts) == 2
    assert briefing.sector_tilts[0].sector == "SaaS"
    assert briefing.sector_tilts[0].tilt == "overweight"


def test_macro_briefing_with_upcoming_events():
    """MacroBriefing with upcoming_events list validates correctly."""
    events = [
        UpcomingEvent(date="2026-04-02", event="FOMC Meeting", relevance="Rate decision"),
    ]
    briefing = MacroBriefing(**_minimal_briefing(upcoming_events=events))
    assert len(briefing.upcoming_events) == 1
    assert briefing.upcoming_events[0].event == "FOMC Meeting"


def test_macro_briefing_sector_tilts_defaults_to_none():
    """sector_tilts defaults to None when not provided."""
    briefing = MacroBriefing(**_minimal_briefing())
    assert briefing.sector_tilts is None


def test_macro_briefing_upcoming_events_defaults_to_none():
    """upcoming_events defaults to None when not provided."""
    briefing = MacroBriefing(**_minimal_briefing())
    assert briefing.upcoming_events is None


# ===========================================================================
# IndicatorScore model validation
# ===========================================================================

def test_indicator_score_valid():
    """IndicatorScore with all valid fields instantiates correctly."""
    ind = IndicatorScore(name="VIX", value=13.5, signal="bullish", note="calm markets")
    assert ind.name == "VIX"
    assert ind.signal == "bullish"
    assert ind.note == "calm markets"


def test_indicator_score_note_optional():
    """IndicatorScore note defaults to None when omitted."""
    ind = IndicatorScore(name="CPI YoY", value=2.1, signal="neutral")
    assert ind.note is None


def test_indicator_score_invalid_signal_raises_validation_error():
    """Invalid signal value raises ValidationError."""
    with pytest.raises(ValidationError):
        IndicatorScore(name="VIX", value=13.5, signal="strong_buy")


def test_indicator_score_all_valid_signals():
    """All three valid signal values are accepted."""
    for signal in ("bullish", "neutral", "bearish"):
        ind = IndicatorScore(name="VIX", value=15.0, signal=signal)
        assert ind.signal == signal


# ===========================================================================
# SectorTilt model validation
# ===========================================================================

def test_sector_tilt_valid():
    """SectorTilt with valid fields instantiates correctly."""
    tilt = SectorTilt(sector="Industrials", tilt="underweight", rationale="PMI contracting")
    assert tilt.sector == "Industrials"
    assert tilt.tilt == "underweight"


def test_sector_tilt_invalid_tilt_raises_validation_error():
    """Invalid tilt value raises ValidationError."""
    with pytest.raises(ValidationError):
        SectorTilt(sector="SaaS", tilt="strong_overweight", rationale="test")


def test_sector_tilt_all_valid_tilts():
    """All three valid tilt values are accepted."""
    for tilt_val in ("overweight", "neutral", "underweight"):
        tilt = SectorTilt(sector="SaaS", tilt=tilt_val, rationale="test reason")
        assert tilt.tilt == tilt_val


# ===========================================================================
# Edge cases
# ===========================================================================

def test_macro_briefing_empty_indicator_scores_list():
    """MacroBriefing with an empty indicator_scores list is valid."""
    briefing = MacroBriefing(**_minimal_briefing(indicator_scores=[]))
    assert briefing.indicator_scores == []


def test_macro_briefing_empty_key_themes_list():
    """MacroBriefing with an empty key_themes list is valid."""
    briefing = MacroBriefing(**_minimal_briefing(key_themes=[]))
    assert briefing.key_themes == []


def test_macro_briefing_previous_regime_none_by_default():
    """previous_regime defaults to None when not provided."""
    briefing = MacroBriefing(**_minimal_briefing())
    assert briefing.previous_regime is None
