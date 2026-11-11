"""The daily numbers-only forecasts (Stage 0.5): forecasts 1 and 2 at 5 and 21 trading
days, and forecast 4 at 21 trading days, each frozen in the ledger with its resolution rule.

Request IDs are ``<day>:<ticker>:<forecast>:<horizon>``, so a rerun on the same day returns
the records already frozen instead of writing duplicates.
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
from foxcast.ledger.chain import Ledger
from foxcast.ledger.resolution import RESOLUTION_RULE
from foxcast.sim.grid import QuantileForecast
from foxcast.sim.naive import forecast_naive
from foxcast.sim.plain_vol import forecast_plain_vol
from foxcast.snapshot import EXCHANGE, snapshot_dir

HORIZONS: Final = (5, 21)
OPTION_HORIZON: Final = 21


@dataclass(frozen=True)
class DailyResult:
    frozen: tuple[str, ...]
    skipped: tuple[str, ...]


def code_version(project: Path) -> str:
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "-C", str(project), "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return f"package {__version__}, git unknown"
    return out.stdout.strip()


def horizon_date(day: date, sessions: int) -> date:
    calendar = xcals.get_calendar(EXCHANGE)
    start = calendar.date_to_session(pd.Timestamp(day), direction="next")
    result: date = calendar.session_offset(start, sessions).date()
    return result


def seed_for(day: str, ticker: str, forecast_id: str, horizon: int) -> int:
    """A recorded seed derived from the request, so every rerun reproduces the same paths."""
    text = f"{day}:{ticker}:{forecast_id}:{horizon}"
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "big")


def _log_returns(prices: pd.DataFrame) -> np.ndarray:
    adjusted = prices.sort_values("date")["adj_close"].to_numpy(dtype=float)
    return np.diff(np.log(adjusted))


def _freeze(ledger: Ledger, day: str, ticker: str, forecast: QuantileForecast, version: str) -> str:
    request_id = f"{day}:{ticker}:{forecast.forecast_id}:{forecast.horizon_days}"
    payload: dict[str, Any] = {
        "ticker": ticker,
        "forecast_day": day,
        "forecast": forecast.to_payload(),
        "resolution_rule": dict(RESOLUTION_RULE),
        "code_version": version,
        "model": "none (numbers only)",
    }
    ledger.append(kind="forecast", request_id=request_id, payload=payload)
    return request_id


def run_daily_forecasts(
    tickers: Sequence[str], data_dir: Path, ledger: Ledger, *, day: str, project: Path
) -> DailyResult:
    folder = snapshot_dir(data_dir, day)
    version = code_version(project)
    frozen: list[str] = []
    skipped: list[str] = []
    for ticker in tickers:
        price_path = folder / f"{ticker}_prices.parquet"
        if not price_path.exists():
            skipped.append(f"{ticker}: no price snapshot for {day}")
            continue
        prices = pd.read_parquet(price_path)
        returns = _log_returns(prices)
        for horizon in HORIZONS:
            for build in (forecast_naive, forecast_plain_vol):
                forecast_id = "naive" if build is forecast_naive else "plain_vol"
                try:
                    fc = build(
                        returns, horizon=horizon, seed=seed_for(day, ticker, forecast_id, horizon)
                    )
                except ValueError as exc:
                    skipped.append(f"{ticker} {forecast_id} {horizon}d: {exc}")
                    continue
                frozen.append(_freeze(ledger, day, ticker, fc, version))
        chain_path = folder / f"{ticker}_chains.parquet"
        if not chain_path.exists():
            skipped.append(f"{ticker}: no option chain snapshot for {day}")
            continue
        spot = float(prices.sort_values("date")["close"].iloc[-1])
        target = horizon_date(date.fromisoformat(day), OPTION_HORIZON)
        option = forecast_option_implied(
            pd.read_parquet(chain_path),
            spot=spot,
            valuation=date.fromisoformat(day),
            horizon_date=target,
            horizon_days=OPTION_HORIZON,
        )
        if isinstance(option, str):
            skipped.append(f"{ticker} option_raw: {option}")
        else:
            frozen.append(_freeze(ledger, day, ticker, option, version))
    return DailyResult(tuple(frozen), tuple(skipped))
