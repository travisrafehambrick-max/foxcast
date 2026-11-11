"""The fact record, source archive and point-in-time store (brief F2, F3, change AF)."""

import hashlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from foxcast.facts.record import Fact, facts_from_sec
from foxcast.sources.base import Fetched, run_provider
from foxcast.sources.sec import ConceptQuery, SecConceptProvider
from foxcast.store.archive import SourceArchive
from foxcast.store.pit import FactStore

FIXTURE = (Path(__file__).parent / "fixtures" / "sec_companyconcept_revenues.json").read_bytes()
FETCHED_AT = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)


def fetched() -> Fetched:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=FIXTURE))
    )
    result = run_provider(
        SecConceptProvider("Jane Doe jane@example.com"),
        ConceptQuery(cik=1234567, tag="Revenues"),
        client,
        now=lambda: FETCHED_AT,
    )
    assert isinstance(result, Fetched)
    return result


def make_fact(fact_id: str, public_on: date, value: float = 1.0) -> Fact:
    return Fact(
        fact_id=fact_id,
        cik=1,
        field="us-gaap:Revenues",
        unit="USD",
        period_start=None,
        period_end=date(2020, 12, 31),
        value=value,
        public_on=public_on,
        fetched_at=FETCHED_AT,
        source_url="https://example.test",
        content_sha256="0" * 64,
        accession=None,
    )


@pytest.mark.unit
def test_facts_from_sec_carry_three_times_and_provenance() -> None:
    facts = facts_from_sec(fetched())
    assert len(facts) == 3
    original = next(f for f in facts if f.accession == "0001234567-24-000010")
    assert original.period_end == date(2023, 12, 31)
    assert original.public_on == date(2024, 2, 16)  # filed 2024-02-15, public from the next day
    assert original.fetched_at == FETCHED_AT
    assert original.content_sha256 == hashlib.sha256(FIXTURE).hexdigest()
    assert original.field == "us-gaap:Revenues"


@pytest.mark.unit
def test_fact_ids_are_stable_and_unique() -> None:
    first = facts_from_sec(fetched())
    second = facts_from_sec(fetched())
    assert [f.fact_id for f in first] == [f.fact_id for f in second]
    assert len({f.fact_id for f in first}) == len(first)


@pytest.mark.unit
def test_archive_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    archive = SourceArchive(tmp_path / "sources")
    path = archive.save(FIXTURE)
    assert path.name.startswith(hashlib.sha256(FIXTURE).hexdigest())
    assert archive.save(FIXTURE) == path
    assert archive.load(hashlib.sha256(FIXTURE).hexdigest()) == FIXTURE


@pytest.mark.unit
def test_archive_detects_a_corrupted_copy(tmp_path: Path) -> None:
    archive = SourceArchive(tmp_path / "sources")
    path = archive.save(FIXTURE)
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash"):
        archive.load(hashlib.sha256(FIXTURE).hexdigest())


@pytest.mark.unit
def test_store_round_trip_and_idempotent_add(tmp_path: Path) -> None:
    store = FactStore(tmp_path / "facts.duckdb")
    facts = facts_from_sec(fetched())
    assert store.add(facts) == 3
    assert store.add(facts) == 0
    on_day = store.as_of(cik=1234567, field="us-gaap:Revenues", as_of=date(2024, 2, 16))
    assert [f.value for f in on_day] == [1_000_000.0]
    assert on_day[0] == next(f for f in facts if f.accession == "0001234567-24-000010")


@pytest.mark.unit
def test_store_as_of_excludes_the_filing_day_itself(tmp_path: Path) -> None:
    store = FactStore(tmp_path / "facts.duckdb")
    store.add(facts_from_sec(fetched()))
    assert store.as_of(cik=1234567, field="us-gaap:Revenues", as_of=date(2024, 2, 15)) == []


@settings(
    max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    offsets=st.lists(st.integers(min_value=0, max_value=60), min_size=1, max_size=15),
    query_offset=st.integers(min_value=0, max_value=60),
)
def test_as_of_never_returns_a_fact_not_yet_public(
    tmp_path_factory: pytest.TempPathFactory, offsets: list[int], query_offset: int
) -> None:
    start = date(2025, 1, 1)
    store = FactStore(tmp_path_factory.mktemp("pit") / "facts.duckdb")
    store.add([make_fact(f"f{i}", start + timedelta(days=o)) for i, o in enumerate(offsets)])
    as_of = start + timedelta(days=query_offset)
    returned = store.as_of(cik=1, field="us-gaap:Revenues", as_of=as_of)
    assert all(f.public_on <= as_of for f in returned)
    assert len(returned) == sum(1 for o in offsets if start + timedelta(days=o) <= as_of)
