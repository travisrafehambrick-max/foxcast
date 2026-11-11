"""The watchlist (brief change BG).

Two kinds of ticker, kept apart:

- **random**: the scored sample, a seeded random draw from a universe declared before the
  draw, so nobody can pick stocks that happen to look good;
- **followed**: tickers a person chose to watch. They are snapshotted and forecast the
  same way, but every ledger record is tagged ``"followed"`` and the scorecard excludes
  them.
"""

import hashlib
import json
import random
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path
from typing import Final

TICKER_PATTERN: Final = re.compile(r"^\^?[A-Z0-9][A-Z0-9.=-]{0,14}$")


def validate_ticker(ticker: str) -> str:
    """Tickers end up in file names, so only plain symbol characters are allowed."""
    if not TICKER_PATTERN.fullmatch(ticker):
        raise ValueError(f"not a valid ticker symbol: {ticker!r}")
    return ticker


@dataclass(frozen=True)
class Watchlist:
    tickers: tuple[str, ...]
    seed: int
    size: int
    drawn_on: str
    universe_source: str
    universe_size: int
    universe_definition: str = ""
    universe_sha256: str = ""
    followed: tuple[str, ...] = ()

    @property
    def all_tickers(self) -> tuple[str, ...]:
        return (*self.tickers, *self.followed)


def draw_watchlist(
    universe: Sequence[str],
    *,
    size: int,
    seed: int,
    drawn_on: date,
    universe_source: str,
    universe_definition: str = "",
) -> Watchlist:
    members = sorted(
        {validate_ticker(ticker.strip().upper()) for ticker in universe if ticker.strip()}
    )
    if size > len(members):
        raise ValueError(f"cannot draw {size} from a universe of {len(members)}")
    picked = sorted(random.Random(seed).sample(members, size))  # noqa: S311 (not for security)
    return Watchlist(
        tickers=tuple(picked),
        seed=seed,
        size=size,
        drawn_on=drawn_on.isoformat(),
        universe_source=universe_source,
        universe_size=len(members),
        universe_definition=universe_definition,
        universe_sha256=hashlib.sha256("\n".join(members).encode("utf-8")).hexdigest(),
    )


def follow(watchlist: Watchlist, ticker: str) -> Watchlist:
    symbol = validate_ticker(ticker.strip().upper())
    if symbol in watchlist.tickers:
        raise ValueError(f"{symbol} is already on the random watchlist; it is scored there")
    if symbol in watchlist.followed:
        return watchlist
    return replace(watchlist, followed=(*watchlist.followed, symbol))


def unfollow(watchlist: Watchlist, ticker: str) -> Watchlist:
    symbol = ticker.strip().upper()
    return replace(watchlist, followed=tuple(t for t in watchlist.followed if t != symbol))


def save_watchlist(watchlist: Watchlist, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(watchlist), indent=2) + "\n", encoding="utf-8")


def load_watchlist(path: Path) -> Watchlist:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tickers"] = tuple(data["tickers"])
    data["followed"] = tuple(data.get("followed", ()))
    return Watchlist(**data)
