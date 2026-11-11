"""The universe the scored watchlist is drawn from (brief change BG).

The universe is declared before the draw, by GICS sector and sub-industry, from a list of
S&P 500 constituents. Declaring it first keeps the draw honest: the pool is fixed before
anyone sees which tickers come out. Results from the scored watchlist then describe only
these sectors, which the watchlist record states.
"""

import csv
import io
from collections.abc import Iterable
from dataclasses import dataclass

from foxcast.watchlist import validate_ticker

SECTOR_COLUMN = "GICS Sector"
SUB_INDUSTRY_COLUMN = "GICS Sub-Industry"
SYMBOL_COLUMN = "Symbol"


@dataclass(frozen=True)
class UniverseSpec:
    sectors: tuple[str, ...]
    sub_industries: tuple[str, ...]

    def describe(self) -> str:
        sectors = ", ".join(sorted(self.sectors)) or "none"
        subs = ", ".join(sorted(self.sub_industries)) or "none"
        return f"S&P 500 members in GICS sectors [{sectors}] or sub-industries [{subs}]"


def to_yahoo_symbol(symbol: str) -> str:
    """S&P lists write share classes with a dot (BRK.B); Yahoo uses a dash (BRK-B)."""
    return symbol.strip().upper().replace(".", "-")


def _unknown(wanted: Iterable[str], available: set[str]) -> list[str]:
    return sorted(set(wanted) - available)


def select_universe(constituents_csv: str, spec: UniverseSpec) -> list[str]:
    rows = list(csv.DictReader(io.StringIO(constituents_csv)))
    sectors = {row[SECTOR_COLUMN] for row in rows}
    subs = {row[SUB_INDUSTRY_COLUMN] for row in rows}
    missing = _unknown(spec.sectors, sectors) + _unknown(spec.sub_industries, subs)
    if missing:
        raise ValueError(f"not in the source list (check spelling): {', '.join(missing)}")
    chosen = {
        validate_ticker(to_yahoo_symbol(row[SYMBOL_COLUMN]))
        for row in rows
        if row[SECTOR_COLUMN] in spec.sectors or row[SUB_INDUSTRY_COLUMN] in spec.sub_industries
    }
    return sorted(chosen)
