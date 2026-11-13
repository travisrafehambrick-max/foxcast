"""Fiscal-period labels on XBRL facts (carry-forward from the 1A.5 review).

SEC's ``fy`` and ``fp`` describe the filing a row came from, not the row's own period: a
FY2025 10-K tags its FY2024 comparative ``fy=2025, fp=FY`` too. A period is labelled only
where a filing reports it as its own current period, and every row for that period then
carries the label. The synthetic company here has a fiscal year ending in late September,
so its fiscal Q1 is October to December.
"""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest

from foxcast.claims.checker import CheckContext, check_claim
from foxcast.claims.render import ClaimType, RenderedClaim, SubmittedClaim, review_claim
from foxcast.facts.fiscal import fiscal_labels
from foxcast.facts.record import Fact, facts_from_sec
from foxcast.sec_ingest import IngestPlan, ingest_company
from foxcast.sources.base import Fetched
from foxcast.store.archive import SourceArchive
from foxcast.store.pit import FactStore

from .test_sec_ingest import GOOGL, NOW, sec

AS_OF = date(2026, 11, 11)
# accession, form, filed, fy, fp
TEN_K_25 = ("0001652044-25-000100", "10-K", "2025-10-31", 2025, "FY")
TEN_Q_26_Q1 = ("0001652044-26-000010", "10-Q", "2026-01-30", 2026, "Q1")
TEN_Q_26_Q2 = ("0001652044-26-000020", "10-Q", "2026-05-01", 2026, "Q2")
LATE_10K_A = ("0001652044-26-000099", "10-K/A", "2026-08-01", 2025, "FY")
# start, end, value, filing
ROWS = [
    # The FY2025 10-K: its own year, its own Q4, and the FY2024 comparative.
    ("2024-09-29", "2025-09-27", 416_000, TEN_K_25),
    ("2025-06-29", "2025-09-27", 102_000, TEN_K_25),
    ("2023-10-01", "2024-09-28", 391_000, TEN_K_25),
    # Fiscal Q1 2026, and its prior-year comparative (fiscal Q1 2025).
    ("2025-09-28", "2025-12-27", 124_000, TEN_Q_26_Q1),
    ("2024-09-29", "2024-12-28", 119_000, TEN_Q_26_Q1),
    # Fiscal Q2 2026 and the half-year to date.
    ("2025-12-28", "2026-03-28", 95_000, TEN_Q_26_Q2),
    ("2025-09-28", "2026-03-28", 219_000, TEN_Q_26_Q2),
    # A late amendment restating FY2025: it repeats the value but cannot label it.
    ("2024-09-29", "2025-09-27", 416_500, LATE_10K_A),
]


def row(start: str, end: str, value: float, filing: tuple[Any, ...]) -> dict[str, Any]:
    accession, form, filed, fy, fp = filing
    return {
        "start": start,
        "end": end,
        "val": value,
        "accn": accession,
        "form": form,
        "filed": filed,
        "fy": fy,
        "fp": fp,
    }


def payload(rows: list[tuple[Any, ...]] = ROWS, tag: str = "Revenues") -> bytes:
    items = [row(*r) for r in rows]
    body = {"cik": GOOGL, "taxonomy": "us-gaap", "tag": tag, "units": {"USD": items}}
    return json.dumps(body).encode()


def records(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    """Rows in the shape the provider's table hands to ``fiscal_labels``."""
    return [
        {
            "period_start": date.fromisoformat(r[0]) if r[0] else None,
            "period_end": date.fromisoformat(r[1]),
            "accession": r[3][0],
            "form": r[3][1],
            "filed": date.fromisoformat(r[3][2]),
            "fiscal_year": r[3][3],
            "fiscal_period": r[3][4],
        }
        for r in rows
    ]


def key(start: str, end: str) -> tuple[date, date]:
    return date.fromisoformat(start), date.fromisoformat(end)


# --- the labelling rule ---


@pytest.mark.unit
def test_a_filing_labels_its_own_periods() -> None:
    labels = fiscal_labels(records(ROWS))
    assert labels[key("2024-09-29", "2025-09-27")] == (2025, "FY")
    assert labels[key("2025-06-29", "2025-09-27")] == (2025, "Q4")
    assert labels[key("2025-09-28", "2025-12-27")] == (2026, "Q1")
    assert labels[key("2025-12-28", "2026-03-28")] == (2026, "Q2")
    assert labels[key("2025-09-28", "2026-03-28")] == (2026, "H1")


@pytest.mark.unit
def test_comparatives_are_not_labelled_with_the_filing_that_repeats_them() -> None:
    labels = fiscal_labels(records(ROWS))
    # Tagged fy=2025 FY and fy=2026 Q1 by the filings that repeat them; both are wrong.
    assert key("2023-10-01", "2024-09-28") not in labels
    assert key("2024-09-29", "2024-12-28") not in labels


@pytest.mark.unit
def test_a_comparative_is_labelled_by_its_own_filing_when_that_is_in_the_payload() -> None:
    own = ("0001652044-25-000010", "10-Q", "2025-01-31", 2025, "Q1")
    labels = fiscal_labels(records([*ROWS, ("2024-09-29", "2024-12-28", 119_000, own)]))
    assert labels[key("2024-09-29", "2024-12-28")] == (2025, "Q1")


@pytest.mark.unit
def test_a_filing_that_reports_only_old_periods_labels_nothing() -> None:
    # A concept the FY2025 10-K reports for FY2024 only: its latest period is not current.
    labels = fiscal_labels(records([("2023-10-01", "2024-09-28", 391_000, TEN_K_25)]))
    assert labels == {}


@pytest.mark.unit
def test_filings_that_disagree_on_a_period_leave_it_unlabelled() -> None:
    other = ("0001652044-26-000011", "10-Q", "2026-02-02", 2026, "Q2")
    rows = [("2025-09-28", "2025-12-27", 124_000, TEN_Q_26_Q1),
            ("2025-09-28", "2025-12-27", 124_000, other)]  # fmt: skip
    assert fiscal_labels(records(rows)) == {}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("fy", "fp", "form"),
    [
        (None, "Q1", "10-Q"),  # no fiscal year
        (2026, None, "10-Q"),  # no fiscal period
        (2026, "Q5", "10-Q"),  # not a period
        (2026, "CY", "10-Q"),
        (2031, "Q1", "10-Q"),  # a fiscal year far from the period
        (True, "Q1", "10-Q"),  # a boolean is not a year
        (2026, "Q1", "8-K"),  # not a periodic report
        (2026, "Q1", "S-1"),
    ],
)
def test_odd_tags_and_forms_label_nothing(fy: Any, fp: Any, form: str) -> None:
    filing = ("0001652044-26-000010", form, "2026-01-30", fy, fp)
    assert fiscal_labels(records([("2025-09-28", "2025-12-27", 1, filing)])) == {}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("start", "fp", "expected"),
    [
        ("2025-09-28", "Q1", (2026, "Q1")),  # 90 days
        ("2025-06-29", "Q3", None),  # 181 days in a Q3 report is the year to date, not H1
        ("2025-03-30", "Q3", None),  # 272 days: nine months, no label
        (None, "Q1", None),  # an instant (a balance) has no fiscal period
    ],
)
def test_only_quarter_half_and_year_lengths_are_labelled(
    start: str | None, fp: str, expected: tuple[int, str] | None
) -> None:
    filing = ("0001652044-26-000010", "10-Q", "2026-01-30", 2026, fp)
    labels = fiscal_labels(records([(start, "2025-12-27", 1, filing)]))
    start_day = date.fromisoformat(start) if start else None
    assert labels.get((start_day, date(2025, 12, 27))) == expected


@pytest.mark.unit
def test_an_annual_reports_latest_quarter_is_q4_only_at_its_year_end() -> None:
    # Review finding 2: a 10-K whose latest quarter row is July to September, with no
    # annual row ending there, filed within the window. It is not Q4.
    late = ("0001652044-26-000005", "10-K", "2026-01-23", 2025, "FY")  # 118 days on
    rows = [("2025-06-29", "2025-09-27", 1, late)]
    assert fiscal_labels(records(rows)) == {}
    with_year = [*rows, ("2024-09-29", "2025-09-27", 4, late)]
    assert fiscal_labels(records(with_year))[key("2025-06-29", "2025-09-27")] == (2025, "Q4")


@pytest.mark.unit
@pytest.mark.parametrize("form", ["10-KT", "10-QT", "10-KT/A"])
def test_transition_reports_label_nothing(form: str) -> None:
    filing = ("0001652044-26-000007", form, "2026-02-10", 2025, "FY")
    rows = [("2025-09-28", "2025-12-27", 1, filing), ("2025-06-29", "2025-12-27", 2, filing)]
    assert fiscal_labels(records(rows)) == {}


@pytest.mark.unit
def test_a_filing_long_after_its_period_labels_nothing() -> None:
    # Only the late 10-K/A reports this period; 308 days on, it is not reporting its own.
    labels = fiscal_labels(records([ROWS[-1]]))
    assert labels == {}


@pytest.mark.unit
def test_labels_ignore_pandas_missing_values() -> None:
    import pandas as pd

    rows = records([ROWS[3]])
    rows[0]["fiscal_year"] = pd.NA
    assert fiscal_labels(rows) == {}


# --- labels on facts and in the store ---


def fetched(raw: bytes) -> Fetched:
    from foxcast.sources.sec import SecConceptProvider

    table = SecConceptProvider("Jane Doe jane@example.com").shape(raw)
    return Fetched(
        table=table,
        raw=raw,
        content_sha256="a" * 64,
        url="https://data.sec.gov/x",
        fetched_at=NOW,
    )


def by_period(facts: list[Fact]) -> dict[tuple[date | None, date, str], Fact]:
    return {(f.period_start, f.period_end, f.accession or ""): f for f in facts}


@pytest.mark.unit
def test_every_row_for_a_labelled_period_carries_the_label() -> None:
    facts = by_period(facts_from_sec(fetched(payload())))
    original = facts[(*key("2024-09-29", "2025-09-27"), TEN_K_25[0])]
    restated = facts[(*key("2024-09-29", "2025-09-27"), LATE_10K_A[0])]
    assert (original.fiscal_year, original.fiscal_period) == (2025, "FY")
    assert (restated.fiscal_year, restated.fiscal_period) == (2025, "FY")
    comparative = facts[(*key("2023-10-01", "2024-09-28"), TEN_K_25[0])]
    assert (comparative.fiscal_year, comparative.fiscal_period) == (None, None)


@pytest.mark.integration
def test_labels_survive_the_store(tmp_path: Path) -> None:
    store = FactStore(tmp_path / "facts.duckdb")
    facts = facts_from_sec(fetched(payload()))
    store.add(facts)
    stored = store.as_of(cik=GOOGL, field="us-gaap:Revenues", as_of=AS_OF)
    assert set(stored) == set(facts)
    assert any(f.fiscal_period == "H1" for f in stored)


@pytest.mark.integration
def test_a_store_made_before_labels_gains_the_columns(tmp_path: Path) -> None:
    path = tmp_path / "facts.duckdb"
    FactStore(path)
    with duckdb.connect(str(path)) as connection:
        connection.execute("ALTER TABLE facts DROP COLUMN fiscal_year")
        connection.execute("ALTER TABLE facts DROP COLUMN fiscal_period")
    store = FactStore(path)
    facts = facts_from_sec(fetched(payload()))
    store.add(facts)
    assert set(store.as_of(cik=GOOGL, field="us-gaap:Revenues", as_of=AS_OF)) == set(facts)


@pytest.mark.integration
def test_a_populated_store_made_before_labels_gains_them_on_re_add(tmp_path: Path) -> None:
    # Review finding 4: stored rows without labels must take them, not raise a conflict.
    store = FactStore(tmp_path / "facts.duckdb")
    facts = facts_from_sec(fetched(payload()))
    store.add([replace(f, fiscal_year=None, fiscal_period=None) for f in facts])
    assert store.add(facts) == 0
    assert set(store.as_of(cik=GOOGL, field="us-gaap:Revenues", as_of=AS_OF)) == set(facts)


@pytest.mark.integration
def test_a_stored_label_is_never_changed(tmp_path: Path) -> None:
    from foxcast.store.pit import StoreConflictError

    store = FactStore(tmp_path / "facts.duckdb")
    labelled = next(f for f in facts_from_sec(fetched(payload())) if f.fiscal_period)
    store.add([labelled])
    with pytest.raises(StoreConflictError):
        store.add([replace(labelled, fiscal_period="Q3")])
    with pytest.raises(StoreConflictError):
        store.add([replace(labelled, fiscal_year=None, fiscal_period=None)])


# --- the checker reads fiscal words against the labels ---


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "/companyconcept/" in url:
        if url.endswith("/Revenues.json"):
            return httpx.Response(200, content=payload())
        return httpx.Response(404)
    return sec(request)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> tuple[FactStore, SourceArchive]:
    folder = tmp_path_factory.mktemp("fiscal")
    store, archive = FactStore(folder / "facts.duckdb"), SourceArchive(folder / "sources")
    ingest_company(
        GOOGL,
        as_of=AS_OF,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        contact="Jane Doe jane@example.com",
        store=store,
        archive=archive,
        plan=IngestPlan(concepts=("Revenues",), max_form4=0),
        now=lambda: NOW,
        throttle=None,
    )
    return store, archive


def ctx(world: tuple[FactStore, SourceArchive]) -> CheckContext:
    store, archive = world
    return CheckContext(view=store.at(AS_OF), archive=archive, subject_cik=GOOGL, companies={})


def revenue(world: tuple[FactStore, SourceArchive], start: str, end: str, accession: str) -> Fact:
    facts = world[0].as_of(cik=GOOGL, field="us-gaap:Revenues", as_of=AS_OF)
    return by_period(facts)[(*key(start, end), accession)]


def problems(world: tuple[FactStore, SourceArchive], words: str, fact: Fact) -> list[str]:
    text = f"Revenue in {words} was {{{fact.fact_id}}}."
    claim = review_claim(SubmittedClaim("summary", ClaimType.FACT, text, (fact.fact_id,)),
                         ctx(world).view)  # fmt: skip
    assert isinstance(claim, RenderedClaim), claim
    return check_claim(claim, ctx(world))


Q1_FY26 = ("2025-09-28", "2025-12-27", TEN_Q_26_Q1[0])
Q2_FY26 = ("2025-12-28", "2026-03-28", TEN_Q_26_Q2[0])
Q1_FY25_COMPARATIVE = ("2024-09-29", "2024-12-28", TEN_Q_26_Q1[0])
H1_FY26 = ("2025-09-28", "2026-03-28", TEN_Q_26_Q2[0])
FY25 = ("2024-09-29", "2025-09-27", TEN_K_25[0])
FY24_COMPARATIVE = ("2023-10-01", "2024-09-28", TEN_K_25[0])


@pytest.mark.integration
@pytest.mark.parametrize(
    ("period", "words", "ok"),
    [
        # Fiscal wording is judged by the label.
        (Q1_FY26, "fiscal Q1 2026", True),
        (Q1_FY26, "Q1 FY2026", True),
        (Q1_FY26, "Q1 fiscal 2026", True),
        (Q1_FY26, "Q1 of fiscal 2026", True),
        (Q1_FY26, "the first quarter of fiscal 2026", True),
        (Q1_FY26, "the fiscal first quarter of fiscal 2026", True),
        (Q1_FY26, "the fiscal first quarter", True),
        (Q1_FY26, "fiscal Q1", True),
        (Q1_FY26, "fiscal Q1 2025", False),
        (Q1_FY26, "fiscal Q2 2026", False),
        (Q1_FY26, "Q1 FY2025", False),
        # The year first, or the quarter word last (review finding 1).
        (Q1_FY26, "fiscal 2026 Q1", True),
        (Q1_FY26, "FY2026 first quarter", True),
        (Q1_FY26, "the first quarter fiscal 2026", True),
        (Q2_FY26, "fiscal 2026 Q1", False),
        (Q2_FY26, "FY2026 first quarter", False),
        (Q2_FY26, "the first quarter fiscal 2026", False),
        (Q2_FY26, "fiscal 2026 Q2", True),
        # A plain quarter beside fiscal wording is ambiguous and withheld (finding 3).
        (Q2_FY26, "Q1 and Q2 of fiscal 2026", False),
        (Q2_FY26, "fiscal-Q1 2026", False),
        (Q2_FY26, "Q1 2026 of fiscal year", False),
        # Plain quarter words stay calendar quarters: October to December is Q4.
        (Q1_FY26, "Q4 2025", True),
        (Q1_FY26, "Q1 2026", False),
        (Q1_FY26, "the first quarter", False),
        # A comparative with no label cannot carry fiscal words.
        (Q1_FY25_COMPARATIVE, "fiscal Q1 2025", False),
        (Q1_FY25_COMPARATIVE, "Q4 2024", True),
        # Halves.
        (H1_FY26, "fiscal H1 2026", True),
        (H1_FY26, "H1 FY2026", True),
        (H1_FY26, "the first half of fiscal 2026", True),
        (H1_FY26, "fiscal H2 2026", False),
        # Fiscal years: by the label when there is one.
        (FY25, "FY2025", True),
        (FY25, "fiscal 2025", True),
        (FY25, "fiscal year 2025", True),
        (FY25, "FY2024", False),
        # Without a label, only a year ending in December counts as its fiscal year.
        (FY24_COMPARATIVE, "FY2024", False),
    ],
)
def test_fiscal_words_are_judged_by_the_fiscal_label(
    world: tuple[FactStore, SourceArchive], period: tuple[str, str, str], words: str, ok: bool
) -> None:
    found = problems(world, words, revenue(world, *period))
    assert (found == []) is ok, found


@pytest.mark.integration
def test_the_late_amendment_carries_the_label_of_its_period(
    world: tuple[FactStore, SourceArchive],
) -> None:
    restated = revenue(world, "2024-09-29", "2025-09-27", LATE_10K_A[0])
    assert problems(world, "FY2025", restated) == []
