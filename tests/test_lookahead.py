"""Scorecard row 2d, "No peeking": no input dated after the forecast time, plus one test
for each fitted piece listed in honesty rule 12 (change AT).

The main check is the poisoned-future test. Corrupt every row dated on or after the
forecast day, rerun, and require a forecast identical value for value. Any lookahead
in the inputs, the fit or the simulation then changes the answer.
"""

import re
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Final

import numpy as np
import pytest

from foxcast.daily import completed_session_returns
from foxcast.sim.naive import forecast_naive
from foxcast.sim.plain_vol import forecast_plain_vol
from foxcast.walkforward.harness import Forecaster, forecasts_on, run_walk_forward
from foxcast.walkforward.past_only import PastOnly
from foxcast.walkforward.pieces import FITTED_PIECES, FittedPiece
from foxcast.walkforward.schedule import forecast_dates
from foxcast.walkforward.settle import settled_before

from .walkforward_data import poison_from, synthetic_prices

ROOT: Final = Path(__file__).resolve().parents[1]
PATHS: Final = 2000
HORIZON: Final = 21
PRICES: Final = synthetic_prices(sessions=900, seed=7)
POISONS: Final = ("scale", "nan", "shuffle")
DAYS: Final = (PRICES["date"].iloc[600], PRICES["date"].iloc[777])


def assert_blind_to_the_future(forecast_id: str, build: Forecaster) -> None:
    for day in DAYS:
        clean = forecasts_on(
            PastOnly(PRICES, day),
            ticker="AAA",
            forecasters={forecast_id: build},
            horizon=HORIZON,
            paths=PATHS,
        )
        assert not isinstance(clean[forecast_id], str), clean[forecast_id]
        for how in POISONS:
            poisoned = forecasts_on(
                PastOnly(poison_from(PRICES, day, how), day),
                ticker="AAA",
                forecasters={forecast_id: build},
                horizon=HORIZON,
                paths=PATHS,
            )
            assert poisoned == clean, f"{forecast_id} on {day} saw the future ({how})"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("forecast_id", "build"), [("naive", forecast_naive), ("plain_vol", forecast_plain_vol)]
)
def test_built_forecasts_ignore_everything_dated_on_or_after_the_forecast_day(
    forecast_id: str, build: Forecaster
) -> None:
    assert_blind_to_the_future(forecast_id, build)


@pytest.mark.unit
def test_no_walk_forward_input_is_dated_on_or_after_its_forecast_day() -> None:
    result = run_walk_forward(
        prices={"AAA": PRICES},
        members_as_of=lambda _: ("AAA",),
        dates=forecast_dates(date(2017, 6, 1), date(2018, 6, 1)),
        forecasters={"naive": forecast_naive},
        horizon=HORIZON,
        paths=PATHS,
    )
    assert len(result.rows) > 5
    for row in result.rows:
        assert row.latest_input < row.day


@pytest.mark.unit
def test_later_dividend_adjustments_leave_earlier_returns_unchanged() -> None:
    # Yahoo back-adjusts every earlier close when a dividend goes ex. Inputs are log
    # returns, so a later adjustment must not move any return before it.
    closes = PRICES["close"].to_numpy()
    ex_day = 700
    factor = np.ones_like(closes)
    factor[:ex_day] = 1.0 - 2.0 / closes[ex_day - 1]
    adjusted_later = PRICES.assign(adj_close=closes * factor)
    day = PRICES["date"].iloc[ex_day - 50]
    np.testing.assert_allclose(
        completed_session_returns(adjusted_later, day),
        completed_session_returns(PRICES, day),
        rtol=0,
        atol=1e-12,
    )


@pytest.mark.unit
@pytest.mark.parametrize("how", POISONS)
def test_the_live_daily_path_ignores_rows_on_or_after_the_day(how: str) -> None:
    day = DAYS[0]
    np.testing.assert_array_equal(
        completed_session_returns(poison_from(PRICES, day, how), day),
        completed_session_returns(PRICES, day),
    )


# ---- One test per fitted piece in honesty rule 12 -------------------------------------


def _check_naive_volatility() -> None:
    assert_blind_to_the_future("naive", forecast_naive)


def _check_volatility_model() -> None:
    assert_blind_to_the_future("plain_vol", forecast_plain_vol)


def _check_conformal_record() -> None:
    # The record at forecast day t holds only outcomes settled before t, so it lags one
    # horizon; outcomes that settle on or after t never enter it.
    result = run_walk_forward(
        prices={"AAA": PRICES},
        members_as_of=lambda _: ("AAA",),
        dates=forecast_dates(date(2017, 6, 1), date(2018, 6, 1)),
        forecasters={"naive": forecast_naive},
        horizon=HORIZON,
        paths=PATHS,
    )
    for row in result.rows:
        record = settled_before(result.rows, row.day)
        assert row not in record
        assert all(r.horizon_day < row.day for r in record)
        assert all(r.day <= row.day - timedelta(days=HORIZON) for r in record)


PIECE_CHECKS: Final[dict[str, Callable[[], None]]] = {
    "naive_volatility": _check_naive_volatility,
    "volatility_model": _check_volatility_model,
    "conformal_record": _check_conformal_record,
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
