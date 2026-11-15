"""The walk-forward harness: refit from the past, forecast, settle, score (rows 2b-2f)."""

import math
from collections.abc import Sequence
from datetime import date
from typing import Final

import numpy as np
import pytest

from foxcast.sim.grid import QuantileForecast
from foxcast.sim.naive import forecast_naive
from foxcast.sim.plain_vol import forecast_plain_vol
from foxcast.walkforward.harness import (
    Forecaster,
    WalkForwardResult,
    paired_scores,
    run_walk_forward,
)
from foxcast.walkforward.schedule import forecast_dates

from .forecast_contract import check_forecast_contract
from .walkforward_data import START, synthetic_prices

PATHS: Final = 2000
HORIZON: Final = 21
PRICES: Final = {
    "AAA": synthetic_prices(sessions=900, seed=1),
    "BBB": synthetic_prices(sessions=900, seed=2, sigma=0.03),
    # Delisted after 700 sessions: a mid-window delisting and void dates after it.
    "CCC": synthetic_prices(sessions=700, seed=3),
}
DATES: Final = forecast_dates(date(2017, 4, 3), date(2018, 7, 31))[::3]
FORECASTERS: Final[dict[str, Forecaster]] = {
    "naive": forecast_naive,
    "plain_vol": forecast_plain_vol,
}


def members(_: date) -> Sequence[str]:
    return ("AAA", "BBB", "CCC")


def run(**overrides: object) -> WalkForwardResult:
    kwargs: dict[str, object] = {
        "prices": PRICES,
        "members_as_of": members,
        "dates": DATES,
        "forecasters": FORECASTERS,
        "horizon": HORIZON,
        "paths": PATHS,
    }
    kwargs.update(overrides)
    return run_walk_forward(**kwargs)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def result() -> WalkForwardResult:
    return run()


@pytest.mark.unit
def test_every_row_is_a_contract_forecast_with_a_finite_score(result: WalkForwardResult) -> None:
    assert result.rows
    for row in result.rows:
        check_forecast_contract(row.forecast, forecast_id=row.forecast.forecast_id, horizon=21)
        assert math.isfinite(row.crps)
        assert row.crps >= 0.0
        assert row.horizon_day > row.day


@pytest.mark.unit
def test_both_forecasters_run_for_every_settled_stock_and_date(result: WalkForwardResult) -> None:
    keys = {(r.day, r.ticker) for r in result.rows}
    for forecast_id in FORECASTERS:
        assert {
            (r.day, r.ticker) for r in result.rows if r.forecast.forecast_id == forecast_id
        } == (keys)


@pytest.mark.unit
def test_a_rerun_reproduces_every_forecast_from_its_seed(result: WalkForwardResult) -> None:
    again = run(forecasters={"naive": forecast_naive})
    naive = {
        (r.day, r.ticker): r.forecast.values
        for r in result.rows
        if r.forecast.forecast_id == "naive"
    }
    assert {(r.day, r.ticker): r.forecast.values for r in again.rows} == naive


@pytest.mark.unit
def test_a_delisted_stock_is_settled_then_void_and_counted(result: WalkForwardResult) -> None:
    last_day = PRICES["CCC"]["date"].iloc[-1]
    ccc = [r for r in result.rows if r.ticker == "CCC"]
    assert ccc
    assert all(r.day <= last_day for r in ccc)
    assert any(r.resolution.startswith("last traded price") for r in ccc)
    void = [v for v in result.void if v.startswith("CCC")]
    assert void
    assert result.void_rate == pytest.approx(
        len(result.void) / (len(result.void) + len({(r.day, r.ticker) for r in result.rows}))
    )


@pytest.mark.unit
def test_a_stock_with_no_price_history_is_void_not_dropped() -> None:
    result = run(
        members_as_of=lambda _: ("AAA", "ZZZ"),
        dates=DATES[:1],
        forecasters={"naive": forecast_naive},
    )
    assert any(v.startswith("ZZZ") and "no price history" in v for v in result.void)


@pytest.mark.unit
def test_a_forecaster_that_cannot_run_is_recorded_as_skipped_with_its_reason() -> None:
    early = forecast_dates(START, date(2015, 12, 31))[5:6]  # 105 sessions: naive yes, plain_vol no
    result = run(dates=early, members_as_of=lambda _: ("AAA",))
    assert {r.forecast.forecast_id for r in result.rows} == {"naive"}
    assert any("plain_vol" in s and "500 days" in s for s in result.skipped)


@pytest.mark.unit
def test_a_forecaster_returning_a_reason_string_is_recorded_as_skipped() -> None:
    def unavailable(_: np.ndarray, *, horizon: int, seed: int, paths: int) -> str:
        return "not built"

    result = run(dates=DATES[:1], forecasters={"stub": unavailable})
    assert not result.rows
    assert any("stub" in s and "not built" in s for s in result.skipped)


@pytest.mark.unit
def test_a_forecast_with_the_wrong_identity_is_refused() -> None:
    def mislabelled(r: np.ndarray, *, horizon: int, seed: int, paths: int) -> QuantileForecast:
        return forecast_naive(r, horizon=horizon, seed=seed, paths=paths)

    with pytest.raises(ValueError, match="forecast_id"):
        run(dates=DATES[:1], forecasters={"other": mislabelled})


@pytest.mark.unit
def test_membership_is_asked_only_for_forecast_days() -> None:
    asked: list[date] = []

    def recording(day: date) -> Sequence[str]:
        asked.append(day)
        return ("AAA",)

    run(members_as_of=recording, dates=DATES[:2], forecasters={"naive": forecast_naive})
    assert asked == list(DATES[:2])


@pytest.mark.unit
def test_paired_scores_compare_the_same_stock_and_date_only(result: WalkForwardResult) -> None:
    pairs = paired_scores(result, model_id="plain_vol", baseline_id="naive")
    by_key = {(r.day.isoformat(), r.ticker, r.forecast.forecast_id): r.crps for r in result.rows}
    assert len(pairs) == len({(r.day, r.ticker) for r in result.rows})
    for p in pairs:
        assert p.model_crps == by_key[(p.day, p.ticker, "plain_vol")]
        assert p.baseline_crps == by_key[(p.day, p.ticker, "naive")]


@pytest.mark.unit
def test_paired_scores_reject_identical_model_and_baseline(result: WalkForwardResult) -> None:
    with pytest.raises(ValueError, match="differ"):
        paired_scores(result, model_id="naive", baseline_id="naive")


@pytest.mark.unit
def test_void_rate_is_zero_when_nothing_was_attempted() -> None:
    assert WalkForwardResult(rows=(), void=(), skipped=()).void_rate == 0.0


@pytest.mark.unit
def test_horizon_must_be_positive() -> None:
    with pytest.raises(ValueError, match="horizon"):
        run(horizon=0)
