"""The fact record (brief F2, change AF).

A fact is one fetched value with its provenance and three times:

- the period it covers (``period_start`` to ``period_end``);
- ``public_on``: the first day the public could have known it;
- ``fetched_at``: when foxcast fetched it.

Point-in-time queries filter on ``public_on``. The ``fact_id`` is a hash of the source copy
and the fact's identifying fields, so the same fetch always yields the same IDs, and a
model can cite a fact only by an ID that code issued.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from foxcast.facts.ids import canonical_id
from foxcast.sources.base import Fetched

# SEC stores only the filing date, so a filing dated D is public from D + 1 (see sources.sec).
SEC_PUBLIC_LAG: Final = timedelta(days=1)


@dataclass(frozen=True)
class Fact:
    fact_id: str
    cik: int
    field: str
    unit: str
    period_start: date | None
    period_end: date
    value: float
    public_on: date
    fetched_at: datetime
    source_url: str
    content_sha256: str
    accession: str | None


def facts_from_sec(fetched: Fetched) -> list[Fact]:
    """Turn a fetched SEC company-concept table into facts."""
    facts = []
    for row in fetched.table.to_dict("records"):
        field = f"{row['taxonomy']}:{row['tag']}"
        start = row["period_start"]
        period_start: date | None = start if isinstance(start, date) else None
        period_end: date = row["period_end"]
        filed: date = row["filed"]
        accession = str(row["accession"])
        fact_id = canonical_id(
            fetched.content_sha256,
            str(row["cik"]),
            field,
            str(row["unit"]),
            period_start.isoformat() if period_start else "",
            period_end.isoformat(),
            accession,
        )
        facts.append(
            Fact(
                fact_id=fact_id,
                cik=int(row["cik"]),
                field=field,
                unit=str(row["unit"]),
                period_start=period_start,
                period_end=period_end,
                value=float(row["value"]),
                public_on=filed + SEC_PUBLIC_LAG,
                fetched_at=fetched.fetched_at,
                source_url=fetched.url,
                content_sha256=fetched.content_sha256,
                accession=accession,
            )
        )
    return facts


__all__ = ["SEC_PUBLIC_LAG", "Fact", "facts_from_sec"]
