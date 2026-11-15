"""The walk-forward harness (scorecard rows 2b-2f; row 2d's lookahead discipline).

At each forecast date, for each stock that is a member on that date, the ``FORECASTS``
registry (``daily.py``) is run on ``ForecastInputs`` built from a past-only view of
prices, so every forecast refits its own pieces from rows dated before the forecast day.
The outcome is then settled under the resolution rule and scored by CRPS. Nothing is
dropped silently: a void outcome and an unavailable forecast are each recorded with the
reason. As in the registry, ``ValueError`` means invalid input and is never caught.

Prices and membership are passed in. This module fetches nothing. The survivorship-free
S&P 500 panel needed for the row 2b final run is a separate data task, and so is
option-chain history: with no chains the option forecast reports itself unavailable.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd

from foxcast.daily import FORECASTS, ForecastInputs, ForecastSpec, request_id, seed_for
from foxcast.scoring.crps import ScoredForecast, crps_from_quantiles
from foxcast.sim.grid import QUANTILE_LEVELS, QuantileForecast
from foxcast.snapshot import completed_session_returns
from foxcast.walkforward.past_only import PastOnly, ensure_before, latest_date
from foxcast.walkforward.schedule import WINDOW_SESSIONS
from foxcast.walkforward.settle import Outcome, settle


@dataclass(frozen=True)
class BuiltForecasts:
    """The family's results for one stock on one day, and what they were built from."""

    results: Mapping[str, QuantileForecast | str]
    latest_input: date | None  # newest price row in the inputs the forecasts received


@dataclass(frozen=True)
class WalkForwardRow:
    """One forecast, its settled outcome and its score."""

    day: date
    ticker: str
    settled_on: date
    forecast: QuantileForecast
    outcome: float
    resolution: str
    crps: float
    latest_input: date


@dataclass(frozen=True)
class WalkForwardResult:
    rows: tuple[WalkForwardRow, ...]
    settled: int  # (date, stock) pairs with an outcome, whether or not any forecast ran
    void: tuple[str, ...]  # one entry per (date, stock) pair with no price to settle on
    skipped: tuple[str, ...]  # one entry per forecast that was unavailable

    @property
    def void_rate(self) -> float:
        """Void (date, stock) pairs over all attempted pairs, reported beside every score."""
        attempted = self.settled + len(self.void)
        return len(self.void) / attempted if attempted else 0.0


def forecasts_on(
    view: PastOnly, *, ticker: str, specs: Sequence[ForecastSpec], horizon: int
) -> BuiltForecasts:
    """Every registered forecast at ``horizon`` for ``ticker``, from rows before the cutoff."""
    day = view.cutoff
    past = view.rows()
    latest = latest_date(past)
    # Defence in depth: PastOnly already filters, but if that ever regresses the run
    # stops here rather than scoring a forecast that saw its own outcome.
    ensure_before(latest, day, what=f"{ticker} prices")
    inputs = ForecastInputs(
        day=day,
        log_returns=completed_session_returns(past, day),
        chains=None,
        spot=float(past["close"].iloc[-1]) if latest is not None else float("nan"),
        results={},
    )
    for spec in specs:
        if horizon not in spec.horizons:
            continue
        seed = seed_for(request_id(day.isoformat(), ticker, spec.forecast_id, horizon))
        result = spec.build(inputs, horizon=horizon, seed=seed)
        if isinstance(result, QuantileForecast) and result.forecast_id != spec.forecast_id:
            raise ValueError(f"{spec.forecast_id!r} returned forecast_id {result.forecast_id!r}")
        inputs.results[f"{spec.forecast_id}:{horizon}"] = result
    return BuiltForecasts(results=dict(inputs.results), latest_input=latest)


def _stock_rows(
    day: date, ticker: str, outcome: Outcome, built: BuiltForecasts, skipped: list[str]
) -> list[WalkForwardRow]:
    rows: list[WalkForwardRow] = []
    for key, forecast in built.results.items():
        if isinstance(forecast, str) or built.latest_input is None:
            skipped.append(f"{ticker} {key} on {day.isoformat()}: {forecast}")
            continue
        rows.append(
            WalkForwardRow(
                day=day,
                ticker=ticker,
                settled_on=outcome.settled_on,
                forecast=forecast,
                outcome=outcome.value,
                resolution=outcome.rule,
                crps=crps_from_quantiles(forecast.values, QUANTILE_LEVELS, outcome.value),
                latest_input=built.latest_input,
            )
        )
    return rows


def run_walk_forward(
    *,
    prices: Mapping[str, pd.DataFrame],
    members_as_of: Callable[[date], Iterable[str]],
    dates: Sequence[date],
    specs: Sequence[ForecastSpec] = FORECASTS,
    horizon: int = WINDOW_SESSIONS,
) -> WalkForwardResult:
    """Forecast, settle and score every member stock on every date."""
    if horizon < 1:
        raise ValueError(f"horizon must be at least one session, got {horizon}")
    rows: list[WalkForwardRow] = []
    void: list[str] = []
    skipped: list[str] = []
    settled = 0
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
            settled += 1
            built = forecasts_on(PastOnly(table, day), ticker=ticker, specs=specs, horizon=horizon)
            rows += _stock_rows(day, ticker, outcome, built, skipped)
    return WalkForwardResult(tuple(rows), settled, tuple(void), tuple(skipped))


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
