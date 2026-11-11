"""On-demand numbers-only forecasts (Stage 0.5): forecasts 1 and 2 at 5 and 21 trading
days, and forecast 4 at 21 trading days, each frozen in the ledger with its resolution rule.

Request IDs are ``<day>:<ticker>:<forecast>:<horizon>``, so a rerun on the same day returns
the records already frozen instead of writing duplicates. A rerun whose inputs changed (a
new code version, say) is skipped with a reason, never frozen twice.

The snapshot runs while the market is open, so its last price row is intraday. Forecasts
1 and 2 use completed sessions only (rows dated before the forecast day), so their
variance matches the horizon they are labelled with. The return is measured from the
first close after the freeze (the resolution rule). Forecast 4 is measured from the spot
price at the snapshot, which the chain's quotes are consistent with; ``detail`` says so.
"""

import hashlib
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from foxcast import __version__
from foxcast.benchmark.option_implied import forecast_option_implied
from foxcast.ledger.chain import Ledger, LedgerConflictError
from foxcast.ledger.resolution import RESOLUTION_RULE
from foxcast.sim.grid import QuantileForecast
from foxcast.sim.naive import forecast_naive
from foxcast.sim.plain_vol import forecast_plain_vol
from foxcast.snapshot import EXCHANGE, snapshot_dir
from foxcast.watchlist import validate_ticker

HORIZONS: Final = (5, 21)
OPTION_HORIZON: Final = 21
GIT_TIMEOUT_SECONDS: Final = 10


@dataclass(frozen=True)
class DailyResult:
    frozen: tuple[str, ...]
    skipped: tuple[str, ...]


def _git(project: Path, *args: str) -> str:
    out = subprocess.run(  # noqa: S603
        ["git", "-C", str(project), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    return out.stdout.strip()


def code_version(project: Path) -> str:
    """The git commit, marked "+dirty" when the working tree has uncommitted changes."""
    try:
        commit = _git(project, "rev-parse", "HEAD")
        dirty = bool(_git(project, "status", "--porcelain", "--untracked-files=no"))
    except (OSError, subprocess.SubprocessError):
        return f"package {__version__}, git unknown"
    return f"{commit}+dirty" if dirty else commit


def horizon_date(day: date, sessions: int) -> date:
    calendar = xcals.get_calendar(EXCHANGE)
    start = calendar.date_to_session(pd.Timestamp(day), direction="next")
    result: date = calendar.session_offset(start, sessions).date()
    return result


def seed_for(day: str, ticker: str, forecast_id: str, horizon: int) -> int:
    """A recorded seed derived from the request, so every rerun reproduces the same paths."""
    text = f"{day}:{ticker}:{forecast_id}:{horizon}"
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "big")


def completed_session_returns(prices: pd.DataFrame, day: date) -> np.ndarray:
    """Daily log returns from adjusted closes of sessions before ``day`` (no intraday row)."""
    ordered = prices.sort_values("date")
    completed = ordered[ordered["date"] < day]
    return np.diff(np.log(completed["adj_close"].to_numpy(dtype=float)))


def _freeze(
    ledger: Ledger,
    day: str,
    ticker: str,
    forecast: QuantileForecast,
    version: str,
    sample: str,
) -> str:
    request_id = f"{day}:{ticker}:{forecast.forecast_id}:{forecast.horizon_days}"
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
    ledger.append(kind="forecast", request_id=request_id, payload=payload)
    return request_id


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


def _history_forecasts(
    ledger: Ledger,
    prices: pd.DataFrame,
    day: str,
    ticker: str,
    version: str,
    skipped: list[str],
    sample: str,
) -> list[str]:
    frozen: list[str] = []
    returns = completed_session_returns(prices, date.fromisoformat(day))
    for horizon in HORIZONS:
        for forecast_id, build in (("naive", forecast_naive), ("plain_vol", forecast_plain_vol)):
            try:
                fc = build(
                    returns, horizon=horizon, seed=seed_for(day, ticker, forecast_id, horizon)
                )
            except ValueError as exc:
                skipped.append(f"{ticker} {forecast_id} {horizon}d: {exc}")
                continue
            request_id = _try_freeze(ledger, day, ticker, fc, version, skipped, sample)
            if request_id:
                frozen.append(request_id)
    return frozen


def _option_forecast(
    ledger: Ledger,
    prices: pd.DataFrame,
    chain_path: Path,
    day: str,
    ticker: str,
    version: str,
    skipped: list[str],
    sample: str,
) -> list[str]:
    if not chain_path.exists():
        skipped.append(f"{ticker}: no option chain snapshot for {day}")
        return []
    spot = float(prices.sort_values("date")["close"].iloc[-1])
    forecast_day = date.fromisoformat(day)
    option = forecast_option_implied(
        pd.read_parquet(chain_path),
        spot=spot,
        valuation=forecast_day,
        horizon_date=horizon_date(forecast_day, OPTION_HORIZON),
        horizon_days=OPTION_HORIZON,
    )
    if isinstance(option, str):
        skipped.append(f"{ticker} option_raw: {option}")
        return []
    request_id = _try_freeze(ledger, day, ticker, option, version, skipped, sample)
    return [request_id] if request_id else []


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
    folder = snapshot_dir(data_dir, day)
    version = code_version(project)
    frozen: list[str] = []
    skipped: list[str] = []
    for ticker in tickers:
        validate_ticker(ticker)
        price_path = folder / f"{ticker}_prices.parquet"
        if not price_path.exists():
            skipped.append(f"{ticker}: no price snapshot for {day}")
            continue
        prices = pd.read_parquet(price_path)
        sample = "followed" if ticker in followed else "random"
        frozen += _history_forecasts(ledger, prices, day, ticker, version, skipped, sample)
        chain_path = folder / f"{ticker}_chains.parquet"
        frozen += _option_forecast(
            ledger, prices, chain_path, day, ticker, version, skipped, sample
        )
    return DailyResult(tuple(frozen), tuple(skipped))
