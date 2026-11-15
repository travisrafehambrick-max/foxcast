"""The walk-forward harness (scorecard rows 2b-2f; row 2d's lookahead discipline).

At each forecast date, for each stock that is a member on that date, every forecast is
rebuilt from a past-only view of prices: forecasts refit their own pieces from the
returns they are handed. The outcome is then settled under the resolution rule and scored
by CRPS. Nothing is dropped silently: a void outcome and a forecast that cannot run are
each recorded with the reason.

Prices and membership are passed in. This module fetches nothing; the survivorship-free
S&P 500 panel needed for the row 2b final run is a separate data task.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

import numpy as np
import pandas as pd

from foxcast.daily import completed_session_returns, seed_for
from foxcast.scoring.crps import ScoredForecast, crps_from_quantiles
from foxcast.sim.grid import DEFAULT_PATHS, QUANTILE_LEVELS, QuantileForecast
from foxcast.walkforward.past_only import PastOnly, ensure_before, latest_date
from foxcast.walkforward.schedule import WINDOW_SESSIONS
from foxcast.walkforward.settle import Outcome, settle


class Forecaster(Protocol):
    """The history-forecast interface shared by forecasts 1 and 2."""

    def __call__(
        self, log_returns: np.ndarray, *, horizon: int, seed: int, paths: int
    ) -> QuantileForecast | str: ...


@dataclass(frozen=True)
class WalkForwardRow:
    """One forecast, its settled outcome and its score."""

    day: date
    ticker: str
    horizon_day: date
    forecast: QuantileForecast
    outcome: float
    resolution: str
    crps: float
    latest_input: date  # the newest input date the forecast was built from


@dataclass(frozen=True)
class WalkForwardResult:
    rows: tuple[WalkForwardRow, ...]
    void: tuple[str, ...]  # one entry per (date, stock) with no price to settle on
    skipped: tuple[str, ...]  # one entry per forecast that could not run

    @property
    def void_rate(self) -> float:
        """Void (date, stock) pairs over all attempted pairs, reported beside every score."""
        settled = len({(r.day, r.ticker) for r in self.rows})
        attempted = settled + len(self.void)
        return len(self.void) / attempted if attempted else 0.0


def forecasts_on(
    view: PastOnly,
    *,
    ticker: str,
    forecasters: Mapping[str, Forecaster],
    horizon: int,
    paths: int,
) -> dict[str, QuantileForecast | str]:
    """Every forecast for ``ticker`` from prices dated before ``view.cutoff``."""
    day = view.cutoff
    inputs = view.rows()
    ensure_before(latest_date(inputs), day, what=f"{ticker} prices")
    returns = completed_session_returns(inputs, day)
    results: dict[str, QuantileForecast | str] = {}
    for forecast_id, build in forecasters.items():
        seed = seed_for(day.isoformat(), ticker, forecast_id, horizon)
        try:
            result = build(returns, horizon=horizon, seed=seed, paths=paths)
        except ValueError as exc:
            result = str(exc)
        if isinstance(result, QuantileForecast) and result.forecast_id != forecast_id:
            raise ValueError(
                f"forecaster {forecast_id!r} returned forecast_id {result.forecast_id!r}"
            )
        results[forecast_id] = result
    return results


def _stock_rows(
    prices: pd.DataFrame,
    day: date,
    ticker: str,
    outcome: Outcome,
    built: Mapping[str, QuantileForecast | str],
    skipped: list[str],
) -> list[WalkForwardRow]:
    latest = latest_date(PastOnly(prices, day).rows())
    rows: list[WalkForwardRow] = []
    for forecast_id, forecast in built.items():
        if isinstance(forecast, str) or latest is None:
            skipped.append(f"{ticker} {forecast_id} on {day.isoformat()}: {forecast}")
            continue
        rows.append(
            WalkForwardRow(
                day=day,
                ticker=ticker,
                horizon_day=outcome.horizon_day,
                forecast=forecast,
                outcome=outcome.value,
                resolution=outcome.rule,
                crps=crps_from_quantiles(forecast.values, QUANTILE_LEVELS, outcome.value),
                latest_input=latest,
            )
        )
    return rows


def run_walk_forward(
    *,
    prices: Mapping[str, pd.DataFrame],
    members_as_of: Callable[[date], Iterable[str]],
    dates: Sequence[date],
    forecasters: Mapping[str, Forecaster],
    horizon: int = WINDOW_SESSIONS,
    paths: int = DEFAULT_PATHS,
) -> WalkForwardResult:
    """Forecast, settle and score every member stock on every date."""
    if horizon < 1:
        raise ValueError(f"horizon must be at least one session, got {horizon}")
    rows: list[WalkForwardRow] = []
    void: list[str] = []
    skipped: list[str] = []
    for day in dates:
        for ticker in members_as_of(day):
            table = prices.get(ticker)
            if table is None:
                void.append(f"{ticker} on {day.isoformat()}: void: no price history")
                continue
            outcome = settle(table, day, horizon=horizon)
            if isinstance(outcome, str):
                void.append(f"{ticker} on {day.isoformat()}: {outcome}")
                continue
            built = forecasts_on(
                PastOnly(table, day),
                ticker=ticker,
                forecasters=forecasters,
                horizon=horizon,
                paths=paths,
            )
            rows += _stock_rows(table, day, ticker, outcome, built, skipped)
    return WalkForwardResult(tuple(rows), tuple(void), tuple(skipped))


def paired_scores(
    result: WalkForwardResult, *, model_id: str, baseline_id: str
) -> tuple[ScoredForecast, ...]:
    """Model against baseline on the same (date, stock) pairs only, for ``skill_summary``."""
    if model_id == baseline_id:
        raise ValueError("model and baseline must differ")
    scores = {(r.day, r.ticker, r.forecast.forecast_id): r.crps for r in result.rows}
    return tuple(
        ScoredForecast(
            day=day.isoformat(),
            ticker=ticker,
            model_crps=crps,
            baseline_crps=scores[(day, ticker, baseline_id)],
        )
        for (day, ticker, forecast_id), crps in sorted(scores.items())
        if forecast_id == model_id and (day, ticker, baseline_id) in scores
    )
