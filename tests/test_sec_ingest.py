"""Ingesting one company's SEC record into the store (Stage 1A.3)."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from foxcast.facts.record import verify_passage
from foxcast.sec_ingest import IngestPlan, ingest_company
from foxcast.store.archive import SourceArchive
from foxcast.store.pit import FactStore

FIXTURES = Path(__file__).parent / "fixtures"
SUBMISSIONS = (FIXTURES / "sec_submissions_googl.json").read_bytes()
EIGHT_K = (FIXTURES / "sec_8k_googl_20261028.htm").read_bytes()
FORM4 = (FIXTURES / "sec_form4_googl_20261030.xml").read_bytes()
CONCEPT = (FIXTURES / "sec_companyconcept_revenues.json").read_bytes()
GOOGL = 1652044
NOW = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)


def concept_for(cik: int) -> bytes:
    payload = json.loads(CONCEPT)
    payload["cik"] = cik
    return json.dumps(payload).encode()


def sec(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.endswith("/submissions/CIK0001652044.json"):
        return httpx.Response(200, content=SUBMISSIONS)
    if "/companyconcept/" in url:
        if url.endswith("/Revenues.json"):
            return httpx.Response(200, content=concept_for(GOOGL))
        return httpx.Response(404)
    if url.endswith("/ownership.xml"):
        return httpx.Response(200, content=FORM4, headers={"content-type": "text/xml"})
    if url.endswith(".htm"):
        return httpx.Response(200, content=EIGHT_K, headers={"content-type": "text/html"})
    return httpx.Response(500)


def run(tmp_path: Path, as_of: date, plan: IngestPlan | None = None, handler=sec):  # type: ignore[no-untyped-def]
    store = FactStore(tmp_path / "facts.duckdb")
    archive = SourceArchive(tmp_path / "sources")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = ingest_company(
        GOOGL,
        as_of=as_of,
        client=client,
        contact="Jane Doe jane@example.com",
        store=store,
        archive=archive,
        plan=plan or IngestPlan(concepts=("Revenues", "NetIncomeLoss"), max_form4=3),
        now=lambda: NOW,
        throttle=None,
    )
    return report, store, archive


@pytest.mark.integration
def test_a_company_is_ingested_as_facts_and_passages(tmp_path: Path) -> None:
    report, store, archive = run(tmp_path, date(2026, 11, 11))
    assert report.cik == GOOGL
    assert report.index_complete
    assert report.documents == ("10-K 0001652044-26-000018", "10-Q 0001652044-26-000084",
                                "8-K 0001193125-26-449835", "8-K 0001652044-26-000080")  # fmt: skip
    assert report.passages > 0
    assert report.text_facts > 0
    assert report.numbers > 0
    view = store.at(date(2026, 11, 11))
    eight_k = store.passages_as_of(
        cik=GOOGL, accession="0001652044-26-000080", as_of=date(2026, 11, 11)
    )
    assert eight_k
    assert all(verify_passage(p, archive.load(p.content_sha256)) for p in eight_k)
    assert view.get_passage(eight_k[0].passage_id) == eight_k[0]
    items = store.text_as_of(cik=GOOGL, field="sec:8-K:item", as_of=date(2026, 11, 11))
    assert {i.value for i in items} == {"2.02", "8.01", "9.01"}


@pytest.mark.integration
def test_what_could_not_be_fetched_is_reported_not_raised(tmp_path: Path) -> None:
    report, _, _ = run(tmp_path, date(2026, 11, 11))
    assert any("NetIncomeLoss" in reason for reason in report.unavailable)


@pytest.mark.integration
def test_nothing_filed_on_or_after_the_as_of_date_is_ingested(tmp_path: Path) -> None:
    report, store, _ = run(tmp_path, date(2026, 10, 29))
    assert "10-Q 0001652044-26-000084" not in report.documents  # filed 2026-10-29
    assert "8-K 0001652044-26-000080" in report.documents  # filed 2026-10-28
    assert "8-K 0001193125-26-449835" not in report.documents
    late = store.passages_as_of(cik=GOOGL, accession="0001652044-26-000084", as_of=date(2030, 1, 1))
    assert late == []


@pytest.mark.integration
def test_a_failed_index_fetch_ends_the_run_with_a_reason(tmp_path: Path) -> None:
    report, _, _ = run(tmp_path, date(2026, 11, 11), handler=lambda r: httpx.Response(503))
    assert report.documents == ()
    assert report.unavailable
    assert "HTTP 503" in report.unavailable[0]


@pytest.mark.integration
def test_running_twice_adds_nothing_new(tmp_path: Path) -> None:
    first, _, _ = run(tmp_path, date(2026, 11, 11))
    second, _, _ = run(tmp_path, date(2026, 11, 11))
    assert first.passages > 0
    assert (second.passages, second.text_facts, second.numbers) == (0, 0, 0)


@pytest.mark.unit
def test_a_plan_refuses_nonsense_limits() -> None:
    with pytest.raises(ValueError):
        IngestPlan(max_8k=-1)
    with pytest.raises(ValueError):
        IngestPlan(concepts=("bad tag!",))
