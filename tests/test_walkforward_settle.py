"""Settling walk-forward outcomes under the written resolution rule (rows 2b, 2d, 3a)."""

from dataclasses import dataclass
from datetime import date

import pandas as pd
import pytest

from foxcast.daily import horizon_date
from foxcast.walkforward.settle import Outcome, settle, settled_before

from .walkforward_data import sessions_from

DAYS = sessions_from(date(2020, 3, 2), 30)
DAY = DAYS[0]


def prices(days: list[date], values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"date": days, "close": values, "adj_close": values})


@pytest.mark.unit
def test_return_runs_from_the_forecast_day_close_to_the_horizon_close() -> None:
    values = [100.0 + i for i in range(len(DAYS))]
    outcome = settle(prices(DAYS, values), DAY, horizon=5)
    assert isinstance(outcome, Outcome)
    assert outcome.value == pytest.approx(105.0 / 100.0 - 1.0)
    assert outcome.horizon_day == horizon_date(DAY, 5) == DAYS[5]
    assert outcome.rule == "horizon close"


@pytest.mark.unit
def test_return_uses_adjusted_closes_not_raw_closes() -> None:
    frame = prices(DAYS[:6], [100.0] * 6).assign(adj_close=[50.0, 1, 1, 1, 1, 60.0])
    outcome = settle(frame, DAY, horizon=5)
    assert isinstance(outcome, Outcome)
    assert outcome.value == pytest.approx(0.2)


@pytest.mark.unit
def test_a_stock_delisted_inside_the_window_resolves_to_its_last_traded_price() -> None:
    outcome = settle(prices(DAYS[:3], [100.0, 90.0, 40.0]), DAY, horizon=5)
    assert isinstance(outcome, Outcome)
    assert outcome.value == pytest.approx(-0.6)
    assert outcome.rule == f"last traded price on {DAYS[2]} (no close on the horizon day)"


@pytest.mark.unit
def test_a_stock_halted_on_the_forecast_day_starts_from_the_first_close_after() -> None:
    outcome = settle(prices(DAYS[2:8], [10.0, 11, 12, 13, 14, 15]), DAY, horizon=5)
    assert isinstance(outcome, Outcome)
    assert outcome.value == pytest.approx(13.0 / 10.0 - 1.0)


@pytest.mark.unit
def test_rows_before_the_forecast_day_and_after_the_horizon_are_ignored() -> None:
    frame = prices([date(2020, 2, 3), *DAYS[:6], DAYS[9]], [1.0, 100, 1, 1, 1, 1, 110, 999])
    outcome = settle(frame, DAY, horizon=5)
    assert isinstance(outcome, Outcome)
    assert outcome.value == pytest.approx(0.1)


@pytest.mark.unit
def test_no_price_in_the_window_is_void_with_a_reason() -> None:
    result = settle(prices([date(2020, 2, 3)], [100.0]), DAY, horizon=5)
    assert isinstance(result, str)
    assert result.startswith("void: no traded price")


@pytest.mark.unit
def test_an_empty_history_is_void() -> None:
    assert isinstance(settle(prices([], []), DAY, horizon=5), str)


@pytest.mark.unit
def test_horizon_must_be_positive() -> None:
    with pytest.raises(ValueError, match="horizon"):
        settle(prices(DAYS, [1.0] * len(DAYS)), DAY, horizon=0)


@dataclass(frozen=True)
class _Record:
    horizon_day: date


@pytest.mark.unit
def test_settled_before_keeps_only_outcomes_known_before_the_forecast_day() -> None:
    t = date(2020, 6, 1)
    records = [_Record(date(2020, 5, 29)), _Record(t), _Record(date(2020, 6, 2))]
    assert settled_before(records, t) == (records[0],)
