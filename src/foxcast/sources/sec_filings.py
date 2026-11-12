"""SEC filing index and 8-K items, from the submissions API (Stage 1A.3; brief F1).

``data.sec.gov/submissions/CIK##########.json`` lists a company's recent filings (up to
1,000, newest first) with their accession numbers, forms, dates, primary documents and,
for 8-Ks, the item codes. Each 8-K item becomes a text fact, so a claim such as "the
company reported results (item 2.02)" cites a fact, not the model's reading.

As-of rule: the same as :mod:`foxcast.sources.sec`. A filing dated D is public from the
start of D + 1, even when it was accepted earlier on day D.

Only the ``recent`` block is read. Older filings sit in extra files that this provider
does not fetch yet; when SEC lists any, every row says ``index_complete = False`` so a
dossier can report the older filings as unavailable rather than missing.
"""

import json
import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

import httpx
import pandas as pd
import pandera.pandas as pa

from foxcast.facts.record import SEC_PUBLIC_LAG, TextFact, text_fact
from foxcast.sources.base import DEFAULT_MAX_BYTES, Fetched
from foxcast.sources.sec import ACCESSION_PATTERN, SEC_DATA_HOST, SEC_THROTTLE
from foxcast.sources.throttle import Throttle

EIGHT_K_FORMS: Final = frozenset({"8-K", "8-K/A"})
EIGHT_K_ITEM_FIELD: Final = "sec:8-K:item"
ITEM_PATTERN: Final = re.compile(r"^[0-9]{1,2}\.[0-9]{2}$")
COLUMNS: Final = (
    "cik",
    "accession",
    "form",
    "filed",
    "public_on",
    "accepted",
    "report_date",
    "primary_document",
    "items",
    "index_complete",
)
SOURCE_KEYS: Final = {
    "accession": "accessionNumber",
    "form": "form",
    "filed": "filingDate",
    "accepted": "acceptanceDateTime",
    "report_date": "reportDate",
    "primary_document": "primaryDocument",
    "items": "items",
}


def _items_valid(series: pd.Series) -> pd.Series:
    return series.map(lambda items: all(ITEM_PATTERN.fullmatch(i) for i in items))


def _public_after_filing(table: pd.DataFrame) -> pd.Series:
    return table["public_on"] == table["filed"].map(lambda d: d + SEC_PUBLIC_LAG)


SUBMISSIONS_CONTRACT: Final = pa.DataFrameSchema(
    {
        "cik": pa.Column(int, pa.Check.ge(1)),
        "accession": pa.Column(str, pa.Check.str_matches(ACCESSION_PATTERN), unique=True),
        "form": pa.Column(str, pa.Check.str_length(min_value=1)),
        "filed": pa.Column(object, nullable=False),
        "public_on": pa.Column(object, nullable=False),
        "accepted": pa.Column(str),
        "report_date": pa.Column(object, nullable=True),
        "primary_document": pa.Column(str),
        "items": pa.Column(object, pa.Check(_items_valid, error="malformed 8-K item code")),
        "index_complete": pa.Column(bool),
    },
    checks=[pa.Check(_public_after_filing, error="public_on must be the day after filing")],
    strict=True,
)


@dataclass(frozen=True)
class SubmissionsQuery:
    cik: int

    def __post_init__(self) -> None:
        if self.cik < 1:
            raise ValueError(f"CIK must be positive, got {self.cik}")


def _to_date(text: str) -> date | None:
    return date.fromisoformat(text) if text else None


def _split_items(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.split(",") if part.strip())


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    recent = payload["filings"]["recent"]
    columns = {ours: recent[theirs] for ours, theirs in SOURCE_KEYS.items()}
    lengths = {len(values) for values in columns.values()}
    if len(lengths) != 1:
        raise ValueError(f"filing columns have different lengths: {sorted(lengths)}")
    cik = int(payload["cik"])
    complete = not payload["filings"].get("files")
    rows = []
    for i in range(lengths.pop()):
        filed = date.fromisoformat(columns["filed"][i])
        rows.append(
            {
                "cik": cik,
                "accession": columns["accession"][i],
                "form": columns["form"][i],
                "filed": filed,
                "public_on": filed + SEC_PUBLIC_LAG,
                "accepted": columns["accepted"][i],
                "report_date": _to_date(columns["report_date"][i]),
                "primary_document": columns["primary_document"][i],
                "items": _split_items(columns["items"][i]),
                "index_complete": complete,
            }
        )
    return rows


class SecSubmissionsProvider:
    """A company's recent filings, from SEC's submissions API."""

    name = "sec_submissions"
    contract = SUBMISSIONS_CONTRACT
    secret_params: frozenset[str] = frozenset()
    default_throttle: Throttle | None = SEC_THROTTLE
    allowed_content_types: frozenset[str] | None = None
    max_bytes: int = DEFAULT_MAX_BYTES

    def __init__(self, contact: str) -> None:
        if not contact.strip():
            raise ValueError("SEC requires a contact name and email in the User-Agent header")
        self._contact = contact

    def build_request(self, query: SubmissionsQuery) -> httpx.Request:
        url = f"{SEC_DATA_HOST}/submissions/CIK{query.cik:010d}.json"
        return httpx.Request("GET", url, headers={"User-Agent": self._contact})

    def shape(self, payload: bytes) -> pd.DataFrame:
        table = pd.DataFrame(_rows(json.loads(payload)), columns=list(COLUMNS))
        return table.astype({"cik": "int64", "index_complete": "bool"})

    def mismatch(self, query: SubmissionsQuery, table: pd.DataFrame) -> str | None:
        """Why the payload is not about the company asked for, if it is not."""
        ciks = set(table["cik"])
        if ciks != {query.cik}:
            return f"payload is for CIK {sorted(ciks)}, asked for {query.cik}"
        return None


def filings_before(
    table: pd.DataFrame, as_of: date, *, forms: Collection[str] | None = None
) -> pd.DataFrame:
    """Filings public on ``as_of`` (filed before it), optionally of some forms only."""
    visible = table[table["public_on"] <= as_of]
    if forms is not None:
        visible = visible[visible["form"].isin(set(forms))]
    return visible.reset_index(drop=True)


def eight_k_item_facts(fetched: Fetched) -> list[TextFact]:
    """One text fact per item code of every 8-K in the index.

    Every fact carries its filing's ``public_on``, and the store's as-of views filter on
    it, so callers need not filter by date first.
    """
    facts = []
    for row in fetched.table.to_dict("records"):
        if row["form"] not in EIGHT_K_FORMS:
            continue
        for item in row["items"]:
            facts.append(
                text_fact(
                    cik=int(row["cik"]),
                    field=EIGHT_K_ITEM_FIELD,
                    value=item,
                    period_end=row["report_date"],
                    public_on=row["public_on"],
                    fetched_at=fetched.fetched_at,
                    source_url=fetched.url,
                    content_sha256=fetched.content_sha256,
                    accession=row["accession"],
                )
            )
    return facts
