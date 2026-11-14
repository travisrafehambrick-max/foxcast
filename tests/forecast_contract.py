"""The contract every forecast in the family satisfies (scorecard rows 2a-2f)."""

from foxcast.sim.grid import QUANTILE_LEVELS, QuantileForecast


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
