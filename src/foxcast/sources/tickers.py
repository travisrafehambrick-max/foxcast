"""The dated ticker table (brief change AF).

Companies are keyed by SEC CIK plus a share-class label. Tickers are dated attributes: a
ticker can be reused by a different company later, and a company can change its ticker.
The table is built forward from daily snapshots of the SEC ticker file, so it only knows
history from its first snapshot on.

The share-class label is the ticker under which the class was first seen. When a company
drops exactly one ticker and gains exactly one in the same snapshot, that is treated as a
rename and the label carries over.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from typing import Final


@dataclass(frozen=True)
class SecurityKey:
    cik: int
    share_class: str


@dataclass(frozen=True)
class TickerSpan:
    ticker: str
    key: SecurityKey
    valid_from: date
    valid_to: date | None = None  # exclusive; None while still current

    def covers(self, on: date) -> bool:
        return self.valid_from <= on and (self.valid_to is None or on < self.valid_to)


# A snapshot that drops more than this share of a table this large is refused: it is far
# more likely a truncated or broken SEC file than a real wave of delistings.
SHRINK_GUARD_MIN_OPEN: Final = 20
MAX_SHRINK_FRACTION: Final = 0.2


def parse_sec_ticker_file(payload: bytes) -> dict[str, int]:
    """SEC company_tickers.json to {ticker: cik}. A ticker listed for two companies is an error."""
    mapping: dict[str, int] = {}
    for entry in json.loads(payload).values():
        ticker, cik = entry["ticker"].upper(), int(entry["cik_str"])
        if mapping.get(ticker, cik) != cik:
            raise ValueError(f"ticker {ticker} is listed for CIKs {mapping[ticker]} and {cik}")
        mapping[ticker] = cik
    return mapping


@dataclass(frozen=True)
class TickerTable:
    spans: tuple[TickerSpan, ...] = ()
    last_observed: date | None = None

    def lookup(self, ticker: str, on: date) -> SecurityKey | None:
        for span in self.spans:
            if span.ticker == ticker.upper() and span.covers(on):
                return span.key
        return None

    def observe(
        self,
        snapshot: Mapping[str, int],
        *,
        as_of: date,
        allow_large_shrink: bool = False,
    ) -> "TickerTable":
        """Return a new table updated with one snapshot of {ticker: cik}."""
        if self.last_observed is not None and as_of <= self.last_observed:
            raise ValueError(f"observations must be after {self.last_observed}, got {as_of}")
        current = {ticker.upper(): cik for ticker, cik in snapshot.items()}
        self._guard_against_shrink(current, allow_large_shrink)

        kept: list[TickerSpan] = []
        closed: list[TickerSpan] = []
        for span in self.spans:
            still_listed = current.get(span.ticker) == span.key.cik
            if span.valid_to is None and not still_listed:
                closed.append(replace(span, valid_to=as_of))
            else:
                kept.append(span)

        open_tickers = {s.ticker for s in kept if s.valid_to is None}
        new_tickers = {t: cik for t, cik in current.items() if t not in open_tickers}
        history = (*kept, *closed)
        opened = [
            TickerSpan(
                ticker,
                SecurityKey(cik, self._share_class(ticker, cik, history, closed, new_tickers)),
                as_of,
            )
            for ticker, cik in sorted(new_tickers.items())
        ]
        return TickerTable(spans=(*history, *opened), last_observed=as_of)

    def _guard_against_shrink(self, current: Mapping[str, int], allowed: bool) -> None:
        open_count = sum(1 for span in self.spans if span.valid_to is None)
        if allowed or open_count < SHRINK_GUARD_MIN_OPEN:
            return
        if len(current) < open_count * (1 - MAX_SHRINK_FRACTION):
            raise ValueError(
                f"snapshot has {len(current)} tickers against {open_count} open; refusing a "
                "drop this large (pass allow_large_shrink=True if it is real)"
            )

    @staticmethod
    def _share_class(
        ticker: str,
        cik: int,
        history: tuple[TickerSpan, ...],
        closed: list[TickerSpan],
        new_tickers: Mapping[str, int],
    ) -> str:
        # The same company returning under the same ticker keeps its old share class.
        returning = [s for s in history if s.ticker == ticker and s.key.cik == cik]
        if returning:
            return max(returning, key=lambda s: s.valid_from).key.share_class
        # One ticker dropped and one gained by the same company at once: a rename.
        dropped = [span for span in closed if span.key.cik == cik]
        gained = [t for t, c in new_tickers.items() if c == cik]
        if len(dropped) == 1 and len(gained) == 1:
            return dropped[0].key.share_class
        return ticker
