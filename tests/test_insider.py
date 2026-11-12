"""Form 4 insider trades as facts (Stage 1A.3; brief F1)."""

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from foxcast.facts.record import Fact, TextFact
from foxcast.sources.base import Fetched, Unavailable, run_provider
from foxcast.sources.insider import (
    Form4Query,
    SecForm4Provider,
    form4_facts,
    raw_ownership_document,
)

FIXTURE = (Path(__file__).parent / "fixtures" / "sec_form4_googl_20261030.xml").read_bytes()
FETCHED_AT = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)
QUERY = Form4Query(cik=1652044, accession="0001193125-26-442116")


def fetch(payload: bytes = FIXTURE, query: Form4Query = QUERY) -> object:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=payload, headers={"content-type": "text/xml"})
        )
    )
    return run_provider(
        SecForm4Provider("Jane Doe jane@example.com"), query, client, now=lambda: FETCHED_AT
    )


def fetched() -> Fetched:
    result = fetch()
    assert isinstance(result, Fetched), result
    return result


@pytest.mark.unit
def test_the_raw_xml_is_fetched_not_the_rendered_view() -> None:
    assert raw_ownership_document("xslF345X06/ownership.xml") == "ownership.xml"
    assert raw_ownership_document("wf-form4_123.xml") == "wf-form4_123.xml"
    request = SecForm4Provider("Jane Doe jane@example.com").build_request(QUERY)
    assert str(request.url) == (
        "https://www.sec.gov/Archives/edgar/data/1652044/000119312526442116/ownership.xml"
    )


@pytest.mark.integration
def test_a_real_form4_gives_one_row_per_transaction() -> None:
    table = fetched().table
    assert len(table) == 1  # one sale; the other entries are holdings, not trades
    sale = table.iloc[0]
    assert sale["owner_name"] == "ARNOLD FRANCES"
    assert sale["owner_ciks"] == (1665419,)
    assert sale["roles"] == ("director",)
    assert sale["security"] == "Class C Capital Stock"
    assert sale["transaction_date"] == date(2026, 10, 29)
    assert sale["code"] == "S"
    assert sale["acquired_disposed"] == "D"
    assert sale["shares"] == 82.0
    assert sale["price"] == 284.0
    assert sale["owned_after"] == 19157.0
    assert bool(sale["ten_b5_one"]) is True


@pytest.mark.integration
def test_each_transaction_becomes_cited_facts_with_provenance() -> None:
    result = fetched()
    numbers, texts = form4_facts(result, accession=QUERY.accession, filed=date(2026, 10, 30))
    by_field = {f.field: f for f in [*numbers, *texts]}
    shares = by_field["sec:form4:t1:shares"]
    assert isinstance(shares, Fact)
    assert (shares.value, shares.unit) == (82.0, "shares")
    assert shares.period_end == date(2026, 10, 29)
    assert shares.public_on == date(2026, 10, 31)
    assert shares.cik == 1652044
    assert shares.content_sha256 == result.content_sha256
    assert by_field["sec:form4:t1:price"].unit == "USD/shares"  # type: ignore[union-attr]
    owner = by_field["sec:form4:t1:owner"]
    assert isinstance(owner, TextFact)
    assert owner.value == "ARNOLD FRANCES"
    assert by_field["sec:form4:t1:code"].value == "S"  # type: ignore[union-attr]
    assert by_field["sec:form4:t1:role"].value == "director"  # type: ignore[union-attr]
    ids = [f.fact_id for f in [*numbers, *texts]]
    assert len(ids) == len(set(ids))


def with_transaction_edit(old: bytes, new: bytes) -> bytes:
    assert old in FIXTURE
    return FIXTURE.replace(old, new, 1)


@pytest.mark.integration
def test_a_trade_without_a_price_has_no_price_fact() -> None:
    payload = with_transaction_edit(
        b"<transactionPricePerShare>\n                    <value>284</value>",
        b"<transactionPricePerShare>\n                    <value></value>",
    )
    result = fetch(payload)
    assert isinstance(result, Fetched)
    numbers, _ = form4_facts(result, accession=QUERY.accession, filed=date(2026, 10, 30))
    assert "sec:form4:t1:price" not in {f.field for f in numbers}


@pytest.mark.integration
def test_a_form4_for_another_issuer_is_refused() -> None:
    result = fetch(query=Form4Query(cik=320193, accession=QUERY.accession))
    assert isinstance(result, Unavailable)
    assert "wrong subject" in result.reason


@pytest.mark.integration
@pytest.mark.parametrize(
    "payload",
    [
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><ownershipDocument/>',
        b"<ownershipDocument><unclosed></ownershipDocument>",
        b"<somethingElse/>",
    ],
)
def test_unsafe_or_malformed_xml_is_unavailable(payload: bytes) -> None:
    result = fetch(payload)
    assert isinstance(result, Unavailable), result


@pytest.mark.integration
def test_an_unknown_transaction_code_breaks_the_contract() -> None:
    payload = with_transaction_edit(
        b"<transactionCode>S</transactionCode>", b"<transactionCode>Q</transactionCode>"
    )
    result = fetch(payload)
    assert isinstance(result, Unavailable)
    assert "contract" in result.reason


@pytest.mark.unit
def test_a_form4_query_validates_its_accession() -> None:
    with pytest.raises(ValueError):
        Form4Query(cik=1652044, accession="nope")
    with pytest.raises(ValueError):
        Form4Query(cik=0, accession=QUERY.accession)


def numbers_and_texts(payload: bytes) -> tuple[list[Fact], list[TextFact]]:
    result = fetch(payload)
    assert isinstance(result, Fetched), result
    return form4_facts(result, accession=QUERY.accession, filed=date(2026, 10, 30))


@pytest.mark.integration
def test_the_10b5_1_flag_is_a_fact() -> None:
    _, texts = numbers_and_texts(FIXTURE)
    assert {t.field: t.value for t in texts}["sec:form4:t1:10b5-1"] == "yes"


@pytest.mark.integration
def test_a_joint_filing_lists_every_owner() -> None:
    start = FIXTURE.index(b"<reportingOwner>")
    end = FIXTURE.index(b"</reportingOwner>") + len(b"</reportingOwner>")
    owner = FIXTURE[start:end]
    second = owner.replace(b"0001665419", b"0000000042").replace(b"ARNOLD FRANCES", b"SOME FUND LP")
    result = fetch(FIXTURE.replace(owner, owner + second, 1))
    assert isinstance(result, Fetched), result
    row = result.table.iloc[0]
    assert row["owner_ciks"] == (1665419, 42)
    assert row["owner_name"] == "ARNOLD FRANCES; SOME FUND LP"


@pytest.mark.integration
def test_utf16_xml_cannot_slip_a_doctype_past_the_screen() -> None:
    sneaky = (
        '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY a "b">]><ownershipDocument/>'
    )
    result = fetch(sneaky.encode("utf-16"))
    assert isinstance(result, Unavailable)
    assert "UTF-8" in result.reason


@pytest.mark.integration
def test_a_declared_non_utf8_encoding_is_refused() -> None:
    payload = FIXTURE.replace(
        b'<?xml version="1.0"?>', b'<?xml version="1.0" encoding="ISO-8859-1"?>', 1
    )
    result = fetch(payload)
    assert isinstance(result, Unavailable)
    assert "encoding" in result.reason


@pytest.mark.integration
@pytest.mark.parametrize("bad", [b"inf", b"nan", b"-inf"])
def test_numbers_must_be_finite(bad: bytes) -> None:
    result = fetch(with_transaction_edit(b"<value>82</value>", b"<value>" + bad + b"</value>"))
    assert isinstance(result, Unavailable)


@pytest.mark.integration
@pytest.mark.parametrize("name", [b"ARNOLD" + chr(0x202E).encode() + b"FRANCES", b"A" * 600])
def test_hostile_owner_text_makes_the_filing_unavailable_not_a_crash(name: bytes) -> None:
    result = fetch(FIXTURE.replace(b"ARNOLD FRANCES", name, 1))
    assert isinstance(result, Unavailable)
    assert "hidden" in result.reason
