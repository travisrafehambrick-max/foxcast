"""Text facts, passages and derived facts (Stage 1A.2; brief change BC)."""

import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from foxcast.facts.derived import (
    MAX_TRACE_DEPTH,
    Basis,
    DerivedFact,
    Method,
    UntraceableError,
    derive,
    recompute,
    trace,
)
from foxcast.facts.ids import canonical_id
from foxcast.facts.record import (
    MAX_PASSAGE_CHARS,
    MAX_TEXT_CHARS,
    Fact,
    Passage,
    TextFact,
    decode_archive,
    passage_from_span,
    text_fact,
    verify_passage,
)
from foxcast.store.pit import FactStore, StoreConflictError

FETCHED_AT = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)
NEW_YORK = timezone(timedelta(hours=-5))
SHA = "a" * 64
ACCESSION = "0000000001-26-000001"
DOCUMENT = "Item 1A. Risk Factors\n\nOur business depends on a small number of customers. " * 3
ARCHIVED = DOCUMENT.encode("utf-8")


def number(
    value: float,
    *,
    fact_id: str,
    field: str = "us-gaap:Revenues",
    unit: str = "USD",
    start: date | None = date(2025, 1, 1),
    end: date = date(2025, 12, 31),
    public_on: date = date(2026, 2, 1),
    cik: int = 1,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        cik=cik,
        field=field,
        unit=unit,
        period_start=start,
        period_end=end,
        value=value,
        public_on=public_on,
        fetched_at=FETCHED_AT,
        source_url="https://example.test/facts",
        content_sha256=SHA,
        accession=ACCESSION,
    )


def year(value: float, fiscal_year: int, *, fact_id: str, field: str = "us-gaap:Revenues") -> Fact:
    return number(
        value,
        fact_id=fact_id,
        field=field,
        start=date(fiscal_year, 1, 1),
        end=date(fiscal_year, 12, 31),
        public_on=date(fiscal_year + 1, 2, 1),
    )


REV_2024 = year(100.0, 2024, fact_id="f_rev2024")
REV_2025 = year(150.0, 2025, fact_id="f_rev2025")
NI_2025 = year(30.0, 2025, fact_id="f_ni2025", field="us-gaap:NetIncomeLoss")
ASSETS_END_2025 = number(
    600.0, fact_id="f_assets2025", field="us-gaap:Assets", start=None, end=date(2025, 12, 31)
)


def lookup_of(*facts: Fact | DerivedFact) -> dict[str, Fact | DerivedFact]:
    return {f.fact_id: f for f in facts}


# --- text facts ---


def make_text(value: str = "Ernst & Young LLP", **overrides: object) -> TextFact:
    kwargs: dict[str, object] = {
        "cik": 1,
        "field": "dei:AuditorName",
        "value": value,
        "period_end": date(2025, 12, 31),
        "public_on": date(2026, 2, 1),
        "fetched_at": FETCHED_AT,
        "source_url": "https://example.test/facts",
        "content_sha256": SHA,
        "accession": ACCESSION,
    }
    kwargs.update(overrides)
    return text_fact(**kwargs)  # type: ignore[arg-type]


@pytest.mark.unit
def test_text_fact_ids_are_stable_and_depend_on_the_value() -> None:
    assert make_text().fact_id == make_text().fact_id
    assert make_text().fact_id != make_text("Deloitte & Touche LLP").fact_id
    assert make_text().fact_id.startswith("f_")


@pytest.mark.unit
def test_a_text_fact_and_a_number_built_from_the_same_fields_get_different_ids() -> None:
    text = make_text(value="100", field="us-gaap:Revenues", period_end=date(2025, 12, 31))
    # The numeric fact ID scheme of facts_from_sec, over the same provenance and fields.
    numeric_id = canonical_id(SHA, "1", "us-gaap:Revenues", "", "", "2025-12-31", ACCESSION)
    assert text.fact_id != numeric_id


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "x" * (MAX_TEXT_CHARS + 1),
        "bad" + chr(0x00) + "value",
        "evil" + chr(0x202E) + "reversed",  # right-to-left override
        "zero" + chr(0x200B) + "width",
        "line" + chr(0x2028) + "break",
        "para" + chr(0x2029) + "break",
        "variation" + chr(0xFE0F) + "selector",
        "variation" + chr(0xE0100) + "supplement",
        "tag" + chr(0xE0041) + "characters",
    ],
)
def test_text_fact_refuses_empty_oversized_or_hidden_character_text(value: str) -> None:
    with pytest.raises(ValueError):
        make_text(value)


# --- passages ---


def make_passage(start: int = 0, end: int = 21, archived: bytes = ARCHIVED) -> Passage:
    return passage_from_span(
        archived,
        start,
        end,
        cik=1,
        accession=ACCESSION,
        form="10-K",
        section="Item 1A",
        public_on=date(2026, 2, 1),
        fetched_at=FETCHED_AT,
        source_url="https://example.test/10k.htm",
    )


@pytest.mark.unit
def test_a_passage_holds_its_span_and_both_hashes() -> None:
    passage = make_passage()
    assert passage.text == "Item 1A. Risk Factors"
    assert passage.text_sha256 == hashlib.sha256(b"Item 1A. Risk Factors").hexdigest()
    assert passage.content_sha256 == hashlib.sha256(ARCHIVED).hexdigest()
    assert passage.passage_id.startswith("p_")
    assert (passage.start, passage.end) == (0, 21)


@pytest.mark.unit
def test_passage_ids_are_stable_and_depend_on_the_location_and_the_document() -> None:
    assert make_passage().passage_id == make_passage().passage_id
    assert make_passage().passage_id != make_passage(23, 60).passage_id
    assert make_passage().passage_id != make_passage(archived=ARCHIVED + b" ").passage_id


@pytest.mark.unit
@pytest.mark.parametrize(("start", "end"), [(-1, 5), (5, 5), (10, 5), (0, len(DOCUMENT) + 1)])
def test_bad_spans_are_refused(start: int, end: int) -> None:
    with pytest.raises(ValueError, match="outside"):
        make_passage(start, end)


@pytest.mark.unit
def test_oversized_passages_are_refused() -> None:
    long_document = b"x" * (MAX_PASSAGE_CHARS + 10)
    with pytest.raises(ValueError, match="longer than"):
        make_passage(0, MAX_PASSAGE_CHARS + 1, long_document)
    assert make_passage(0, MAX_PASSAGE_CHARS, long_document).end == MAX_PASSAGE_CHARS


@pytest.mark.unit
def test_a_document_that_is_not_utf8_is_refused_with_a_value_error() -> None:
    with pytest.raises(ValueError, match="UTF-8"):
        make_passage(0, 3, b"ab\xed\xa0\x80cd")  # an encoded lone surrogate
    with pytest.raises(ValueError, match="UTF-8"):
        decode_archive(b"\xff")


@pytest.mark.unit
def test_a_passage_with_hidden_characters_is_refused() -> None:
    with pytest.raises(ValueError, match="invisible"):
        make_passage(0, 8, ("safe" + chr(0x202E) + "txt.exe").encode())


@pytest.mark.unit
def test_a_passage_verifies_only_against_its_own_archived_bytes() -> None:
    passage = make_passage(23, 60)
    assert verify_passage(passage, ARCHIVED)
    altered = ARCHIVED[:30] + b"X" + ARCHIVED[31:]
    assert not verify_passage(passage, altered)  # different document hash
    assert not verify_passage(passage, ARCHIVED[:40])  # truncated
    assert not verify_passage(replace(passage, text="Our business"), ARCHIVED)
    assert not verify_passage(replace(passage, start=-37), ARCHIVED)  # negative start
    assert not verify_passage(replace(passage, text_sha256="0" * 64), ARCHIVED)
    assert not verify_passage(replace(passage, content_sha256="0" * 64), ARCHIVED)


@pytest.mark.unit
def test_verification_fails_cleanly_on_bytes_that_are_not_utf8() -> None:
    bad = b"\xff\xfe"
    passage = replace(make_passage(), content_sha256=hashlib.sha256(bad).hexdigest())
    assert not verify_passage(passage, bad)


@pytest.mark.unit
def test_crlf_documents_keep_their_offsets() -> None:
    archived = b"Line one\r\nLine two\r\n"
    passage = make_passage(10, 18, archived)
    assert passage.text == "Line two"
    assert verify_passage(passage, archived)


@given(
    st.text(
        alphabet=st.characters(exclude_categories=("Cs", "Cc", "Cf")),
        min_size=1,
        max_size=200,
    ),
    st.data(),
)
def test_any_valid_span_round_trips(document: str, data: st.DataObject) -> None:
    start = data.draw(st.integers(0, len(document) - 1))
    end = data.draw(st.integers(start + 1, len(document)))
    archived = document.encode("utf-8")
    passage = make_passage(start, end, archived)
    assert passage.text == document[start:end]
    assert verify_passage(passage, archived)


# --- derived facts ---


@pytest.mark.unit
def test_growth_between_two_years_records_its_inputs_method_and_basis() -> None:
    growth = derive(Method.GROWTH, REV_2024, REV_2025)
    assert growth.value == pytest.approx(0.5)
    assert growth.inputs == (REV_2024.fact_id, REV_2025.fact_id)
    assert growth.method is Method.GROWTH
    assert growth.basis is Basis.YEAR_ON_YEAR
    assert growth.unit == "pure"
    assert growth.field == "derived:growth(us-gaap:Revenues)"
    assert (growth.period_start, growth.period_end) == (date(2024, 1, 1), date(2025, 12, 31))
    assert growth.fact_id.startswith("f_")


@pytest.mark.unit
def test_difference_keeps_the_unit() -> None:
    change = derive(Method.DIFFERENCE, REV_2024, REV_2025)
    assert change.value == pytest.approx(50.0)
    assert change.unit == "USD"


@pytest.mark.unit
def test_a_margin_is_a_ratio_within_one_period() -> None:
    margin = derive(Method.RATIO, NI_2025, REV_2025)
    assert margin.value == pytest.approx(0.2)
    assert margin.unit == "pure"
    assert margin.basis is Basis.SAME_PERIOD
    assert margin.field == "derived:ratio(us-gaap:NetIncomeLoss/us-gaap:Revenues)"
    assert (margin.period_start, margin.period_end) == (REV_2025.period_start, REV_2025.period_end)


@pytest.mark.unit
def test_a_flow_over_a_stock_at_the_period_end_is_allowed() -> None:
    roa = derive(Method.RATIO, NI_2025, ASSETS_END_2025)
    assert roa.value == pytest.approx(0.05)
    assert roa.period_start == NI_2025.period_start


@pytest.mark.unit
def test_sequential_quarters_and_year_on_year_quarters_are_both_allowed() -> None:
    q2 = number(10.0, fact_id="f_q2", start=date(2025, 4, 1), end=date(2025, 6, 30))
    q3 = number(12.0, fact_id="f_q3", start=date(2025, 7, 1), end=date(2025, 9, 30))
    q3_last_year = number(8.0, fact_id="f_q3y", start=date(2024, 7, 1), end=date(2024, 9, 30))
    assert derive(Method.GROWTH, q2, q3).basis is Basis.SEQUENTIAL
    assert derive(Method.GROWTH, q3_last_year, q3).basis is Basis.YEAR_ON_YEAR


@pytest.mark.unit
def test_balance_sheet_dates_a_quarter_or_a_year_apart_are_allowed() -> None:
    june = replace(ASSETS_END_2025, fact_id="f_june", period_end=date(2025, 6, 30))
    september = replace(ASSETS_END_2025, fact_id="f_sept", period_end=date(2025, 9, 30))
    last_year = replace(ASSETS_END_2025, fact_id="f_ly", period_end=date(2024, 12, 31))
    assert derive(Method.DIFFERENCE, june, september).basis is Basis.SEQUENTIAL
    assert derive(Method.DIFFERENCE, last_year, ASSETS_END_2025).basis is Basis.YEAR_ON_YEAR


@pytest.mark.unit
def test_quarters_of_slightly_different_length_can_be_compared() -> None:
    q3_2024 = number(10.0, fact_id="f_q3a", start=date(2024, 6, 30), end=date(2024, 9, 28))
    q3_2025 = number(
        12.0,
        fact_id="f_q3b",
        start=date(2025, 6, 29),
        end=date(2025, 9, 27),
        public_on=date(2025, 10, 30),
    )
    assert derive(Method.GROWTH, q3_2024, q3_2025).value == pytest.approx(0.2)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("a_public", "b_public", "expected"),
    [
        (date(2026, 3, 15), date(2026, 2, 1), date(2026, 3, 15)),  # the first input is later
        (date(2026, 2, 1), date(2026, 3, 15), date(2026, 3, 15)),  # the second input is later
    ],
)
def test_a_derived_fact_is_public_only_once_all_its_inputs_are(
    a_public: date, b_public: date, expected: date
) -> None:
    a = replace(NI_2025, public_on=a_public)
    b = replace(REV_2025, public_on=b_public)
    assert derive(Method.RATIO, a, b).public_on == expected


@pytest.mark.unit
def test_derived_ids_are_stable_and_depend_on_the_inputs() -> None:
    first = derive(Method.GROWTH, REV_2024, REV_2025)
    assert first.fact_id == derive(Method.GROWTH, REV_2024, REV_2025).fact_id
    other = replace(REV_2025, fact_id="f_rev2025b", value=151.0)
    assert first.fact_id != derive(Method.GROWTH, REV_2024, other).fact_id
    assert first.fact_id != derive(Method.DIFFERENCE, REV_2024, REV_2025).fact_id


REV_2020 = year(50.0, 2020, fact_id="f_rev2020")
REV_H2_2025 = number(80.0, fact_id="f_h2", start=date(2025, 7, 1), end=date(2025, 12, 31))
REV_OVERLAP = number(140.0, fact_id="f_ov", start=date(2024, 7, 1), end=date(2025, 6, 30))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "a", "b", "why"),
    [
        (Method.GROWTH, REV_2024, replace(REV_2025, cik=2), "company"),
        (Method.GROWTH, REV_2024, replace(REV_2025, unit="EUR"), "unit"),
        (Method.GROWTH, REV_2024, NI_2025, "field"),
        (Method.GROWTH, REV_2025, REV_2024, "earlier"),
        (Method.GROWTH, REV_2024, replace(REV_2025, period_start=None), "instant"),
        (Method.GROWTH, replace(REV_2024, value=0.0), REV_2025, "base"),
        (Method.GROWTH, replace(REV_2024, value=-5.0), REV_2025, "base"),
        (Method.GROWTH, replace(REV_2024, value=float("nan")), REV_2025, "finite"),
        (Method.GROWTH, REV_2020, REV_2025, "adjacent"),
        (Method.GROWTH, REV_OVERLAP, REV_2025, "overlapping"),
        (Method.DIFFERENCE, REV_2024, REV_H2_2025, "length"),
        (Method.RATIO, NI_2025, REV_2024, "period"),
        (Method.RATIO, NI_2025, replace(REV_2025, value=0.0), "zero"),
        (Method.RATIO, NI_2025, replace(REV_2025, unit="EUR"), "unit"),
        (Method.RATIO, NI_2025, replace(REV_2025, period_start=date(2025, 7, 1)), "period"),
    ],
)
def test_unsafe_derivations_are_refused(method: Method, a: Fact, b: Fact, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        derive(method, a, b)


def margins() -> tuple[DerivedFact, DerivedFact, DerivedFact, Fact]:
    ni_2024 = year(25.0, 2024, fact_id="f_ni2024", field="us-gaap:NetIncomeLoss")
    margin_2024 = derive(Method.RATIO, ni_2024, REV_2024)
    margin_2025 = derive(Method.RATIO, NI_2025, REV_2025)
    return margin_2024, margin_2025, derive(Method.DIFFERENCE, margin_2024, margin_2025), ni_2024


@pytest.mark.unit
def test_trace_leads_back_to_the_source_facts_in_first_seen_order() -> None:
    margin_2024, margin_2025, change, ni_2024 = margins()
    lookup = lookup_of(REV_2024, REV_2025, NI_2025, ni_2024, margin_2024, margin_2025)
    assert [f.fact_id for f in trace(change, lookup)] == [
        "f_ni2024",
        "f_rev2024",
        "f_ni2025",
        "f_rev2025",
    ]
    assert change.value == pytest.approx(0.2 - 0.25)


@pytest.mark.unit
def test_trace_lists_a_shared_source_once() -> None:
    # A diamond: both shares divide by the same 2025 revenue.
    cost = year(90.0, 2025, fact_id="f_cost2025", field="us-gaap:CostOfRevenue")
    ni_share = derive(Method.RATIO, NI_2025, REV_2025)
    cost_share = derive(Method.RATIO, cost, REV_2025)
    lookup = lookup_of(REV_2025, NI_2025, cost, ni_share, cost_share)
    top = replace(ni_share, fact_id="f_top", inputs=(ni_share.fact_id, cost_share.fact_id))
    assert [f.fact_id for f in trace(top, lookup)] == ["f_ni2025", "f_rev2025", "f_cost2025"]


@pytest.mark.unit
def test_trace_fails_loudly_when_an_input_is_missing() -> None:
    growth = derive(Method.GROWTH, REV_2024, REV_2025)
    with pytest.raises(UntraceableError, match="f_rev2024"):
        trace(growth, lookup_of(REV_2025))


@pytest.mark.unit
def test_a_derived_fact_cannot_rest_on_a_text_value() -> None:
    growth = derive(Method.GROWTH, REV_2024, REV_2025)
    text = replace(make_text(), fact_id=REV_2024.fact_id)
    with pytest.raises(UntraceableError, match="not a number"):
        trace(growth, {text.fact_id: text, REV_2025.fact_id: REV_2025})


@pytest.mark.unit
def test_trace_and_recompute_refuse_derivations_nested_too_deep() -> None:
    # A chain of ratios, each dividing the last result by another "pure" fact.
    first_denominator = replace(REV_2025, fact_id="f_denominator0")
    current: DerivedFact = derive(Method.RATIO, REV_2025, first_denominator)
    known: list[Fact | DerivedFact] = [REV_2025, first_denominator, current]
    for i in range(1, MAX_TRACE_DEPTH + 1):
        denominator = replace(REV_2025, fact_id=f"f_denominator{i}", unit="pure")
        current = derive(Method.RATIO, current, denominator)
        known.extend([denominator, current])
    with pytest.raises(UntraceableError, match="deeper"):
        trace(current, lookup_of(*known))
    with pytest.raises(UntraceableError, match="deeper"):
        recompute(current, lookup_of(*known))


@pytest.mark.unit
def test_recompute_rebuilds_the_record_and_accepts_an_honest_one() -> None:
    growth = derive(Method.GROWTH, REV_2024, REV_2025)
    assert recompute(growth, lookup_of(REV_2024, REV_2025)) == growth


@pytest.mark.unit
@pytest.mark.parametrize(
    "tampered",
    [
        {"value": 0.9},
        {"value": 0.5 + 1e-13},  # exact, not "close enough"
        {"public_on": date(2020, 1, 1)},  # backdated: would leak into the past (row 2d)
        {"unit": "USD"},
        {"period_end": date(2026, 12, 31)},
        {"period_start": None},
        {"basis": Basis.SEQUENTIAL},
        {"field": "derived:growth(us-gaap:NetIncomeLoss)"},
        {"fact_id": "f_other"},
    ],
)
def test_recompute_refuses_a_record_tampered_in_any_field(tampered: dict[str, object]) -> None:
    growth = derive(Method.GROWTH, REV_2024, REV_2025)
    with pytest.raises(UntraceableError, match="does not match"):
        recompute(replace(growth, **tampered), lookup_of(REV_2024, REV_2025))  # type: ignore[arg-type]


@pytest.mark.unit
def test_recompute_refuses_when_an_input_changed_or_no_longer_qualifies() -> None:
    growth = derive(Method.GROWTH, REV_2024, REV_2025)
    with pytest.raises(UntraceableError, match="does not match"):
        recompute(growth, lookup_of(REV_2024, replace(REV_2025, value=999.0)))
    with pytest.raises(UntraceableError, match="no longer allow"):
        recompute(growth, lookup_of(REV_2024, replace(REV_2025, cik=2)))
    with pytest.raises(UntraceableError, match="exactly two"):
        recompute(replace(growth, inputs=(REV_2024.fact_id,)), lookup_of(REV_2024))


@pytest.mark.unit
def test_recompute_checks_nested_derivations_too() -> None:
    margin_2024, margin_2025, change, ni_2024 = margins()
    lookup = lookup_of(REV_2024, REV_2025, NI_2025, ni_2024, margin_2024)
    bad_margin = replace(margin_2025, value=0.9)  # same ID, wrong value
    with pytest.raises(UntraceableError, match=margin_2025.fact_id):
        recompute(change, {**lookup, margin_2025.fact_id: bad_margin})


# --- the store ---


def store_with_revenue(tmp_path: Path) -> FactStore:
    store = FactStore(tmp_path / "facts.duckdb")
    store.add([REV_2024, REV_2025])
    return store


@pytest.mark.unit
def test_every_kind_round_trips_exactly_including_offsets_and_empty_fields(
    tmp_path: Path,
) -> None:
    store = FactStore(tmp_path / "facts.duckdb")
    local_time = datetime(2026, 11, 11, 10, 0, tzinfo=NEW_YORK)
    text = make_text(period_end=None, accession=None, fetched_at=local_time)
    passage = replace(make_passage(23, 60), fetched_at=local_time)
    instant = replace(ASSETS_END_2025, accession=None, fetched_at=local_time)
    store.add([instant])
    store.add_text([text])
    store.add_passages([passage])
    view = store.at(date(2026, 2, 1))

    got_text = view.get(text.fact_id)
    assert got_text == text
    assert isinstance(got_text, TextFact)
    assert got_text.fetched_at.utcoffset() == timedelta(hours=-5)
    got_passage = view.get_passage(passage.passage_id)
    assert got_passage == passage
    assert got_passage is not None
    assert got_passage.fetched_at.utcoffset() == timedelta(hours=-5)
    assert view.get(instant.fact_id) == instant


@pytest.mark.unit
def test_derived_facts_round_trip_with_enums_and_tuples(tmp_path: Path) -> None:
    store = store_with_revenue(tmp_path)
    growth = derive(Method.GROWTH, REV_2024, REV_2025)
    assert store.add_derived([growth]) == 1
    got = store.at(date(2026, 2, 1)).get(growth.fact_id)
    assert got == growth
    assert isinstance(got, DerivedFact)
    assert got.method is Method.GROWTH
    assert got.basis is Basis.YEAR_ON_YEAR
    assert got.inputs == growth.inputs


@pytest.mark.unit
def test_as_of_queries_respect_the_public_date(tmp_path: Path) -> None:
    store = store_with_revenue(tmp_path)
    text = make_text()
    undated = make_text("KPMG LLP", period_end=None)
    passage = make_passage(23, 60)
    growth = derive(Method.GROWTH, REV_2024, REV_2025)
    store.add_text([text, undated])
    store.add_passages([passage])
    store.add_derived([growth])

    texts = store.text_as_of(cik=1, field="dei:AuditorName", as_of=date(2026, 2, 1))
    assert texts == [undated, text]  # an undated value sorts first
    assert store.text_as_of(cik=1, field="dei:AuditorName", as_of=date(2026, 1, 31)) == []
    on_time = store.passages_as_of(cik=1, accession=ACCESSION, as_of=date(2026, 2, 1))
    assert on_time == [passage]
    assert store.passages_as_of(cik=1, accession=ACCESSION, as_of=date(2026, 1, 1)) == []
    assert store.derived_as_of(cik=1, as_of=date(2026, 2, 1)) == [growth]
    assert store.derived_as_of(cik=1, as_of=date(2026, 1, 31)) == []


@pytest.mark.unit
def test_a_view_cannot_see_anything_made_public_after_its_date(tmp_path: Path) -> None:
    store = store_with_revenue(tmp_path)
    growth = derive(Method.GROWTH, REV_2024, REV_2025)
    passage = make_passage(23, 60)
    store.add_derived([growth])
    store.add_passages([passage])
    early = store.at(date(2025, 6, 1))  # after 2024's revenue, before 2025's
    assert early.get(REV_2024.fact_id) == REV_2024
    assert early.get(REV_2025.fact_id) is None
    assert early.get(growth.fact_id) is None
    assert early.get_passage(passage.passage_id) is None
    late = store.at(date(2026, 2, 1))
    assert set(late.get_many([REV_2024.fact_id, growth.fact_id, "f_missing"])) == {
        REV_2024.fact_id,
        growth.fact_id,
    }
    assert {f.fact_id for f in trace(growth, late)} == {"f_rev2024", "f_rev2025"}
    with pytest.raises(UntraceableError):
        trace(growth, early)


@pytest.mark.unit
def test_adding_the_same_content_again_is_skipped_even_if_fetched_later(tmp_path: Path) -> None:
    store = FactStore(tmp_path / "facts.duckdb")
    text = make_text()
    assert store.add_text([text]) == 1
    assert store.add_text([replace(text, fetched_at=FETCHED_AT + timedelta(days=3))]) == 0
    assert store.add_text([]) == 0
    assert store.add_passages([make_passage(), make_passage()]) == 1


@pytest.mark.unit
def test_an_id_stored_with_other_content_is_refused_not_dropped(tmp_path: Path) -> None:
    store = FactStore(tmp_path / "facts.duckdb")
    text = make_text()
    store.add_text([text])
    with pytest.raises(StoreConflictError, match="other content"):
        store.add_text([replace(text, public_on=date(2026, 3, 1))])
    with pytest.raises(StoreConflictError, match="two different"):
        store.add([REV_2024, replace(REV_2024, value=1.0)])


@pytest.mark.unit
def test_the_store_refuses_a_derived_fact_that_does_not_rebuild(tmp_path: Path) -> None:
    store = store_with_revenue(tmp_path)
    growth = derive(Method.GROWTH, REV_2024, REV_2025)
    with pytest.raises(UntraceableError):
        store.add_derived([replace(growth, public_on=date(2020, 1, 1))])
    with pytest.raises(UntraceableError):
        FactStore(tmp_path / "empty.duckdb").add_derived([growth])  # inputs not stored
    assert store.derived_as_of(cik=1, as_of=date(2030, 1, 1)) == []


@pytest.mark.unit
def test_derived_facts_in_one_call_may_build_on_each_other(tmp_path: Path) -> None:
    store = FactStore(tmp_path / "facts.duckdb")
    margin_2024, margin_2025, change, ni_2024 = margins()
    store.add([REV_2024, REV_2025, NI_2025, ni_2024])
    assert store.add_derived([margin_2024, margin_2025, change]) == 3
    assert store.at(date(2026, 2, 1)).get(change.fact_id) == change


@pytest.mark.unit
def test_ids_refuse_an_unknown_prefix_or_a_separator_in_a_part() -> None:
    assert canonical_id("a", prefix="p").startswith("p_")
    with pytest.raises(ValueError, match="prefix"):
        canonical_id("a", prefix="x")
    with pytest.raises(ValueError, match="separator"):
        canonical_id("a\x1fb")


@pytest.mark.unit
def test_a_view_returns_text_facts_by_id(tmp_path: Path) -> None:
    store = FactStore(tmp_path / "facts.duckdb")
    text = make_text()
    store.add_text([text])
    assert store.at(date(2026, 2, 1)).get(text.fact_id) == text
    assert store.at(date(2026, 1, 31)).get(text.fact_id) is None


@pytest.mark.unit
def test_a_backdated_derived_fact_never_becomes_visible(tmp_path: Path) -> None:
    store = store_with_revenue(tmp_path)
    growth = derive(Method.GROWTH, REV_2024, REV_2025)
    backdated = replace(growth, public_on=date(2025, 6, 1))
    with pytest.raises(UntraceableError):
        store.add_derived([backdated])
    for day in (date(2025, 6, 1), date(2026, 2, 1), date(2030, 1, 1)):
        assert store.at(day).get(growth.fact_id) is None
