"""The walk-forward harness: refit from the past, forecast, settle, score (rows 2b-2f)."""

import math
from collections.abc import Sequence
from datetime import date
from typing import Final

import pytest

from foxcast.daily import FORECASTS, ForecastInputs, ForecastSpec
from foxcast.sim.grid import QuantileForecast
from foxcast.sim.naive import forecast_naive
from foxcast.walkforward.harness import WalkForwardResult, paired_scores, run_walk_forward
from foxcast.walkforward.schedule import forecast_dates

from .forecast_contract import check_forecast_contract
from .walkforward_data import FAST_SPECS, START, synthetic_prices

HORIZON: Final = 21
PRICES: Final = {
    "AAA": synthetic_prices(sessions=900, seed=1),
    "BBB": synthetic_prices(sessions=900, seed=2, sigma=0.03),
    # Delisted after 700 sessions: a mid-window delisting and void dates after it.
    "CCC": synthetic_prices(sessions=700, seed=3),
}
DATES: Final = forecast_dates(date(2017, 4, 3), date(2018, 7, 31))[::3]
NAIVE_ONLY: Final = FAST_SPECS[:1]


def members(_: date) -> Sequence[str]:
    return ("AAA", "BBB", "CCC")


def run(**overrides: object) -> WalkForwardResult:
    kwargs: dict[str, object] = {
        "prices": PRICES,
        "members_as_of": members,
        "dates": DATES,
        "specs": FAST_SPECS,
        "horizon": HORIZON,
    }
    kwargs.update(overrides)
    return run_walk_forward(**kwargs)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def result() -> WalkForwardResult:
    return run()


@pytest.mark.unit
def test_every_row_is_a_contract_forecast_with_a_finite_score(result: WalkForwardResult) -> None:
    assert result.rows
    assert not result.skipped
    for row in result.rows:
        check_forecast_contract(row.forecast, forecast_id=row.forecast.forecast_id, horizon=21)
        assert math.isfinite(row.crps)
        assert row.crps >= 0.0
        assert row.latest_input < row.day < row.settled_on


@pytest.mark.unit
def test_both_forecasts_run_for_every_settled_stock_and_date(result: WalkForwardResult) -> None:
    keys = {(r.day, r.ticker) for r in result.rows}
    assert len(keys) == result.settled
    for spec in FAST_SPECS:
        ran = {(r.day, r.ticker) for r in result.rows if r.forecast.forecast_id == spec.forecast_id}
        assert ran == keys


@pytest.mark.unit
def test_a_rerun_reproduces_every_forecast_from_its_seed(result: WalkForwardResult) -> None:
    again = run(specs=NAIVE_ONLY)
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
    assert any(v.startswith("CCC") for v in result.void)
    assert result.void_rate == pytest.approx(len(result.void) / (len(result.void) + result.settled))


@pytest.mark.unit
def test_a_stock_with_no_price_history_is_void_not_dropped() -> None:
    result = run(members_as_of=lambda _: ("AAA", "ZZZ"), dates=DATES[:1], specs=NAIVE_ONLY)
    assert any(v.startswith("ZZZ") and "no price history" in v for v in result.void)
    assert result.settled == 1


@pytest.mark.unit
def test_an_unavailable_forecast_is_skipped_with_its_reason_and_the_pair_still_counts() -> None:
    early = forecast_dates(START, date(2015, 12, 31))[5:6]  # 105 sessions: naive yes, GARCH no
    result = run(dates=early, members_as_of=lambda _: ("AAA",))
    assert {r.forecast.forecast_id for r in result.rows} == {"naive"}
    assert any("plain_vol:21" in s and "500 days" in s for s in result.skipped)
    assert result.settled == 1


@pytest.mark.unit
def test_a_pair_whose_forecasts_all_fail_is_still_in_the_void_rate_denominator() -> None:
    first = forecast_dates(START, date(2015, 12, 31))[:1]  # no history before the first day
    result = run(dates=first, members_as_of=lambda _: ("AAA",))
    assert not result.rows
    assert len(result.skipped) == len(FAST_SPECS)
    assert result.settled == 1


@pytest.mark.unit
def test_the_shipped_option_forecast_reports_no_chain_history() -> None:
    option = tuple(s for s in FORECASTS if s.forecast_id == "option_raw")
    result = run(dates=DATES[:1], members_as_of=lambda _: ("AAA",), specs=option)
    assert not result.rows
    assert any("option_raw:21" in s and "no option chain" in s for s in result.skipped)


@pytest.mark.unit
def test_only_specs_registered_at_the_horizon_run() -> None:
    result = run(dates=DATES[:1], members_as_of=lambda _: ("AAA",), horizon=10)
    assert not result.rows
    assert not result.skipped


@pytest.mark.unit
def test_a_forecast_with_the_wrong_identity_is_refused() -> None:
    def mislabelled(inputs: ForecastInputs, *, horizon: int, seed: int) -> QuantileForecast | str:
        return forecast_naive(inputs.log_returns, horizon=horizon, seed=seed, paths=500)

    with pytest.raises(ValueError, match="forecast_id"):
        run(dates=DATES[:1], specs=(ForecastSpec("other", (21,), mislabelled),))


@pytest.mark.unit
def test_invalid_input_errors_propagate_rather_than_becoming_skips() -> None:
    def broken(inputs: ForecastInputs, *, horizon: int, seed: int) -> QuantileForecast | str:
        raise ValueError("bad input")

    with pytest.raises(ValueError, match="bad input"):
        run(dates=DATES[:1], specs=(ForecastSpec("naive", (21,), broken),))


@pytest.mark.unit
def test_membership_is_asked_only_for_forecast_days() -> None:
    asked: list[date] = []

    def recording(day: date) -> Sequence[str]:
        asked.append(day)
        return ("AAA",)

    run(members_as_of=recording, dates=DATES[:2], specs=NAIVE_ONLY)
    assert asked == list(DATES[:2])


@pytest.mark.unit
def test_paired_scores_compare_the_same_stock_and_date_only(result: WalkForwardResult) -> None:
    pairs = paired_scores(result, model_id="plain_vol", baseline_id="naive")
    by_key = {(r.day.isoformat(), r.ticker, r.forecast.forecast_id): r.crps for r in result.rows}
    assert len(pairs) == result.settled
    for p in pairs:
        assert p.model_crps == by_key[(p.day, p.ticker, "plain_vol")]
        assert p.baseline_crps == by_key[(p.day, p.ticker, "naive")]


@pytest.mark.unit
def test_paired_scores_reject_identical_model_and_baseline(result: WalkForwardResult) -> None:
    with pytest.raises(ValueError, match="differ"):
        paired_scores(result, model_id="naive", baseline_id="naive")


@pytest.mark.unit
def test_void_rate_is_zero_when_nothing_was_attempted() -> None:
    assert WalkForwardResult(rows=(), settled=0, void=(), skipped=()).void_rate == 0.0


@pytest.mark.unit
def test_horizon_must_be_positive() -> None:
    with pytest.raises(ValueError, match="horizon"):
        run(horizon=0)
