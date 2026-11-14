"""Temporary guards for the Stage 2 stubs: each asserts the honest "not built"
reason the stub returns today. When a forecast is implemented, delete its guard
here — the durable contract lives in tests/test_forecast_contracts.py.
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from foxcast.benchmark import iv_benchmark
from foxcast.sim import event_aware

from .test_forecasts import black_scholes_chain, garch_log_returns

SEED = 20261111


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
