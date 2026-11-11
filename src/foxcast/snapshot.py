"""The snapshot, taken on demand during market hours (brief F3, Stage 0.5, change BD).

Captures what has no free history (option chains, analyst views) plus prices, for every
ticker on the watchlist, during market hours only. Each day's tables are written as
Parquet under ``<data_dir>/snapshots/<date>/``, and one health line per run is appended to
``health.jsonl``. More than 1 failure in 10 raises the alarm (the change-course trigger).
"""

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

import exchange_calendars as xcals
import pandas as pd

from foxcast.sources.base import Unavailable
from foxcast.sources.yahoo import (
    YahooResult,
    YahooTable,
    fetch_analyst_views,
    fetch_option_chains,
    fetch_prices,
)
from foxcast.watchlist import validate_ticker

ALARM_FAILURE_RATE: Final = 0.10
EXCHANGE: Final = "XNYS"


class Fetcher(Protocol):
    def __call__(self, symbol: str, *, now: Callable[[], datetime]) -> YahooResult: ...


DEFAULT_FETCHERS: Final[Mapping[str, Fetcher]] = {
    "prices": fetch_prices,
    "chains": fetch_option_chains,
    "analysts": fetch_analyst_views,
}


class MarketClosedError(RuntimeError):
    """The snapshot only runs while the exchange is open (change BD)."""


@dataclass(frozen=True)
class SnapshotReport:
    day: str
    started_at: str
    written: tuple[str, ...]
    kept: tuple[str, ...]
    failures: tuple[str, ...]
    failure_rate: float
    alarm: bool


def market_is_open(moment: datetime) -> bool:
    calendar = xcals.get_calendar(EXCHANGE)
    minute = pd.Timestamp(moment).tz_convert("UTC").floor("min")
    return bool(calendar.is_open_on_minute(minute))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def snapshot_dir(data_dir: Path, day: str) -> Path:
    return data_dir / "snapshots" / day


def run_snapshot(
    symbols: Sequence[str],
    data_dir: Path,
    *,
    now: Callable[[], datetime] = _utc_now,
    fetchers: Mapping[str, Fetcher] = DEFAULT_FETCHERS,
    require_market_open: bool = True,
) -> SnapshotReport:
    started = now()
    if require_market_open and not market_is_open(started):
        raise MarketClosedError(f"{EXCHANGE} is closed at {started.isoformat()}")
    day = pd.Timestamp(started).tz_convert("America/New_York").date().isoformat()
    folder = snapshot_dir(data_dir, day)
    folder.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    failures: list[str] = []
    kept: list[str] = []
    for symbol in symbols:
        validate_ticker(symbol)
        for kind, fetch in fetchers.items():
            result = fetch(symbol, now=now)
            if isinstance(result, YahooTable):
                path = folder / f"{symbol}_{kind}.parquet"
                if path.exists():
                    kept.append(path.name)  # the day's first capture is never replaced
                    continue
                temporary = path.with_name(path.name + ".tmp")
                result.table.to_parquet(temporary, index=False)
                os.replace(temporary, path)
                written.append(path.name)
            elif isinstance(result, Unavailable):
                failures.append(result.reason)
    attempts = max(len(symbols) * len(fetchers), 1)
    rate = len(failures) / attempts
    report = SnapshotReport(
        day=day,
        started_at=started.isoformat(),
        written=tuple(written),
        kept=tuple(kept),
        failures=tuple(failures),
        failure_rate=rate,
        alarm=rate > ALARM_FAILURE_RATE,
    )
    health = data_dir / "snapshots" / "health.jsonl"
    with health.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(asdict(report)) + "\n")
    return report
