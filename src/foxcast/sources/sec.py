"""SEC XBRL company-concept provider (brief F1, change AF).

Every value keeps the accession number and filing date of the filing that reported it, so
the value as first reported can be told apart from a later restatement.

As-of rule (no lookahead): only the filing *date* is stored, not the acceptance time, so a
value filed on day D is treated as public from the start of day D + 1. An as-of query for
day D therefore sees filings dated before D only.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Literal

import httpx
import numpy as np
import pandas as pd
import pandera.pandas as pa

from foxcast.sources.throttle import Throttle

SEC_DATA_HOST: Final = "https://data.sec.gov"
SEC_MAX_REQUESTS_PER_SECOND: Final = 10
ACCESSION_PATTERN: Final = r"^\d{10}-\d{2}-\d{6}$"
NAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]+$")
PERIOD_KEY: Final = ["cik", "taxonomy", "tag", "unit", "period_start", "period_end"]
DTYPES: Final = {
    "cik": "int64",
    "taxonomy": "object",
    "tag": "object",
    "unit": "object",
    "period_start": "object",
    "period_end": "object",
    "value": "float64",
    "accession": "object",
    "form": "object",
    "filed": "object",
    "fiscal_year": "Int64",
    "fiscal_period": "object",
    "frame": "object",
}

# One shared limiter for every SEC provider in the process (SEC fair-access policy).
SEC_THROTTLE: Final = Throttle(max_per_second=SEC_MAX_REQUESTS_PER_SECOND)


def _filed_after_period(table: pd.DataFrame) -> pd.Series:
    return table["filed"] >= table["period_end"]


def _start_before_end(table: pd.DataFrame) -> pd.Series:
    starts = table["period_start"]
    return starts.isna() | (starts.fillna(table["period_end"]) <= table["period_end"])


def _unique_rows(table: pd.DataFrame) -> bool:
    return not table.duplicated(subset=[*PERIOD_KEY, "accession"]).any()


CONCEPT_CONTRACT: Final = pa.DataFrameSchema(
    {
        "cik": pa.Column(int, pa.Check.ge(1)),
        "taxonomy": pa.Column(str),
        "tag": pa.Column(str),
        "unit": pa.Column(str),
        "period_start": pa.Column(object, nullable=True),
        "period_end": pa.Column(object, nullable=False),
        "value": pa.Column(float, pa.Check(lambda s: np.isfinite(s), element_wise=False)),
        "accession": pa.Column(str, pa.Check.str_matches(ACCESSION_PATTERN)),
        "form": pa.Column(str),
        "filed": pa.Column(object, nullable=False),
        "fiscal_year": pa.Column("Int64", nullable=True),
        "fiscal_period": pa.Column(str, nullable=True),
        "frame": pa.Column(str, nullable=True),
    },
    checks=[
        pa.Check(_filed_after_period, error="filed before the period it reports ended"),
        pa.Check(_start_before_end, error="period starts after it ends"),
        pa.Check(_unique_rows, error="duplicate rows for the same period and filing"),
    ],
    strict=True,
)


@dataclass(frozen=True)
class ConceptQuery:
    cik: int
    tag: str
    taxonomy: str = "us-gaap"

    def __post_init__(self) -> None:
        if self.cik < 1:
            raise ValueError(f"CIK must be positive, got {self.cik}")
        for label, value in (("tag", self.tag), ("taxonomy", self.taxonomy)):
            if not NAME_PATTERN.fullmatch(value):
                raise ValueError(f"invalid XBRL {label}: {value!r}")


def _to_date(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    units = payload["units"]
    if not isinstance(units, dict):
        raise TypeError("'units' must be an object")
    rows = []
    for unit, items in units.items():
        for item in items:
            rows.append(
                {
                    "cik": int(payload["cik"]),
                    "taxonomy": payload["taxonomy"],
                    "tag": payload["tag"],
                    "unit": unit,
                    "period_start": _to_date(item.get("start")),
                    "period_end": _to_date(item["end"]),
                    "value": float(item["val"]),
                    "accession": item["accn"],
                    "form": item["form"],
                    "filed": _to_date(item["filed"]),
                    "fiscal_year": item.get("fy"),
                    "fiscal_period": item.get("fp"),
                    "frame": item.get("frame"),
                }
            )
    return rows


class SecConceptProvider:
    """One XBRL concept (for example us-gaap Revenues) for one company."""

    name = "sec_companyconcept"
    contract = CONCEPT_CONTRACT
    secret_params: frozenset[str] = frozenset()
    default_throttle: Throttle | None = SEC_THROTTLE

    def __init__(self, contact: str) -> None:
        if not contact.strip():
            raise ValueError("SEC requires a contact name and email in the User-Agent header")
        self._contact = contact

    def build_request(self, query: ConceptQuery) -> httpx.Request:
        url = (
            f"{SEC_DATA_HOST}/api/xbrl/companyconcept/"
            f"CIK{query.cik:010d}/{query.taxonomy}/{query.tag}.json"
        )
        return httpx.Request("GET", url, headers={"User-Agent": self._contact})

    def shape(self, payload: bytes) -> pd.DataFrame:
        table = pd.DataFrame(_rows(json.loads(payload)), columns=list(DTYPES))
        return table.astype(DTYPES)

    def mismatch(self, query: ConceptQuery, table: pd.DataFrame) -> str | None:
        """Why the payload is not about the company and concept asked for, if it is not."""
        if table.empty:
            return None
        if set(table["cik"]) != {query.cik}:
            return f"payload is for CIK {sorted(set(table['cik']))}, asked for {query.cik}"
        if set(table["tag"]) != {query.tag} or set(table["taxonomy"]) != {query.taxonomy}:
            return f"payload is not {query.taxonomy}:{query.tag}"
        return None


def _public_before(table: pd.DataFrame, as_of: date | None) -> pd.DataFrame:
    if as_of is None:
        return table
    return table[table["filed"] < as_of]


def _one_per_period(
    table: pd.DataFrame, as_of: date | None, *, keep: Literal["first", "last"]
) -> pd.DataFrame:
    visible = _public_before(table, as_of).assign(
        _is_amendment=lambda t: t["form"].str.endswith("/A")
    )
    # Same-day ties: an original filing comes before its amendment.
    ordered = visible.sort_values(["filed", "_is_amendment", "accession"], kind="stable")
    deduped = ordered.drop_duplicates(subset=PERIOD_KEY, keep=keep)
    return deduped.drop(columns="_is_amendment").reset_index(drop=True)


def first_reported(table: pd.DataFrame, as_of: date | None = None) -> pd.DataFrame:
    """Each period's value as first reported (the earliest filing), as public before ``as_of``."""
    return _one_per_period(table, as_of, keep="first")


def latest_reported(table: pd.DataFrame, as_of: date | None = None) -> pd.DataFrame:
    """Each period's most recent value (restatements win), as public before ``as_of``."""
    return _one_per_period(table, as_of, keep="last")
