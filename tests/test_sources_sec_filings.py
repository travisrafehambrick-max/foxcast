"""SEC filing index and 8-K items, from the submissions API (Stage 1A.3; brief F1)."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from foxcast.sources.base import Fetched, NoData, Unavailable, run_provider
from foxcast.sources.sec_filings import (
    EIGHT_K_ITEM_FIELD,
    SecSubmissionsProvider,
    SubmissionsQuery,
    eight_k_item_facts,
    filings_before,
)

FIXTURE = (Path(__file__).parent / "fixtures" / "sec_submissions_googl.json").read_bytes()
GOOGL_CIK = 1652044
FETCHED_AT = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)


def fetch(payload: bytes = FIXTURE, status: int = 200, cik: int = GOOGL_CIK) -> object:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, content=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_provider(
        SecSubmissionsProvider("Jane Doe jane@example.com"),
        SubmissionsQuery(cik=cik),
        client,
        now=lambda: FETCHED_AT,
    )
    fetch.last_request = seen[0] if seen else None  # type: ignore[attr-defined]
    return result


def fetched() -> Fetched:
    result = fetch()
    assert isinstance(result, Fetched), result
    return result


@pytest.mark.integration
def test_the_request_goes_to_the_submissions_api_with_a_contact() -> None:
    fetch()
    request = fetch.last_request  # type: ignore[attr-defined]
    assert str(request.url) == "https://data.sec.gov/submissions/CIK0001652044.json"
    assert request.headers["User-Agent"] == "Jane Doe jane@example.com"


@pytest.mark.integration
def test_the_filing_index_has_one_row_per_filing_with_its_dates() -> None:
    table = fetched().table
    assert len(table) == 41
    assert table["accession"].is_unique
    ten_k = table[table["form"] == "10-K"].iloc[0]
    assert ten_k["accession"] == "0001652044-26-000018"
    assert ten_k["filed"] == date(2026, 2, 5)
    assert ten_k["report_date"] == date(2025, 12, 31)
    assert ten_k["primary_document"] == "goog-20251231.htm"
    assert ten_k["public_on"] == date(2026, 2, 6)  # filed on D, public from D + 1


@pytest.mark.integration
def test_eight_k_items_are_split_into_a_tuple() -> None:
    table = fetched().table
    earnings = table[table["accession"] == "0001652044-26-000080"].iloc[0]
    assert earnings["form"] == "8-K"
    assert earnings["items"] == ("2.02", "9.01")
    ownership = table[table["form"] == "4"].iloc[0]
    assert ownership["items"] == ()


@pytest.mark.integration
def test_filings_before_a_date_hide_anything_not_yet_public() -> None:
    table = fetched().table
    visible = filings_before(table, date(2026, 11, 5))  # its own filing day
    assert "0001193125-26-449835" not in set(visible["accession"])  # filed 2026-11-05
    later = filings_before(table, date(2026, 11, 6))  # the next day
    assert "0001193125-26-449835" in set(later["accession"])
    assert list(filings_before(table, date(2026, 11, 7), forms={"10-K"})["form"]) == ["10-K"]


@pytest.mark.integration
def test_each_eight_k_item_becomes_a_text_fact_with_provenance() -> None:
    result = fetched()
    facts = eight_k_item_facts(result)
    by_accession: dict[str, list[str]] = {}
    for fact in facts:
        by_accession.setdefault(str(fact.accession), []).append(fact.value)
    assert by_accession == {
        "0001193125-26-449835": ["8.01", "9.01"],
        "0001652044-26-000080": ["2.02", "9.01"],
    }
    earnings = next(f for f in facts if f.value == "2.02")
    assert earnings.field == EIGHT_K_ITEM_FIELD
    assert earnings.cik == GOOGL_CIK
    assert earnings.period_end == date(2026, 10, 28)
    assert earnings.public_on == date(2026, 10, 29)
    assert earnings.content_sha256 == result.content_sha256
    assert earnings.source_url == result.url
    assert len({f.fact_id for f in facts}) == len(facts)


@pytest.mark.integration
def test_a_payload_for_another_company_is_refused() -> None:
    result = fetch(cik=320193)
    assert isinstance(result, Unavailable)
    assert "wrong subject" in result.reason


@pytest.mark.integration
def test_not_found_is_no_data_and_bad_rows_break_the_contract() -> None:
    assert isinstance(fetch(status=404), NoData)
    payload = json.loads(FIXTURE)
    payload["filings"]["recent"]["accessionNumber"][0] = "not-an-accession"
    broken = fetch(json.dumps(payload).encode())
    assert isinstance(broken, Unavailable)
    assert "contract" in broken.reason


@pytest.mark.integration
def test_columns_of_unequal_length_are_refused() -> None:
    payload = json.loads(FIXTURE)
    payload["filings"]["recent"]["form"].pop()
    result = fetch(json.dumps(payload).encode())
    assert isinstance(result, Unavailable)
    assert "shape" in result.reason


@pytest.mark.unit
def test_a_query_needs_a_positive_cik() -> None:
    with pytest.raises(ValueError):
        SubmissionsQuery(cik=0)
    with pytest.raises(ValueError):
        SecSubmissionsProvider("  ")


@pytest.mark.integration
def test_an_index_with_older_filings_elsewhere_says_it_is_incomplete() -> None:
    assert bool(fetched().table["index_complete"].all())
    payload = json.loads(FIXTURE)
    payload["filings"]["files"] = [{"name": "CIK0001652044-submissions-001.json"}]
    result = fetch(json.dumps(payload).encode())
    assert isinstance(result, Fetched)
    assert not bool(result.table["index_complete"].any())


@pytest.mark.integration
def test_a_10k_is_hidden_on_its_filing_day_and_visible_the_next() -> None:
    table = fetched().table
    assert filings_before(table, date(2026, 2, 5), forms={"10-K"}).empty
    assert len(filings_before(table, date(2026, 2, 6), forms={"10-K"})) == 1
