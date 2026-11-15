"""The past-only view every walk-forward input passes through (scorecard row 2d)."""

from datetime import date

import pandas as pd
import pytest

from foxcast.walkforward.past_only import LookaheadError, PastOnly, ensure_before, latest_date

DAYS = [date(2020, 1, d) for d in (2, 3, 6, 7, 8)]


def table() -> pd.DataFrame:
    return pd.DataFrame({"date": DAYS[::-1], "adj_close": [5.0, 4.0, 3.0, 2.0, 1.0]})


@pytest.mark.unit
def test_rows_returns_only_rows_dated_before_the_cutoff_in_date_order() -> None:
    rows = PastOnly(table(), date(2020, 1, 7)).rows()
    assert list(rows["date"]) == DAYS[:3]
    assert list(rows["adj_close"]) == [1.0, 2.0, 3.0]


@pytest.mark.unit
def test_rows_excludes_the_cutoff_day_itself() -> None:
    rows = PastOnly(table(), DAYS[0]).rows()
    assert rows.empty


@pytest.mark.unit
def test_rows_is_a_copy_so_callers_cannot_edit_the_source() -> None:
    source = table()
    rows = PastOnly(source, date(2020, 2, 1)).rows()
    rows.loc[0, "adj_close"] = -1.0
    assert -1.0 not in set(source["adj_close"])


@pytest.mark.unit
def test_rows_honours_a_custom_date_column() -> None:
    frame = table().rename(columns={"date": "filed"})
    rows = PastOnly(frame, date(2020, 1, 3), date_column="filed").rows()
    assert list(rows["filed"]) == [DAYS[0]]


@pytest.mark.unit
def test_latest_date_is_the_newest_row_or_none_when_empty() -> None:
    assert latest_date(table()) == DAYS[-1]
    assert latest_date(table().iloc[0:0]) is None


@pytest.mark.unit
@pytest.mark.parametrize("seen", [date(2020, 1, 7), date(2020, 1, 8)])
def test_ensure_before_raises_on_the_cutoff_day_and_after(seen: date) -> None:
    with pytest.raises(LookaheadError, match="forecast time"):
        ensure_before(seen, date(2020, 1, 7), what="prices")


@pytest.mark.unit
@pytest.mark.parametrize("seen", [None, date(2020, 1, 6)])
def test_ensure_before_accepts_earlier_dates_and_no_input(seen: date | None) -> None:
    ensure_before(seen, date(2020, 1, 7), what="prices")


@pytest.mark.unit
def test_lookahead_error_is_a_value_error() -> None:
    assert issubclass(LookaheadError, ValueError)
