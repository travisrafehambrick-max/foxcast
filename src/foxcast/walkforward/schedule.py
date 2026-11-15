"""Walk-forward forecast dates (scorecard row 2b, change AS).

There is one forecast date every 21 sessions, so each outcome window ends where the next
one starts and no two windows overlap. Tuning dates come from 2014-2022 only, and their
outcomes also settle inside that period. The 2023-2026 final test is a separate call made
by name, because it is run once.
"""

from datetime import date
from typing import Final

import exchange_calendars as xcals
import pandas as pd

from foxcast.snapshot import EXCHANGE

WINDOW_SESSIONS: Final = 21
TUNING_START: Final = date(2014, 1, 1)
TUNING_END: Final = date(2022, 12, 31)
FINAL_START: Final = date(2023, 1, 1)
FINAL_END: Final = date(2026, 12, 31)


def forecast_dates(start: date, end: date, *, window: int = WINDOW_SESSIONS) -> tuple[date, ...]:
    """Sessions ``window`` apart from the first session on or after ``start``, keeping
    only dates whose ``window``-session outcome settles on or before ``end``."""
    if window < 1:
        raise ValueError(f"window must be at least one session, got {window}")
    if end <= start:
        raise ValueError(f"end {end} must be after start {start}")
    calendar = xcals.get_calendar(EXCHANGE)
    sessions = [
        s.date() for s in calendar.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    ]
    return tuple(sessions[i] for i in range(0, len(sessions) - window, window))


def tuning_dates() -> tuple[date, ...]:
    """Forecast dates for tuning: 2014-2022, with every outcome settled inside it."""
    return forecast_dates(TUNING_START, TUNING_END)


def final_test_dates() -> tuple[date, ...]:
    """Forecast dates for the one recorded final test, 2023-2026 (change AS)."""
    return forecast_dates(FINAL_START, FINAL_END)
