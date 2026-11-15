"""Walk-forward forecast dates: non-overlapping 21-session windows (rows 2b, 2d)."""

import itertools
from datetime import date

import exchange_calendars as xcals
import pandas as pd
import pytest

from foxcast.daily import horizon_date
from foxcast.snapshot import EXCHANGE
from foxcast.walkforward.schedule import (
    FINAL_END,
    FINAL_START,
    TUNING_END,
    TUNING_START,
    WINDOW_SESSIONS,
    final_test_dates,
    forecast_dates,
    tuning_dates,
)


@pytest.mark.unit
def test_each_window_ends_where_the_next_begins() -> None:
    days = forecast_dates(date(2019, 1, 1), date(2019, 12, 31))
    assert len(days) > 5
    for day, following in itertools.pairwise(days):
        assert horizon_date(day, WINDOW_SESSIONS) == following


@pytest.mark.unit
def test_every_forecast_date_is_a_trading_session() -> None:
    calendar = xcals.get_calendar(EXCHANGE)
    for day in forecast_dates(date(2019, 1, 1), date(2019, 6, 30)):
        assert calendar.is_session(pd.Timestamp(day))


@pytest.mark.unit
def test_the_last_window_settles_inside_the_range() -> None:
    end = date(2019, 12, 31)
    days = forecast_dates(date(2019, 1, 1), end)
    assert horizon_date(days[-1], WINDOW_SESSIONS) <= end


@pytest.mark.unit
def test_tuning_outcomes_all_settle_before_the_final_test_period() -> None:
    days = tuning_dates()
    assert days[0] >= TUNING_START
    assert horizon_date(days[-1], WINDOW_SESSIONS) <= TUNING_END < FINAL_START


@pytest.mark.unit
def test_final_test_dates_lie_inside_the_final_period_only() -> None:
    days = final_test_dates()
    assert days[0] >= FINAL_START
    assert horizon_date(days[-1], WINDOW_SESSIONS) <= FINAL_END
    assert not set(days) & set(tuning_dates())


@pytest.mark.unit
def test_a_range_shorter_than_one_window_has_no_dates() -> None:
    assert forecast_dates(date(2019, 1, 2), date(2019, 1, 20)) == ()


@pytest.mark.unit
def test_an_empty_or_backwards_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="after"):
        forecast_dates(date(2019, 6, 1), date(2019, 6, 1))


@pytest.mark.unit
def test_window_must_be_positive() -> None:
    with pytest.raises(ValueError, match="window"):
        forecast_dates(date(2019, 1, 1), date(2019, 12, 31), window=0)
