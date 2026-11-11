"""SEC XBRL company-concept provider (brief F1, change AF).

Every value keeps the accession number and filing date of the filing that reported it, so
the value as first reported can be told apart from a later restatement. As-of queries use
the filing date: a value counts as public from the day it was filed.
"""

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Literal

import httpx
import numpy as np
import pandas as pd
import pandera.pandas as pa

SEC_DATA_HOST: Final = "https://data.sec.gov"
SEC_MAX_REQUESTS_PER_SECOND: Final = 10
ACCESSION_PATTERN: Final = r"^\d{10}-\d{2}-\d{6}$"
PERIOD_KEY: Final = ["cik", "taxonomy", "tag", "unit", "period_start", "period_end"]

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
    strict=True,
)


@dataclass(frozen=True)
class ConceptQuery:
    cik: int
    tag: str
    taxonomy: str = "us-gaap"


def _to_date(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for unit, items in payload["units"].items():
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
        table = pd.DataFrame(_rows(json.loads(payload)), columns=list(CONCEPT_CONTRACT.columns))
        return table.astype({"fiscal_year": "Int64", "value": float, "cik": int})


def _public_by(table: pd.DataFrame, as_of: date | None) -> pd.DataFrame:
    if as_of is None:
        return table
    return table[table["filed"] <= as_of]


def _one_per_period(
    table: pd.DataFrame, as_of: date | None, *, keep: Literal["first", "last"]
) -> pd.DataFrame:
    visible = _public_by(table, as_of).sort_values(["filed", "accession"], kind="stable")
    deduped = visible.drop_duplicates(subset=PERIOD_KEY, keep=keep)
    return deduped.reset_index(drop=True)


def first_reported(table: pd.DataFrame, as_of: date | None = None) -> pd.DataFrame:
    """Each period's value as first reported (the earliest filing), as known on ``as_of``."""
    return _one_per_period(table, as_of, keep="first")


def latest_reported(table: pd.DataFrame, as_of: date | None = None) -> pd.DataFrame:
    """Each period's most recent value (restatements win), as known on ``as_of``."""
    return _one_per_period(table, as_of, keep="last")
