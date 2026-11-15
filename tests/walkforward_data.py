"""Synthetic price panels for the walk-forward and lookahead tests (no network)."""

from datetime import date
from typing import Final

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from foxcast.daily import FORECASTS, ForecastInputs, ForecastSpec
from foxcast.sim.grid import QuantileForecast
from foxcast.sim.naive import forecast_naive
from foxcast.sim.plain_vol import forecast_plain_vol
from foxcast.snapshot import EXCHANGE

START: date = date(2015, 1, 2)


def sessions_from(start: date, count: int) -> list[date]:
    calendar = xcals.get_calendar(EXCHANGE)
    first = calendar.date_to_session(pd.Timestamp(start), direction="next")
    return [s.date() for s in calendar.sessions_window(first, count)]


FAST_PATHS: Final = 2000


def _fast_naive(inputs: ForecastInputs, *, horizon: int, seed: int) -> QuantileForecast | str:
    return forecast_naive(inputs.log_returns, horizon=horizon, seed=seed, paths=FAST_PATHS)


def _fast_plain_vol(inputs: ForecastInputs, *, horizon: int, seed: int) -> QuantileForecast | str:
    return forecast_plain_vol(inputs.log_returns, horizon=horizon, seed=seed, paths=FAST_PATHS)


# The registry's history forecasts at fewer paths, so harness tests stay fast. The row 2d
# suite runs the shipped ``FORECASTS`` registry itself.
FAST_SPECS: Final = (
    ForecastSpec("naive", (5, 21), _fast_naive),
    ForecastSpec("plain_vol", (5, 21), _fast_plain_vol),
)
HISTORY_SPECS: Final = tuple(s for s in FORECASTS if s.forecast_id in {"naive", "plain_vol"})


def synthetic_prices(
    *, sessions: int, seed: int, start: date = START, sigma: float = 0.015
) -> pd.DataFrame:
    """Daily prices shaped like the Yahoo price contract's ``date``/``close``/``adj_close``."""
    days = sessions_from(start, sessions)
    log_returns = np.random.default_rng(seed).standard_t(df=5, size=sessions) * sigma
    adj_close = 100.0 * np.exp(np.cumsum(log_returns))
    return pd.DataFrame({"date": days, "close": adj_close, "adj_close": adj_close})


def poison_from(prices: pd.DataFrame, day: date, how: str) -> pd.DataFrame:
    """The same panel with every row dated on or after ``day`` corrupted."""
    future = prices["date"] >= day
    poisoned = prices.copy()
    if how == "scale":
        poisoned.loc[future, ["close", "adj_close"]] *= 1000.0
    elif how == "nan":
        poisoned.loc[future, ["close", "adj_close"]] = np.nan
    elif how == "shuffle":
        values = poisoned.loc[future, "adj_close"].to_numpy()
        poisoned.loc[future, "adj_close"] = np.random.default_rng(0).permutation(values)
    else:
        raise ValueError(how)
    return poisoned
