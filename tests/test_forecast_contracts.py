"""The contract every forecast in the family satisfies (scorecard rows 2a-2f).

A forecast call returns a ``QuantileForecast`` on the shared 101-level grid, or a
plain-``str`` reason it is unavailable. These tests pin that contract against the
forecasts already built and, as later stages land, against each new one.
"""

from collections.abc import Sequence
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from foxcast.benchmark.iv_benchmark import forecast_iv_benchmark
from foxcast.benchmark.option_corrected import forecast_option_corrected
from foxcast.benchmark.option_implied import forecast_option_implied
from foxcast.sim.event_aware import forecast_event_aware
from foxcast.sim.naive import forecast_naive
from foxcast.sim.plain_vol import forecast_plain_vol

from .forecast_contract import check_forecast_contract
from .test_forecasts import black_scholes_chain, garch_log_returns

SEED = 20261111


class _IdentityCorrection:
    def adjust(self, values: Sequence[float], levels: Sequence[float]) -> tuple[float, ...]:
        return tuple(values)


@pytest.mark.unit
def test_naive_satisfies_the_forecast_contract() -> None:
    returns = np.random.default_rng(1).normal(0.0, 0.02, size=2000)
    check_forecast_contract(
        forecast_naive(returns, horizon=21, seed=SEED), forecast_id="naive", horizon=21
    )


@pytest.mark.unit
def test_plain_vol_satisfies_the_forecast_contract() -> None:
    check_forecast_contract(
        forecast_plain_vol(garch_log_returns(), horizon=21, seed=SEED),
        forecast_id="plain_vol",
        horizon=21,
    )


@pytest.mark.unit
def test_option_raw_satisfies_the_forecast_contract() -> None:
    valuation = date(2026, 11, 11)
    expiry = valuation + timedelta(days=30)
    chain = black_scholes_chain(100.0, 0.30, valuation, expiry)
    result = forecast_option_implied(
        chain, spot=100.0, valuation=valuation, horizon_date=expiry, horizon_days=21
    )
    check_forecast_contract(result, forecast_id="option_raw", horizon=21)


@pytest.mark.unit
def test_event_aware_satisfies_the_forecast_contract() -> None:
    events = pd.DataFrame({"event_date": [date(2026, 12, 1)], "kind": ["earnings"]})
    result = forecast_event_aware(
        garch_log_returns(), events, forecast_day=date(2026, 11, 14), horizon=21, seed=SEED
    )
    check_forecast_contract(result, forecast_id="event_aware", horizon=21)


@pytest.mark.unit
def test_iv_benchmark_satisfies_the_forecast_contract() -> None:
    valuation = date(2026, 11, 11)
    expiry = valuation + timedelta(days=30)
    chain = black_scholes_chain(100.0, 0.30, valuation, expiry)
    result = forecast_iv_benchmark(
        chain, spot=100.0, valuation=valuation, horizon_date=expiry, horizon_days=21
    )
    check_forecast_contract(result, forecast_id="iv_benchmark", horizon=21)


@pytest.mark.unit
def test_option_corrected_satisfies_the_forecast_contract() -> None:
    valuation = date(2026, 11, 11)
    expiry = valuation + timedelta(days=30)
    chain = black_scholes_chain(100.0, 0.30, valuation, expiry)
    result = forecast_option_corrected(
        chain,
        spot=100.0,
        valuation=valuation,
        horizon_date=expiry,
        correction=_IdentityCorrection(),
        horizon_days=21,
    )
    check_forecast_contract(result, forecast_id="option_corrected", horizon=21)
