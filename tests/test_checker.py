"""The citation checker (Stage 1A.5; scorecard rows 1a and 1c)."""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx
import pytest

from foxcast.claims.checker import (
    WORDS_CHECK_VERSION,
    CheckContext,
    check_claim,
    check_dossier,
    read_back,
    row_1a_failures,
)
from foxcast.claims.render import ClaimType, RenderedClaim, SubmittedClaim, review_claim
from foxcast.facts.derived import Method, derive
from foxcast.facts.record import Fact, passage_from_span
from foxcast.sec_ingest import IngestPlan, ingest_company
from foxcast.store.archive import SourceArchive
from foxcast.store.pit import FactStore

from .test_sec_ingest import GOOGL, NOW, sec

AS_OF = date(2026, 11, 11)
COMPANIES = {"GOOGL": GOOGL, "Alphabet": GOOGL, "AAPL": 320193, "Apple": 320193}
TEN_K = ("0001652044-26-000018", "10-K", "2026-02-05")
TEN_K_2024 = ("0001652044-25-000014", "10-K", "2025-02-04")
TEN_Q = ("0001652044-26-000084", "10-Q", "2026-10-29")
# Synthetic XBRL figures (fixtures may use SEC or synthetic data only, plan 1A.8).
CONCEPTS = {
    "Revenues": [
        ("2024-01-01", "2024-12-31", 350_018_000_000, TEN_K_2024),
        ("2025-01-01", "2025-12-31", 402_836_000_000, TEN_K),
        ("2025-07-01", "2025-09-30", 102_346_000_000, TEN_Q),
    ],
    "NetIncomeLoss": [("2025-01-01", "2025-12-31", 132_170_000_000, TEN_K)],
}


def concept_payload(tag: str) -> bytes:
    items = [
        {
            "start": s,
            "end": e,
            "val": v,
            "accn": a,
            "form": f,
            "filed": filed,
            "fy": int(e[:4]),
            "fp": "FY" if f == "10-K" else "Q3",
        }
        for s, e, v, (a, f, filed) in CONCEPTS[tag]
    ]
    payload = {"cik": GOOGL, "taxonomy": "us-gaap", "tag": tag, "units": {"USD": items}}
    return json.dumps(payload).encode()


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "/companyconcept/" in url:
        tag = url.rsplit("/", 1)[1].removesuffix(".json")
        return (
            httpx.Response(200, content=concept_payload(tag))
            if tag in CONCEPTS
            else (httpx.Response(404))
        )
    return sec(request)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> tuple[FactStore, SourceArchive]:
    folder = tmp_path_factory.mktemp("ingest")
    store, archive = FactStore(folder / "facts.duckdb"), SourceArchive(folder / "sources")
    ingest_company(
        GOOGL,
        as_of=AS_OF,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        contact="Jane Doe jane@example.com",
        store=store,
        archive=archive,
        plan=IngestPlan(concepts=tuple(CONCEPTS), max_form4=3),
        now=lambda: NOW,
    )
    return store, archive


def ctx(world: tuple[FactStore, SourceArchive]) -> CheckContext:
    store, archive = world
    return CheckContext(
        view=store.at(AS_OF), archive=archive, subject_cik=GOOGL, companies=COMPANIES
    )


def annual_revenue(store: FactStore) -> tuple[Fact, Fact]:
    facts = [
        f
        for f in store.as_of(cik=GOOGL, field="us-gaap:Revenues", as_of=AS_OF)
        if f.period_start and (f.period_end - f.period_start).days > 360
    ]
    return facts[-2], facts[-1]


def shown(
    world: tuple[FactStore, SourceArchive],
    text: str,
    cites: tuple[str, ...],
    kind: ClaimType = ClaimType.FACT,
) -> RenderedClaim:
    result = review_claim(SubmittedClaim("summary", kind, text, cites), ctx(world).view)
    assert isinstance(result, RenderedClaim), result
    return result


def ph(i: str) -> str:
    return "{" + i + "}"


# --- reading rendered values back ---


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "value", "half_unit"),
    [
        ("$402.8 billion", 402.8e9, 0.05e9),
        ("-$1.234 billion", -1.234e9, 0.0005e9),
        ("(1,234)", -1234.0, 0.5),
        ("19,157 shares", 19157.0, 0.5),
        ("$10.91 per share", 10.91, 0.005),
        ("+15.09%", 15.09, 0.005),
        ("-4.826 percentage points", -4.826, 0.0005),
        ("$950,000", 950000.0, 0.5),
    ],
)
def test_rendered_values_read_back_with_their_shown_precision(
    text: str, value: float, half_unit: float
) -> None:
    got = read_back(text)
    assert got is not None
    assert got[0] == pytest.approx(value)
    assert got[1] == pytest.approx(half_unit)


@pytest.mark.unit
def test_text_that_is_not_a_number_does_not_read_back() -> None:
    assert read_back("Ernst & Young LLP") is None


# --- verified claims ---


@pytest.mark.integration
def test_an_honest_fact_claim_verifies_against_its_archived_source(world) -> None:  # type: ignore[no-untyped-def]
    _, latest = annual_revenue(world[0])
    year = latest.period_end.year
    claim = shown(
        world, f"Alphabet revenue for FY{year} was {ph(latest.fact_id)}.", (latest.fact_id,)
    )
    assert check_claim(claim, ctx(world)) == []


@pytest.mark.integration
def test_a_passage_claim_verifies_against_the_archived_text(world) -> None:  # type: ignore[no-untyped-def]
    store, _ = world
    passage = store.passages_as_of(cik=GOOGL, accession="0001652044-26-000080", as_of=AS_OF)[0]
    claim = shown(world, f"The 8-K reports {ph(passage.passage_id)}.", (passage.passage_id,))
    assert check_claim(claim, ctx(world)) == []


@pytest.mark.integration
def test_a_derived_claim_verifies_through_its_inputs(world, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    store, _ = world
    earlier, latest = annual_revenue(store)
    growth = derive(Method.GROWTH, earlier, latest)
    store.add_derived([growth])
    claim = shown(world, f"Alphabet revenue grew {ph(growth.fact_id)}.", (growth.fact_id,))
    assert check_claim(claim, ctx(world)) == []


@pytest.mark.integration
def test_form4_and_8k_item_facts_verify(world) -> None:  # type: ignore[no-untyped-def]
    store, _ = world
    shares = store.as_of(cik=GOOGL, field="sec:form4:t1:shares", as_of=AS_OF)[0]
    item = store.text_as_of(cik=GOOGL, field="sec:8-K:item", as_of=AS_OF)[0]
    claim = shown(
        world,
        f"An insider sold {ph(shares.fact_id)}; an 8-K reported {ph(item.fact_id)}.",
        (shares.fact_id, item.fact_id),
    )
    assert check_claim(claim, ctx(world)) == []


# --- what the checker catches ---


@pytest.mark.integration
def test_a_span_that_no_longer_matches_its_fact_is_caught(world) -> None:  # type: ignore[no-untyped-def]
    _, latest = annual_revenue(world[0])
    claim = shown(world, f"Revenue was {ph(latest.fact_id)}.", (latest.fact_id,))
    start, end = claim.code_spans[0]
    shown_value = claim.text[start:end].split(" (")[0]
    forged = "$999.9 billion"
    tampered = claim.text[:start] + forged + claim.text[start + len(shown_value) :]
    new_end = end + len(forged) - len(shown_value)
    problems = check_claim(
        replace(claim, text=tampered, code_spans=((start, new_end),)), ctx(world)
    )
    assert any("does not match" in p for p in problems), problems


@pytest.mark.integration
def test_a_fact_whose_archived_source_disagrees_is_caught(world, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    store, archive = world
    _, latest = annual_revenue(store)
    forged = replace(latest, fact_id="f_" + "e" * 16, value=latest.value * 1.07)
    other = FactStore(tmp_path / "forged.duckdb")
    other.add([forged])
    claim = review_claim(
        SubmittedClaim(
            "summary", ClaimType.FACT, f"Revenue was {ph(forged.fact_id)}.", (forged.fact_id,)
        ),
        other.at(AS_OF),
    )
    assert isinstance(claim, RenderedClaim)
    context = CheckContext(
        view=other.at(AS_OF), archive=archive, subject_cik=GOOGL, companies=COMPANIES
    )
    assert any("source" in p for p in check_claim(claim, context))


@pytest.mark.integration
def test_a_named_period_must_be_carried_by_a_cited_fact(world) -> None:  # type: ignore[no-untyped-def]
    _, latest = annual_revenue(world[0])
    wrong_year = latest.period_end.year - 3
    claim = shown(world, f"Revenue in {wrong_year} was {ph(latest.fact_id)}.", (latest.fact_id,))
    problems = check_claim(claim, ctx(world))
    assert any("period" in p for p in problems), problems


@pytest.mark.integration
def test_a_named_quarter_needs_a_quarterly_fact(world) -> None:  # type: ignore[no-untyped-def]
    _, latest = annual_revenue(world[0])
    claim = shown(world, f"Q3 revenue was {ph(latest.fact_id)}.", (latest.fact_id,))
    assert any("period" in p for p in check_claim(claim, ctx(world)))


@pytest.mark.integration
def test_a_named_metric_must_be_carried_by_a_cited_fact(world) -> None:  # type: ignore[no-untyped-def]
    _, latest = annual_revenue(world[0])
    claim = shown(world, f"Net income was {ph(latest.fact_id)}.", (latest.fact_id,))
    problems = check_claim(claim, ctx(world))
    assert any("metric" in p for p in problems), problems


@pytest.mark.integration
def test_another_company_named_or_cited_is_caught(world) -> None:  # type: ignore[no-untyped-def]
    _, latest = annual_revenue(world[0])
    named = shown(world, f"Apple revenue was {ph(latest.fact_id)}.", (latest.fact_id,))
    assert any("company" in p for p in check_claim(named, ctx(world)))
    elsewhere = CheckContext(
        view=world[0].at(AS_OF), archive=world[1], subject_cik=320193, companies=COMPANIES
    )
    plain = shown(world, f"Revenue was {ph(latest.fact_id)}.", (latest.fact_id,))
    assert any("company" in p for p in check_claim(plain, elsewhere))


@pytest.mark.integration
def test_per_share_wording_needs_a_per_share_fact(world) -> None:  # type: ignore[no-untyped-def]
    _, latest = annual_revenue(world[0])
    claim = shown(world, f"Revenue per share was {ph(latest.fact_id)}.", (latest.fact_id,))
    assert any("unit" in p for p in check_claim(claim, ctx(world)))


@pytest.mark.integration
def test_a_passage_carries_the_periods_and_metrics_it_mentions(world) -> None:  # type: ignore[no-untyped-def]
    store, archive = world
    document = b"Revenue in 2025 grew across segments."
    archive.save(document)
    passage = passage_from_span(
        document,
        0,
        len(document),
        cik=GOOGL,
        accession=TEN_K[0],
        form="10-K",
        section="full text",
        public_on=date(2026, 2, 6),
        fetched_at=NOW,
        source_url="https://www.sec.gov/x",
    )
    store.add_passages([passage])
    claim = shown(
        world,
        f"Revenue in 2025 is discussed in {ph(passage.passage_id)}.",
        (passage.passage_id,),
        ClaimType.OPINION,
    )
    assert check_claim(claim, ctx(world)) == []
    other_year = shown(
        world,
        f"Revenue in 2023 is discussed in {ph(passage.passage_id)}.",
        (passage.passage_id,),
        ClaimType.OPINION,
    )
    assert any("period" in p for p in check_claim(other_year, ctx(world)))


@pytest.mark.integration
def test_a_missing_archive_copy_is_caught(world, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    store, _ = world
    _, latest = annual_revenue(store)
    claim = shown(world, f"Revenue was {ph(latest.fact_id)}.", (latest.fact_id,))
    empty = CheckContext(
        view=store.at(AS_OF),
        archive=SourceArchive(tmp_path / "none"),
        subject_cik=GOOGL,
        companies=COMPANIES,
    )
    assert any("source" in p for p in check_claim(claim, empty))


# --- the dossier: row 1c's rate and row 1a's rule ---


@pytest.mark.integration
def test_a_dossier_check_counts_withholds_and_rates(world) -> None:  # type: ignore[no-untyped-def]
    store, _ = world
    _, latest = annual_revenue(store)
    view = ctx(world).view
    good = review_claim(
        SubmittedClaim(
            "summary", ClaimType.FACT, f"Revenue was {ph(latest.fact_id)}.", (latest.fact_id,)
        ),
        view,
    )
    wrong = review_claim(
        SubmittedClaim(
            "summary", ClaimType.FACT, f"Net income was {ph(latest.fact_id)}.", (latest.fact_id,)
        ),
        view,
    )
    typed = review_claim(
        SubmittedClaim("summary", ClaimType.FACT, "Revenue was 402 billion.", (latest.fact_id,)),
        view,
    )
    uncited = review_claim(
        SubmittedClaim("summary", ClaimType.OPINION, "Things look fine.", ()), view
    )
    result = check_dossier([good, wrong, typed, uncited], ctx(world))
    assert result.counted == 3  # the uncited claim is not in row 1c's count
    assert result.verified == 1
    assert result.rate == pytest.approx(1 / 3)
    assert [c.claim_id for c in result.shown] == [good.claim_id]  # type: ignore[union-attr]
    assert len(result.withheld) == 3
    assert result.words_check_version == WORDS_CHECK_VERSION


@pytest.mark.unit
def test_row_1a_fails_a_dossier_that_shows_an_unverified_fact_claim() -> None:
    def rendered(claim_id: str, kind: ClaimType) -> RenderedClaim:
        return RenderedClaim(
            claim_id, "summary", kind, "x", (), ("f_" + "0" * 16,), None, None, *_grades(), "x"
        )

    shown_claims = [rendered("c_1", ClaimType.FACT), rendered("c_2", ClaimType.OPINION)]
    assert row_1a_failures(shown_claims, verified_ids={"c_1"}) == []
    assert row_1a_failures(shown_claims, verified_ids=set()) == ["c_1"]  # the opinion is not a fact


def _grades() -> tuple[object, object, object]:
    from foxcast.claims.scale import Confidence, Credibility, Reliability

    return Reliability.A, Credibility.PROBABLY_TRUE, Confidence.HIGH


@pytest.mark.unit
def test_the_words_check_version_is_a_hash() -> None:
    assert len(WORDS_CHECK_VERSION) == 16
    int(WORDS_CHECK_VERSION, 16)


# --- findings of the 1A.5 review ---


def quarterly_revenue(store: FactStore) -> Fact:
    return next(
        f
        for f in store.as_of(cik=GOOGL, field="us-gaap:Revenues", as_of=AS_OF)
        if f.period_start and (f.period_end - f.period_start).days < 100
    )


@pytest.mark.integration
def test_an_impossible_date_is_a_reason_not_a_crash(world) -> None:  # type: ignore[no-untyped-def]
    _, latest = annual_revenue(world[0])
    claim = shown(world, f"Revenue on 2026-02-31 was {ph(latest.fact_id)}.", (latest.fact_id,))
    assert any("does not exist" in p for p in check_claim(claim, ctx(world)))


@pytest.mark.integration
@pytest.mark.parametrize(
    ("named", "ok"),
    [
        ("Q3 2025", True),
        ("Q4 2025", False),
        ("Q3 2024", False),
        ("third quarter", True),
        ("first quarter", False),
        ("H2 2025", False),
        ("FY2025", False),
    ],
)
def test_the_named_quarter_and_year_must_match_the_fact(world, named: str, ok: bool) -> None:  # type: ignore[no-untyped-def]
    quarter = quarterly_revenue(world[0])  # July to September 2025
    claim = shown(world, f"Revenue in {named} was {ph(quarter.fact_id)}.", (quarter.fact_id,))
    assert (check_claim(claim, ctx(world)) == []) is ok, check_claim(claim, ctx(world))


@pytest.mark.integration
@pytest.mark.parametrize("name", ["Apple Inc.", "apple", "APPLE INC.", "(AAPL)", "AAPL"])
def test_another_company_is_caught_in_any_case_or_punctuation(world, name: str) -> None:  # type: ignore[no-untyped-def]
    _, latest = annual_revenue(world[0])
    store, archive = world
    companies = {**COMPANIES, "Apple Inc.": 320193}
    context = CheckContext(
        view=store.at(AS_OF), archive=archive, subject_cik=GOOGL, companies=companies
    )
    claim = shown(world, f"Unlike {name}, revenue was {ph(latest.fact_id)}.", (latest.fact_id,))
    assert any("company" in p for p in check_claim(claim, context)), name


@pytest.mark.integration
def test_a_ticker_that_is_a_word_counts_only_in_parentheses(world) -> None:  # type: ignore[no-untyped-def]
    _, latest = annual_revenue(world[0])
    store, archive = world
    context = CheckContext(
        view=store.at(AS_OF),
        archive=archive,
        subject_cik=GOOGL,
        companies={"ALL": 899051, "T": 732717},
    )
    plain = shown(world, f"Revenue for ALL segments was {ph(latest.fact_id)}.", (latest.fact_id,))
    assert check_claim(plain, context) == []
    named = shown(world, f"Unlike (T), revenue was {ph(latest.fact_id)}.", (latest.fact_id,))
    assert any("company" in p for p in check_claim(named, context))


@pytest.mark.integration
def test_currency_words_must_match_the_unit(world) -> None:  # type: ignore[no-untyped-def]
    store, _ = world
    shares = store.as_of(cik=GOOGL, field="sec:form4:t1:shares", as_of=AS_OF)[0]
    claim = shown(world, f"An insider sold dollars worth {ph(shares.fact_id)}.", (shares.fact_id,))
    assert any("unit" in p for p in check_claim(claim, ctx(world)))


@pytest.mark.integration
def test_a_passage_cited_but_not_quoted_carries_nothing(world) -> None:  # type: ignore[no-untyped-def]
    store, archive = world
    document = b"Revenue in 2019 grew across segments."
    archive.save(document)
    passage = passage_from_span(
        document,
        0,
        len(document),
        cik=GOOGL,
        accession=TEN_K[0],
        form="10-K",
        section="full text",
        public_on=date(2026, 2, 6),
        fetched_at=NOW,
        source_url="https://www.sec.gov/y",
    )
    store.add_passages([passage])
    claim = shown(
        world, "Revenue in 2019 drove the result.", (passage.passage_id,), ClaimType.OPINION
    )
    problems = check_claim(claim, ctx(world))
    assert any("period" in p for p in problems) and any("metric" in p for p in problems)


@pytest.mark.integration
def test_words_changed_after_rendering_are_caught(world) -> None:  # type: ignore[no-untyped-def]
    _, latest = annual_revenue(world[0])
    claim = shown(world, f"Revenue was {ph(latest.fact_id)}.", (latest.fact_id,))
    edited = replace(
        claim,
        text="Profit" + claim.text[len("Revenue") :],
        code_spans=tuple((s - 1, e - 1) for s, e in claim.code_spans),
    )
    assert any("differ from the template" in p for p in check_claim(edited, ctx(world)))


@pytest.mark.integration
def test_a_corrupt_archived_source_is_a_reason_not_a_crash(world, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    store, _ = world
    _, latest = annual_revenue(store)
    corrupt = SourceArchive(tmp_path / "corrupt")
    corrupt.save(b"{not json")
    forged = replace(
        latest,
        fact_id="f_" + "d" * 16,
        content_sha256=__import__("hashlib").sha256(b"{not json").hexdigest(),
    )
    other = FactStore(tmp_path / "corrupt.duckdb")
    other.add([forged])
    claim = review_claim(
        SubmittedClaim(
            "summary", ClaimType.FACT, f"Revenue was {ph(forged.fact_id)}.", (forged.fact_id,)
        ),
        other.at(AS_OF),
    )
    assert isinstance(claim, RenderedClaim)
    context = CheckContext(
        view=other.at(AS_OF), archive=corrupt, subject_cik=GOOGL, companies=COMPANIES
    )
    assert any("cannot be read" in p for p in check_claim(claim, context))
