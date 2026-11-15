"""Settling walk-forward outcomes under the written resolution rule (``RESOLUTION_RULE``).

The return runs from the first close after the freeze to the close at the horizon, on
split- and dividend-adjusted prices. A stock that stops trading inside the window resolves
to its last traded price, so delistings stay in the record. An outcome is void only when
no price exists. Settlement is the only step that reads prices after the forecast day.

Not built: the takeover consideration. A stock taken over resolves to its last traded
price, which is usually close to the deal price but not the same thing.
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
    rule: str


def settle(prices: pd.DataFrame, day: date, *, horizon: int) -> Outcome | str:
    """The outcome of a forecast made on ``day``, or why it is void."""
    if horizon < 1:
        raise ValueError(f"horizon must be at least one session, got {horizon}")
    end_day = horizon_date(day, horizon)
    window = prices[(prices["date"] >= day) & (prices["date"] <= end_day)].sort_values("date")
    if window.empty:
        return f"void: no traded price between {day.isoformat()} and {end_day.isoformat()}"
    first, last = window.iloc[0], window.iloc[-1]
    rule = (
        HORIZON_CLOSE
        if last["date"] == end_day
        else f"last traded price on {last['date']} (no close on the horizon day)"
    )
    value = float(last["adj_close"]) / float(first["adj_close"]) - 1.0
    return Outcome(value=value, horizon_day=end_day, rule=rule)


class _Settles(Protocol):
    @property
    def horizon_day(self) -> date: ...


def settled_before[S: _Settles](records: Iterable[S], day: date) -> tuple[S, ...]:
    """The outcomes known to a forecast made on ``day``: settled at a close before it.

    This is the conformal record's input (honesty rule 12). It lags by one horizon.
    """
    return tuple(r for r in records if r.horizon_day < day)
