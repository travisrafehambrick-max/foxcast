"""On-demand numbers-only forecasts (Stage 0.5): the ``FORECASTS`` registry, frozen.

Every forecast in the family consumes the same ``ForecastInputs`` — one ``DaySnapshot``
read per ticker — and returns ``QuantileForecast | str``: a forecast on the shared
101-level grid or the reason it is unavailable. ``run_daily_forecasts`` walks the
registry, so adding forecast 3, 5, 6 or B is one ``ForecastSpec`` entry, not a new
dispatch block. Legitimately-unavailable forecasts return the reason as a string;
``ValueError`` is reserved for invalid caller input.

Request IDs are ``<day>:<ticker>:<forecast>:<horizon>``, written once in ``request_id``
— the ledger key and the seed text come from it — so a rerun on the same day returns
the records already frozen instead of writing duplicates. A rerun whose inputs changed
(a new code version, say) is skipped with a reason, never frozen twice.

The snapshot runs while the market is open, so its last price row is intraday.
``DaySnapshot.log_returns`` uses completed sessions only (rows dated before the forecast
day), so history forecasts' variance matches the horizon they are labelled with; the
option forecast is measured from ``DaySnapshot.spot`` — the intraday close the chain's
quotes are consistent with (``detail`` says so).
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final, Protocol

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from foxcast.benchmark.option_implied import forecast_option_implied
from foxcast.ledger.chain import Ledger, LedgerConflictError
from foxcast.ledger.resolution import RESOLUTION_RULE
from foxcast.provenance import code_version
from foxcast.sim.grid import QuantileForecast
from foxcast.sim.naive import forecast_naive
from foxcast.sim.plain_vol import forecast_plain_vol
from foxcast.snapshot import EXCHANGE, load_day
from foxcast.watchlist import validate_ticker

HORIZONS: Final = (5, 21)
OPTION_HORIZON: Final = 21


@dataclass(frozen=True)
class DailyResult:
    frozen: tuple[str, ...]
    skipped: tuple[str, ...]


def horizon_date(day: date, sessions: int) -> date:
    calendar = xcals.get_calendar(EXCHANGE)
    start = calendar.date_to_session(pd.Timestamp(day), direction="next")
    result: date = calendar.session_offset(start, sessions).date()
    return result


def request_id(day: str, ticker: str, forecast_id: str, horizon: int) -> str:
    """The ledger key for one forecast on one day — the format is written once, here."""
    return f"{day}:{ticker}:{forecast_id}:{horizon}"


def seed_for(request: str) -> int:
    """A recorded seed derived from the request ID, so every rerun reproduces the paths."""
    return int.from_bytes(hashlib.sha256(request.encode("utf-8")).digest()[:4], "big")


@dataclass(frozen=True, eq=False)
class ForecastInputs:
    """Everything a forecast may consume for one ticker on one day.

    ``results`` is filled by the dispatcher as each forecast resolves, keyed
    ``<forecast_id>:<horizon>``, so forecasts that combine other forecasts (the blend)
    read the family's outputs rather than files.
    """

    day: date
    log_returns: np.ndarray
    chains: pd.DataFrame | None
    spot: float
    results: dict[str, QuantileForecast | str]


class ForecastBuild(Protocol):
    """One forecast's adapter: shared inputs in, the family's union out."""

    def __call__(
        self, inputs: ForecastInputs, *, horizon: int, seed: int
    ) -> QuantileForecast | str: ...


@dataclass(frozen=True)
class ForecastSpec:
    """One forecast in the family: its id, its horizons, and how to build it."""

    forecast_id: str
    horizons: tuple[int, ...]
    build: ForecastBuild


def _naive(inputs: ForecastInputs, *, horizon: int, seed: int) -> QuantileForecast | str:
    return forecast_naive(inputs.log_returns, horizon=horizon, seed=seed)


def _plain_vol(inputs: ForecastInputs, *, horizon: int, seed: int) -> QuantileForecast | str:
    return forecast_plain_vol(inputs.log_returns, horizon=horizon, seed=seed)


def _option_raw(inputs: ForecastInputs, *, horizon: int, seed: int) -> QuantileForecast | str:
    if inputs.chains is None:
        return "no option chain snapshot"
    return forecast_option_implied(
        inputs.chains,
        spot=inputs.spot,
        valuation=inputs.day,
        horizon_date=horizon_date(inputs.day, horizon),
        horizon_days=horizon,
    )


FORECASTS: Final[tuple[ForecastSpec, ...]] = (
    ForecastSpec("naive", HORIZONS, _naive),
    ForecastSpec("plain_vol", HORIZONS, _plain_vol),
    ForecastSpec("option_raw", (OPTION_HORIZON,), _option_raw),
)


def _freeze(
    ledger: Ledger,
    day: str,
    ticker: str,
    forecast: QuantileForecast,
    version: str,
    sample: str,
) -> str:
    rid = request_id(day, ticker, forecast.forecast_id, forecast.horizon_days)
    payload: dict[str, Any] = {
        "ticker": ticker,
        "forecast_day": day,
        "forecast": forecast.to_payload(),
        "resolution_rule": dict(RESOLUTION_RULE),
        "code_version": version,
        "model": "none (numbers only)",
        # "followed" tickers were hand-picked and are excluded from every scorecard row.
        "sample": sample,
    }
    ledger.append(kind="forecast", request_id=rid, payload=payload)
    return rid


def _try_freeze(
    ledger: Ledger,
    day: str,
    ticker: str,
    forecast: QuantileForecast,
    version: str,
    skipped: list[str],
    sample: str,
) -> str | None:
    try:
        return _freeze(ledger, day, ticker, forecast, version, sample)
    except LedgerConflictError as exc:
        skipped.append(f"{ticker} {forecast.forecast_id}: {exc}; kept the first record")
        return None


def run_daily_forecasts(
    tickers: Sequence[str],
    data_dir: Path,
    ledger: Ledger,
    *,
    day: str,
    project: Path,
    followed: Sequence[str] = (),
) -> DailyResult:
    date.fromisoformat(day)  # reject anything that is not a plain date
    version = code_version(project)
    frozen: list[str] = []
    skipped: list[str] = []
    for ticker in tickers:
        validate_ticker(ticker)
        snap = load_day(data_dir, day, ticker)
        if isinstance(snap, str):
            skipped.append(f"{ticker}: {snap}")
            continue
        sample = "followed" if ticker in followed else "random"
        inputs = ForecastInputs(
            day=snap.day,
            log_returns=snap.log_returns,
            chains=snap.chains,
            spot=snap.spot,
            results={},
        )
        for spec in FORECASTS:
            for horizon in spec.horizons:
                result = spec.build(
                    inputs,
                    horizon=horizon,
                    seed=seed_for(request_id(day, ticker, spec.forecast_id, horizon)),
                )
                inputs.results[f"{spec.forecast_id}:{horizon}"] = result
                if isinstance(result, str):
                    skipped.append(f"{ticker} {spec.forecast_id} {horizon}d: {result}")
                    continue
                rid = _try_freeze(ledger, day, ticker, result, version, skipped, sample)
                if rid:
                    frozen.append(rid)
    return DailyResult(tuple(frozen), tuple(skipped))
