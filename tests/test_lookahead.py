"""Scorecard row 2d, "No peeking": no input dated after the forecast time, plus one test
for each fitted piece listed in honesty rule 12 (change AT).

The main check is the poisoned-future test. It corrupts every row dated on or after the
forecast day, reruns, and requires forecasts identical value for value. It runs the path
the harness uses end to end: the past-only view, the ``ForecastInputs`` built from it, and
the shipped ``FORECASTS`` adapters with their fits and simulations. The forecasts receive
nothing else, so a leak anywhere on that path changes the answer.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd
import pytest

from foxcast.snapshot import DaySnapshot, completed_session_returns
from foxcast.walkforward.harness import BuiltForecasts, forecasts_on, run_walk_forward
from foxcast.walkforward.past_only import LookaheadError, PastOnly
from foxcast.walkforward.pieces import FITTED_PIECES, FittedPiece
from foxcast.walkforward.schedule import forecast_dates

from .walkforward_data import HISTORY_SPECS, poison_from, synthetic_prices

ROOT: Final = Path(__file__).resolve().parents[1]
HORIZON: Final = 21
PRICES: Final = synthetic_prices(sessions=900, seed=7)
POISONS: Final = ("scale", "nan", "shuffle")
DAYS: Final = (PRICES["date"].iloc[600], PRICES["date"].iloc[777])


def built(prices: pd.DataFrame, day: date, forecast_id: str) -> BuiltForecasts:
    specs = tuple(s for s in HISTORY_SPECS if s.forecast_id == forecast_id)
    return forecasts_on(PastOnly(prices, day), ticker="AAA", specs=specs, horizon=HORIZON)


def assert_blind_to_the_future(forecast_id: str) -> None:
    for day in DAYS:
        clean = built(PRICES, day, forecast_id)
        result = clean.results[f"{forecast_id}:{HORIZON}"]
        assert not isinstance(result, str), result
        for how in POISONS:
            poisoned = built(poison_from(PRICES, day, how), day, forecast_id)
            assert poisoned == clean, f"{forecast_id} on {day} saw the future ({how})"


@pytest.mark.unit
@pytest.mark.parametrize("forecast_id", ["naive", "plain_vol"])
def test_shipped_forecasts_ignore_everything_dated_on_or_after_the_forecast_day(
    forecast_id: str,
) -> None:
    assert_blind_to_the_future(forecast_id)


@pytest.mark.unit
def test_no_walk_forward_input_is_dated_on_or_after_its_forecast_day() -> None:
    result = run_walk_forward(
        prices={"AAA": PRICES},
        members_as_of=lambda _: ("AAA",),
        dates=forecast_dates(date(2017, 6, 1), date(2018, 6, 1)),
        specs=HISTORY_SPECS[:1],
        horizon=HORIZON,
    )
    assert len(result.rows) > 5
    assert not result.skipped
    for row in result.rows:
        assert row.latest_input < row.day


@dataclass(frozen=True, eq=False)
class _LeakyView(PastOnly):
    """A view that has regressed and hands over the whole table."""

    def rows(self) -> pd.DataFrame:
        return self.table.sort_values(self.date_column).reset_index(drop=True)


@pytest.mark.unit
def test_a_view_that_leaks_stops_the_run_instead_of_forecasting() -> None:
    with pytest.raises(LookaheadError, match="forecast time"):
        forecasts_on(
            _LeakyView(PRICES, DAYS[0]), ticker="AAA", specs=HISTORY_SPECS, horizon=HORIZON
        )


def _back_adjust(closes: np.ndarray, dividends: list[tuple[int, float]]) -> np.ndarray:
    """Yahoo-style adjusted closes: each dividend scales every earlier close."""
    factor = np.ones_like(closes)
    for ex_index, amount in dividends:
        factor[:ex_index] *= 1.0 - amount / closes[ex_index - 1]
    return closes * factor


@pytest.mark.unit
def test_dividends_after_the_forecast_day_do_not_change_its_inputs() -> None:
    # The adjusted series fetched today carries every later dividend. A forecast on day t
    # must see the same returns as the series known on t, which is adjusted only for
    # dividends before t, including the return across that earlier ex-date.
    closes = PRICES["close"].to_numpy()
    earlier, later, t = (300, 1.5), (700, 2.0), 650
    day = PRICES["date"].iloc[t]
    known_then = completed_session_returns(
        PRICES.assign(adj_close=_back_adjust(closes, [earlier])), day
    )
    known_today = completed_session_returns(
        PRICES.assign(adj_close=_back_adjust(closes, [earlier, later])), day
    )
    np.testing.assert_allclose(known_today, known_then, rtol=0, atol=1e-12)
    raw = completed_session_returns(PRICES, day)
    assert known_then[earlier[0] - 1] != pytest.approx(raw[earlier[0] - 1])  # dividend counted


@pytest.mark.unit
@pytest.mark.parametrize("how", POISONS)
def test_the_live_snapshot_returns_ignore_rows_on_or_after_the_day(how: str) -> None:
    day = DAYS[0]

    def returns(prices: pd.DataFrame) -> np.ndarray:
        return DaySnapshot(day=day, ticker="AAA", prices=prices, chains=None).log_returns

    np.testing.assert_array_equal(returns(poison_from(PRICES, day, how)), returns(PRICES))


# ---- One test per fitted piece in honesty rule 12 -------------------------------------

PIECE_CHECKS: Final[dict[str, Callable[[], None]]] = {
    "naive_volatility": lambda: assert_blind_to_the_future("naive"),
    "volatility_model": lambda: assert_blind_to_the_future("plain_vol"),
}


@pytest.mark.unit
@pytest.mark.parametrize("piece", FITTED_PIECES, ids=lambda p: p.name)
def test_each_fitted_piece_is_refit_from_the_past_only(piece: FittedPiece) -> None:
    if not piece.built:
        pytest.skip(f"{piece.name}: {piece.note}")
    PIECE_CHECKS[piece.name]()


@pytest.mark.unit
def test_every_built_piece_has_a_lookahead_check_and_no_unbuilt_one_does() -> None:
    assert set(PIECE_CHECKS) == {p.name for p in FITTED_PIECES if p.built}


def rule_12_pieces() -> list[str]:
    text = (ROOT / "SCORECARD.md").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.startswith("12. "))
    listed = line.split("available before that date: ", 1)[1].split(". *(Change AT", 1)[0]
    listed = re.sub(r"\s*\([^)]*\)", "", listed)
    return [re.sub(r"^and ", "", part.strip()) for part in listed.split(", ")]


@pytest.mark.unit
def test_the_registry_names_exactly_the_pieces_honesty_rule_12_lists() -> None:
    named = [p.rule_12 for p in FITTED_PIECES if p.rule_12 is not None]
    assert sorted(named) == sorted(rule_12_pieces())
    assert len(rule_12_pieces()) == 6


@pytest.mark.unit
def test_registry_names_are_unique_and_every_piece_says_why() -> None:
    names = [p.name for p in FITTED_PIECES]
    assert len(names) == len(set(names))
    assert all(p.note.strip() for p in FITTED_PIECES)
