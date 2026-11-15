"""The past-only view every walk-forward input passes through (scorecard row 2d).

A forecast made on day *t* sees only rows dated before *t*. The forecast is frozen during
the session, so the close of *t* itself is not yet known.
"""

from dataclasses import dataclass
from datetime import date

import pandas as pd

DATE_COLUMN = "date"


class LookaheadError(ValueError):
    """An input dated on or after the forecast time reached a forecast."""


@dataclass(frozen=True, eq=False)
class PastOnly:
    """A dated table seen from ``cutoff``: rows dated on or after it do not exist."""

    table: pd.DataFrame
    cutoff: date
    date_column: str = DATE_COLUMN

    def rows(self) -> pd.DataFrame:
        """A copy of the rows dated before the cutoff, oldest first."""
        before = self.table[self.table[self.date_column] < self.cutoff]
        return before.sort_values(self.date_column).reset_index(drop=True)


def latest_date(table: pd.DataFrame, date_column: str = DATE_COLUMN) -> date | None:
    """The newest date in ``table``, or ``None`` when it is empty."""
    if table.empty:
        return None
    latest: date = max(table[date_column])
    return latest


def ensure_before(seen: date | None, cutoff: date, *, what: str) -> None:
    """Refuse an input dated on or after the forecast time."""
    if seen is not None and seen >= cutoff:
        raise LookaheadError(
            f"{what} dated {seen.isoformat()} is not before the forecast time {cutoff.isoformat()}"
        )
