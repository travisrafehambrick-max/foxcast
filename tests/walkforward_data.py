"""Synthetic price panels for the walk-forward and lookahead tests (no network)."""

from datetime import date

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from foxcast.snapshot import EXCHANGE

START: date = date(2015, 1, 2)


def sessions_from(start: date, count: int) -> list[date]:
    calendar = xcals.get_calendar(EXCHANGE)
    first = calendar.date_to_session(pd.Timestamp(start), direction="next")
    return [s.date() for s in calendar.sessions_window(first, count)]


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
