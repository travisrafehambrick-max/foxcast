"""Fiscal-period labels for XBRL facts (carry-forward from the 1A.5 review).

A company's own "fiscal Q1 2026" can be October to December 2025. The checker needs the
label to judge such words, and the label cannot be read off a row directly: SEC's ``fy``
and ``fp`` describe the *filing* a row came from, so a FY2025 10-K tags its FY2024
comparative ``fy=2025, fp=FY`` as well.

A period is therefore labelled only where a filing reports it as its own current period:

- the filing is a periodic report (10-K or 10-Q, or an amendment of one);
- the period is the latest one the filing reports for the concept;
- the filing came within :data:`CURRENT_PERIOD_WINDOW` of the period's end;
- the period's length fits the label (a quarter, the half-year to date in a Q2 report,
  or a year), and the fiscal year is within a year of the period's end.

Every row for a labelled period, from any filing, then carries the label: the label
belongs to the period, not to the value. Filings that disagree leave the period
unlabelled. A missing label withholds fiscal wording; it never guesses.
"""

from collections import defaultdict
from collections.abc import Hashable, Iterable, Mapping
from datetime import date, timedelta
from typing import Any, Final

# Lengths in days, allowing for 52/53-week fiscal years.
QUARTER_DAYS: Final = (80, 100)
HALF_DAYS: Final = (170, 190)
YEAR_DAYS: Final = (355, 375)
# Large filers report within 40 (10-Q) and 60 (10-K) days, others within 45 and 90, plus
# the 5- and 15-day extensions. A comparative is at least a year old when it is repeated.
CURRENT_PERIOD_WINDOW: Final = timedelta(days=120)
# Transition reports (10-KT, 10-QT) cover odd-length periods and are left unlabelled.
PERIODIC_FORMS: Final = frozenset({"10-K", "10-Q"})
QUARTERS: Final = frozenset({"Q1", "Q2", "Q3"})
MAX_YEAR_DRIFT: Final = 1

Period = tuple[date | None, date]
FiscalLabel = tuple[int, str]


def within(days: int | None, bounds: tuple[int, int]) -> bool:
    return days is not None and bounds[0] <= days <= bounds[1]


def _label_for(fp: str, days: int, *, at_year_end: bool) -> str | None:
    """The label a period of ``days`` gets in a report tagged ``fp``.

    In an annual report a quarter is Q4, and a half H2, only if it ends on the fiscal
    year end that the same report gives for the concept; otherwise it may be an earlier
    quarter that happens to be the latest one reported.
    """
    if within(days, QUARTER_DAYS):
        if fp in QUARTERS:
            return fp
        return "Q4" if fp == "FY" and at_year_end else None
    if within(days, HALF_DAYS):
        if fp == "Q2":
            return "H1"
        return "H2" if fp == "FY" and at_year_end else None
    if within(days, YEAR_DAYS):
        return "FY" if fp == "FY" else None
    return None


def _fiscal_year(value: Any) -> int | None:
    """An integer year, or None for pandas' missing values, floats and booleans."""
    if isinstance(value, bool) or not hasattr(value, "__index__"):
        return None
    return int(value)


Row = Mapping[Hashable, Any]  # a record from ``DataFrame.to_dict("records")``


def _candidate(row: Row, latest_end: date, year_ends: set[date]) -> FiscalLabel | None:
    start, end, filed = row["period_start"], row["period_end"], row["filed"]
    form = str(row["form"]).removesuffix("/A")
    fy, fp = _fiscal_year(row["fiscal_year"]), row["fiscal_period"]
    if form not in PERIODIC_FORMS or not isinstance(start, date) or end != latest_end:
        return None
    if not timedelta(0) <= filed - end <= CURRENT_PERIOD_WINDOW:
        return None
    if fy is None or not isinstance(fp, str) or abs(fy - end.year) > MAX_YEAR_DRIFT:
        return None
    label = _label_for(fp, (end - start).days, at_year_end=end in year_ends)
    return None if label is None else (fy, label)


def fiscal_labels(rows: Iterable[Row]) -> dict[Period, FiscalLabel]:
    """The fiscal label of each period some filing reports as its own current period.

    ``rows`` are a company-concept table's records (see :mod:`foxcast.sources.sec`).
    """
    rows = list(rows)
    latest: dict[str, date] = {}
    year_ends: defaultdict[str, set[date]] = defaultdict(set)
    for row in rows:
        accession, start, end = str(row["accession"]), row["period_start"], row["period_end"]
        latest[accession] = max(latest.get(accession, end), end)
        if isinstance(start, date) and within((end - start).days, YEAR_DAYS):
            year_ends[accession].add(end)
    found: defaultdict[Period, set[FiscalLabel]] = defaultdict(set)
    for row in rows:
        accession = str(row["accession"])
        label = _candidate(row, latest[accession], year_ends[accession])
        if label is not None:
            found[(row["period_start"], row["period_end"])].add(label)
    return {period: labels.pop() for period, labels in found.items() if len(labels) == 1}
