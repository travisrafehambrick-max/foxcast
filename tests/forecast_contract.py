"""The contract every forecast in the family satisfies (scorecard rows 2a-2f)."""

from dataclasses import replace

import numpy as np

from foxcast.sim.grid import QUANTILE_LEVELS, QuantileForecast
from foxcast.sim.naive import forecast_naive


def check_forecast_contract(
    result: QuantileForecast | str, *, forecast_id: str, horizon: int
) -> None:
    """Every forecast in the family returns a forecast on the grid or a reason string."""
    assert isinstance(result, (QuantileForecast, str))
    if isinstance(result, str):
        assert result.strip(), "unavailable reason must not be empty"
        return
    assert result.forecast_id == forecast_id
    assert result.horizon_days == horizon
    assert len(result.values) == len(QUANTILE_LEVELS) == 101
    assert list(result.values) == sorted(result.values)


def make_ingredient(forecast_id: str, horizon: int, *, seed: int = 20261111) -> QuantileForecast:
    """A real ``QuantileForecast`` relabelled to ``forecast_id`` — a stand-in blend ingredient."""
    returns = np.random.default_rng(1).normal(0.0, 0.02, size=2000)
    return replace(forecast_naive(returns, horizon=horizon, seed=seed), forecast_id=forecast_id)
