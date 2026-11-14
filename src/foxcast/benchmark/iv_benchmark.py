"""Forecast B, the simple implied-volatility benchmark (MODELS.md row B) — stub.

A distribution built from a single implied-volatility number, the benchmark
outside the numbered family (change BG). The signature mirrors
``forecast_option_implied`` minus ``risk_free_rate``: which strike counts as
at-the-money on a free-data chain is the real model's decision. Today the model
is not built, so every call reports that instead of a number.
"""

from datetime import date
from typing import Final

import pandas as pd

from foxcast.sim.grid import QuantileForecast

NOT_BUILT: Final = "forecast B (simple implied-volatility benchmark) is not built (Stage 2)"


def forecast_iv_benchmark(
    chain: pd.DataFrame,
    *,
    spot: float,
    valuation: date,
    horizon_date: date,
    horizon_days: int | None = None,
) -> QuantileForecast | str:
    """The single-IV benchmark forecast, or the reason it is unavailable."""
    return NOT_BUILT
