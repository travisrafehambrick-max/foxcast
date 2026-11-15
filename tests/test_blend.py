"""The blend's fallback rules (forecast 6, MODELS.md row 6, change AW) — durable
behaviour tests, not stub guards.
"""

import pytest

from foxcast.sim import blend
from foxcast.sim.grid import QuantileForecast

from .forecast_contract import make_ingredient


@pytest.mark.unit
def test_blend_at_five_days_is_the_history_forecast() -> None:
    history_fc = make_ingredient("event_aware", 5)
    result = blend.forecast_blend(
        history_fc, make_ingredient("option_corrected", 5), horizon_days=5
    )
    assert isinstance(result, QuantileForecast)
    assert result.forecast_id == "blend"
    assert result.values == history_fc.values
    assert result.detail["blend_source"] == "event_aware"
    assert result.detail["fallback"] == "5-day horizon"


@pytest.mark.unit
def test_blend_falls_back_when_the_chain_is_unavailable() -> None:
    history_fc = make_ingredient("event_aware", 21)
    result = blend.forecast_blend(history_fc, "no option expiry within 7 days", horizon_days=21)
    assert isinstance(result, QuantileForecast)
    assert result.forecast_id == "blend"
    assert result.detail["blend_source"] == "event_aware"
    assert "no option expiry" in str(result.detail["fallback"])


@pytest.mark.unit
def test_blend_is_unavailable_when_history_is() -> None:
    result = blend.forecast_blend(
        "insufficient history", make_ingredient("option_corrected", 21), horizon_days=21
    )
    assert isinstance(result, str)
    assert "insufficient history" in result


@pytest.mark.unit
def test_blend_rejects_an_ingredient_measured_at_a_different_horizon() -> None:
    with pytest.raises(ValueError, match="horizon"):
        blend.forecast_blend(make_ingredient("event_aware", 5), "no option expiry", horizon_days=21)
