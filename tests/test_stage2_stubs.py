"""Temporary guards for the Stage 2 stubs: each asserts the honest "not built"
reason the stub returns today. When a forecast is implemented, delete its guard
here — the durable contract lives in tests/test_forecast_contracts.py.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta

import pandas as pd
import pytest

from foxcast.benchmark import iv_benchmark, option_corrected
from foxcast.sim import blend, event_aware
from foxcast.sim.grid import QuantileForecast
from foxcast.sim.naive import forecast_naive

from .test_forecasts import black_scholes_chain, garch_log_returns, gbm_log_returns

SEED = 20261111


def _ingredient(forecast_id: str, horizon: int) -> QuantileForecast:
    return replace(
        forecast_naive(gbm_log_returns(0.02, 2000), horizon=horizon, seed=SEED),
        forecast_id=forecast_id,
    )


class _IdentityCorrection:
    def adjust(self, values: Sequence[float], levels: Sequence[float]) -> tuple[float, ...]:
        return tuple(values)


@pytest.mark.unit
def test_event_aware_reports_not_built() -> None:
    events = pd.DataFrame({"event_date": [date(2026, 12, 1)], "kind": ["earnings"]})
    result = event_aware.forecast_event_aware(
        garch_log_returns(), events, forecast_day=date(2026, 11, 14), horizon=21, seed=SEED
    )
    assert result == event_aware.NOT_BUILT


@pytest.mark.unit
def test_event_aware_rejects_an_event_calendar_without_required_columns() -> None:
    with pytest.raises(ValueError, match="event_date"):
        event_aware.forecast_event_aware(
            garch_log_returns(),
            pd.DataFrame({"kind": ["earnings"]}),
            forecast_day=date(2026, 11, 14),
            horizon=21,
            seed=SEED,
        )


@pytest.mark.unit
def test_iv_benchmark_reports_not_built() -> None:
    valuation = date(2026, 11, 14)
    chain = black_scholes_chain(100.0, 0.30, valuation, valuation + timedelta(days=30))
    result = iv_benchmark.forecast_iv_benchmark(
        chain,
        spot=100.0,
        valuation=valuation,
        horizon_date=valuation + timedelta(days=30),
        horizon_days=21,
    )
    assert result == iv_benchmark.NOT_BUILT


@pytest.mark.unit
def test_option_corrected_reports_not_built() -> None:
    valuation = date(2026, 11, 14)
    chain = black_scholes_chain(100.0, 0.30, valuation, valuation + timedelta(days=30))
    result = option_corrected.forecast_option_corrected(
        chain,
        spot=100.0,
        valuation=valuation,
        horizon_date=valuation + timedelta(days=30),
        correction=_IdentityCorrection(),
        horizon_days=21,
    )
    assert result == option_corrected.NOT_BUILT


@pytest.mark.unit
def test_blend_at_five_days_is_the_history_forecast() -> None:
    history_fc = _ingredient("event_aware", 5)
    result = blend.forecast_blend(history_fc, _ingredient("option_corrected", 5), horizon_days=5)
    assert isinstance(result, QuantileForecast)
    assert result.forecast_id == "blend"
    assert result.values == history_fc.values
    assert result.detail["blend_source"] == "event_aware"
    assert result.detail["fallback"] == "5-day horizon"


@pytest.mark.unit
def test_blend_falls_back_when_the_chain_is_unavailable() -> None:
    history_fc = _ingredient("event_aware", 21)
    result = blend.forecast_blend(history_fc, "no option expiry within 7 days", horizon_days=21)
    assert isinstance(result, QuantileForecast)
    assert result.forecast_id == "blend"
    assert result.detail["blend_source"] == "event_aware"
    assert "no option expiry" in str(result.detail["fallback"])


@pytest.mark.unit
def test_blend_is_unavailable_when_history_is() -> None:
    result = blend.forecast_blend(
        "insufficient history", _ingredient("option_corrected", 21), horizon_days=21
    )
    assert isinstance(result, str)
    assert "insufficient history" in result


@pytest.mark.unit
def test_blend_of_two_forecasts_reports_not_built() -> None:
    result = blend.forecast_blend(
        _ingredient("event_aware", 21), _ingredient("option_corrected", 21), horizon_days=21
    )
    assert result == blend.NOT_BUILT
