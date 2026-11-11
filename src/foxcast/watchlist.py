"""The watchlist: a seeded random draw, so it cannot be picked by hand (brief change BG)."""

import json
import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class Watchlist:
    tickers: tuple[str, ...]
    seed: int
    size: int
    drawn_on: str
    universe_source: str
    universe_size: int


def draw_watchlist(
    universe: Sequence[str], *, size: int, seed: int, drawn_on: date, universe_source: str
) -> Watchlist:
    members = sorted({ticker.strip().upper() for ticker in universe if ticker.strip()})
    if size > len(members):
        raise ValueError(f"cannot draw {size} from a universe of {len(members)}")
    picked = sorted(random.Random(seed).sample(members, size))  # noqa: S311 (not for security)
    return Watchlist(tuple(picked), seed, size, drawn_on.isoformat(), universe_source, len(members))


def save_watchlist(watchlist: Watchlist, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(watchlist), indent=2) + "\n", encoding="utf-8")


def load_watchlist(path: Path) -> Watchlist:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Watchlist(**{**data, "tickers": tuple(data["tickers"])})
