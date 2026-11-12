"""Ingest one company's SEC record into the fact store and archive (Stage 1A.3).

One call fetches, for a company and an as-of date:

- the filing index (submissions API) and every 8-K's item codes, as text facts;
- the latest 10-K and 10-Q, and recent 8-Ks, as passages of archived extracted text;
- recent Form 4 insider trades, as numbers and text;
- a fixed list of XBRL concepts, as numbers.

Only what was public on the as-of date is kept, so a dossier built later on the same date
sees the same record. A source that cannot be fetched is never fatal: it is listed in
:attr:`IngestReport.unavailable` with its reason, for the dossier to state (row 1d).
Running again adds nothing that is already stored.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final, cast

import httpx
import pandas as pd

from foxcast.facts.record import Fact, TextFact, facts_from_sec
from foxcast.sources.base import Fetched, run_provider
from foxcast.sources.filing_text import (
    FilingDocumentQuery,
    SecDocumentProvider,
    archive_filing_text,
)
from foxcast.sources.insider import (
    Form4Query,
    SecForm4Provider,
    form4_facts,
    raw_ownership_document,
)
from foxcast.sources.sec import ConceptQuery, SecConceptProvider
from foxcast.sources.sec_filings import (
    SecSubmissionsProvider,
    SubmissionsQuery,
    eight_k_item_facts,
    filings_before,
)
from foxcast.sources.throttle import Throttle
from foxcast.store.archive import SourceArchive
from foxcast.store.pit import FactStore

DEFAULT_CONCEPTS: Final = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "CostOfRevenue",
    "OperatingIncomeLoss",
    "NetIncomeLoss",
    "EarningsPerShareDiluted",
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "CashAndCashEquivalentsAtCarryingValue",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
)
CONCEPT_NAME: Final = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class IngestPlan:
    """How much to fetch. The defaults suit one dossier."""

    concepts: tuple[str, ...] = DEFAULT_CONCEPTS
    max_8k: int = 10
    eight_k_lookback_days: int = 365
    max_form4: int = 20

    def __post_init__(self) -> None:
        for name in ("max_8k", "eight_k_lookback_days", "max_form4"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        for concept in self.concepts:
            if not CONCEPT_NAME.fullmatch(concept):
                raise ValueError(f"not an XBRL concept name: {concept!r}")


@dataclass
class IngestReport:
    cik: int
    as_of: date
    index_complete: bool = False
    documents: tuple[str, ...] = ()
    passages: int = 0
    text_facts: int = 0
    numbers: int = 0
    unavailable: list[str] = field(default_factory=list)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class _Context:
    cik: int
    as_of: date
    client: httpx.Client
    contact: str
    store: FactStore
    archive: SourceArchive
    now: Callable[[], datetime]
    throttle: Throttle | None

    def fetch(self, provider: Any, query: Any) -> Fetched | str:
        result = run_provider(provider, query, self.client, throttle=self.throttle, now=self.now)
        return result if isinstance(result, Fetched) else result.reason


def _public(facts: list[Any], as_of: date) -> list[Any]:
    return [f for f in facts if f.public_on <= as_of]


def _documents_to_read(index: pd.DataFrame, plan: IngestPlan, as_of: date) -> pd.DataFrame:
    """The latest 10-K and 10-Q, then recent 8-Ks, newest first within each form."""
    visible = filings_before(index, as_of).sort_values(["filed", "accession"], ascending=False)
    picked = [visible[visible["form"] == form].head(1) for form in ("10-K", "10-Q")]
    since = as_of - timedelta(days=plan.eight_k_lookback_days)
    eight_ks = visible[(visible["form"] == "8-K") & (visible["filed"] >= since)]
    picked.append(eight_ks.head(plan.max_8k))
    return pd.concat(picked, ignore_index=True)


def _read_documents(ctx: _Context, report: IngestReport, rows: pd.DataFrame) -> None:
    read = []
    for row in cast(list[dict[str, Any]], rows.to_dict("records")):
        label = f"{row['form']} {row['accession']}"
        query = FilingDocumentQuery(
            cik=ctx.cik, accession=row["accession"], document=row["primary_document"]
        )
        fetched = ctx.fetch(SecDocumentProvider(ctx.contact), query)
        if isinstance(fetched, str):
            report.unavailable.append(f"{label}: {fetched}")
            continue
        archived = archive_filing_text(
            fetched, ctx.archive, query=query, form=row["form"], filed=row["filed"]
        )
        report.passages += ctx.store.add_passages(list(archived.passages))
        read.append(label)
    report.documents = tuple(read)


def _read_form4s(ctx: _Context, report: IngestReport, index: pd.DataFrame, limit: int) -> None:
    visible = filings_before(index, ctx.as_of, forms={"4"})
    newest = visible.sort_values(["filed", "accession"], ascending=False).head(limit)
    for row in cast(list[dict[str, Any]], newest.to_dict("records")):
        query = Form4Query(
            cik=ctx.cik,
            accession=row["accession"],
            document=raw_ownership_document(row["primary_document"]),
        )
        fetched = ctx.fetch(SecForm4Provider(ctx.contact), query)
        if isinstance(fetched, str):
            report.unavailable.append(f"Form 4 {row['accession']}: {fetched}")
            continue
        numbers, texts = form4_facts(fetched, accession=row["accession"], filed=row["filed"])
        report.numbers += ctx.store.add(numbers)
        report.text_facts += ctx.store.add_text(texts)


def _read_concepts(ctx: _Context, report: IngestReport, concepts: tuple[str, ...]) -> None:
    for concept in concepts:
        fetched = ctx.fetch(SecConceptProvider(ctx.contact), ConceptQuery(cik=ctx.cik, tag=concept))
        if isinstance(fetched, str):
            report.unavailable.append(f"XBRL {concept}: {fetched}")
            continue
        facts: list[Fact] = _public(facts_from_sec(fetched), ctx.as_of)
        report.numbers += ctx.store.add(facts)


def ingest_company(
    cik: int,
    *,
    as_of: date,
    client: httpx.Client,
    contact: str,
    store: FactStore,
    archive: SourceArchive,
    plan: IngestPlan | None = None,
    now: Callable[[], datetime] = _utc_now,
    throttle: Throttle | None = None,
) -> IngestReport:
    """Fetch and store one company's SEC record as public on ``as_of``."""
    plan = plan or IngestPlan()
    ctx = _Context(cik, as_of, client, contact, store, archive, now, throttle)
    report = IngestReport(cik=cik, as_of=as_of)
    index = ctx.fetch(SecSubmissionsProvider(contact), SubmissionsQuery(cik=cik))
    if isinstance(index, str):
        report.unavailable.append(f"filing index: {index}")
        return report
    archive.save(index.raw)
    report.index_complete = bool(index.table["index_complete"].all())
    if not report.index_complete:
        report.unavailable.append("filing index: filings older than SEC's recent list")
    items: list[TextFact] = _public(eight_k_item_facts(index), as_of)
    report.text_facts += store.add_text(items)
    _read_documents(ctx, report, _documents_to_read(index.table, plan, as_of))
    _read_form4s(ctx, report, index.table, plan.max_form4)
    _read_concepts(ctx, report, plan.concepts)
    return report
