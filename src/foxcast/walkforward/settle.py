"""Settling walk-forward outcomes under the written resolution rule (``RESOLUTION_RULE``).

The return runs on split- and dividend-adjusted prices, from the first close on or after
the forecast day (the freeze is intraday) to the close at the horizon. If the horizon day
has no close:

- a stock that trades again later was halted, so it resolves at the first close after
  trading resumes;
- a stock that never trades again was delisted, so it resolves to its last traded price.

An outcome is void only when no price exists. That includes the case where the only close
in the window falls after the forecast day, because then there is no return to measure.
Rows with a missing price count as no price. Settlement is the only step that reads prices
after the forecast day.

Not built: the takeover consideration. A stock taken over resolves to its last traded
price, which is usually close to the deal price but not the same thing. A gap in the data
on the horizon day reads as a halt; the panel cannot tell the two apart.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd

from foxcast.daily import horizon_date

HORIZON_CLOSE = "horizon close"


@dataclass(frozen=True)
class Outcome:
    """A settled simple return and the part of the resolution rule that produced it."""

    value: float
    horizon_day: date
    settled_on: date  # the close the outcome was read from; later than horizon_day after a halt
    rule: str


def _end(after: pd.DataFrame, window: pd.DataFrame, end_day: date) -> tuple[pd.Series, str]:
    on_horizon = window[window["date"] == end_day]
    if not on_horizon.empty:
        return on_horizon.iloc[0], HORIZON_CLOSE
    resumed = after[after["date"] > end_day]
    if not resumed.empty:
        row = resumed.iloc[0]
        return row, f"first close after trading resumed, on {row['date']} (halted at the horizon)"
    row = window.iloc[-1]
    return row, f"last traded price on {row['date']} (no close on the horizon day)"


def settle(prices: pd.DataFrame, day: date, *, horizon: int) -> Outcome | str:
    """The outcome of a forecast made on ``day``, or why it is void."""
    if horizon < 1:
        raise ValueError(f"horizon must be at least one session, got {horizon}")
    end_day = horizon_date(day, horizon)
    priced = prices.dropna(subset=["adj_close"])
    after = priced[priced["date"] >= day].sort_values("date")
    window = after[after["date"] <= end_day]
    if window.empty:
        return f"void: no traded price between {day.isoformat()} and {end_day.isoformat()}"
    first = window.iloc[0]
    last, rule = _end(after, window, end_day)
    if first["date"] > day and last["date"] == first["date"]:
        return f"void: only one close ({first['date']}) between {day.isoformat()} and {end_day}"
    value = float(last["adj_close"]) / float(first["adj_close"]) - 1.0
    return Outcome(value=value, horizon_day=end_day, settled_on=last["date"], rule=rule)


class _Settles(Protocol):
    @property
    def settled_on(self) -> date: ...


def settled_before[S: _Settles](records: Iterable[S], day: date) -> tuple[S, ...]:
    """The outcomes known to a forecast made on ``day``: settled at a close before it.

    This is the input to the conformal record (honesty rule 12). It lags by at least
    one horizon.
    """
    return tuple(r for r in records if r.settled_on < day)
