"""SEC companyconcept provider: every value keeps its filing date and accession number."""

from datetime import date
from pathlib import Path

import httpx
import pytest

from foxcast.sources.base import Fetched, Unavailable, run_provider
from foxcast.sources.sec import ConceptQuery, SecConceptProvider, first_reported, latest_reported

FIXTURE = (Path(__file__).parent / "fixtures" / "sec_companyconcept_revenues.json").read_bytes()
CONTACT = "Jane Doe jane@example.com"


def sec_client(body: bytes = FIXTURE) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == CONTACT
        assert request.url.path == "/api/xbrl/companyconcept/CIK0001234567/us-gaap/Revenues.json"
        return httpx.Response(200, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def fetch() -> Fetched:
    provider = SecConceptProvider(contact=CONTACT)
    result = run_provider(provider, ConceptQuery(cik=1234567, tag="Revenues"), sec_client())
    assert isinstance(result, Fetched), result
    return result


@pytest.mark.integration
def test_every_value_keeps_filing_date_and_accession() -> None:
    table = fetch().table
    assert len(table) == 3
    assert set(table["accession"]) == {
        "0001234567-24-000010",
        "0001234567-24-000031",
        "0001234567-24-000020",
    }
    assert table["filed"].notna().all()
    assert (table["cik"] == 1234567).all()


@pytest.mark.integration
def test_first_reported_and_latest_differ_for_a_restated_value() -> None:
    table = fetch().table
    first = first_reported(table)
    latest = latest_reported(table)
    fy2023 = (first["period_end"] == date(2023, 12, 31)) & (first["fiscal_period"] == "FY")
    assert first.loc[fy2023, "value"].item() == 1_000_000
    assert first.loc[fy2023, "accession"].item() == "0001234567-24-000010"
    fy2023_latest = latest["period_end"] == date(2023, 12, 31)
    assert latest.loc[fy2023_latest, "value"].item() == 950_000
    assert len(first) == 2
    assert len(latest) == 2


@pytest.mark.integration
def test_as_of_query_only_sees_values_public_by_then() -> None:
    table = fetch().table
    before_restatement = latest_reported(table, as_of=date(2024, 3, 1))
    assert before_restatement["value"].tolist() == [1_000_000]


@pytest.mark.unit
def test_contact_is_required() -> None:
    with pytest.raises(ValueError, match="contact"):
        SecConceptProvider(contact="")


@pytest.mark.unit
def test_bad_accession_breaks_the_contract() -> None:
    broken = FIXTURE.replace(b"0001234567-24-000010", b"not-an-accession")
    provider = SecConceptProvider(contact=CONTACT)
    result = run_provider(provider, ConceptQuery(cik=1234567, tag="Revenues"), sec_client(broken))
    assert isinstance(result, Unavailable)
    assert "contract" in result.reason
