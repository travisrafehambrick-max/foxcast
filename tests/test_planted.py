"""Planted false claims and the false-accept rate beside row 1c (Stage 1A.5)."""

from dataclasses import replace

import pytest

from foxcast.claims.checker import CheckContext, check_claim
from foxcast.claims.planted import (
    PLANTED_SEED,
    PlantedCategory,
    plant,
    run_planted,
)
from foxcast.claims.render import ClaimType, RenderedClaim, SubmittedClaim, review_claim
from foxcast.facts.record import Fact
from foxcast.store.archive import SourceArchive
from foxcast.store.pit import FactStore

from .test_checker import AS_OF, COMPANIES, GOOGL, annual_revenue, ph

APPLE = 320193


def verified_claims(
    world: tuple[FactStore, SourceArchive],
) -> tuple[list[RenderedClaim], list[Fact]]:
    store, archive = world
    earlier, latest = annual_revenue(store)
    apple = replace(latest, fact_id="f_" + "a" * 15 + "1", cik=APPLE)
    store.add([apple])
    view = store.at(AS_OF)
    passage = store.passages_as_of(cik=GOOGL, accession="0001652044-26-000080", as_of=AS_OF)[0]
    submissions = [
        SubmittedClaim(
            "summary",
            ClaimType.FACT,
            f"Revenue for FY{latest.period_end.year} was {ph(latest.fact_id)}.",
            (latest.fact_id,),
        ),
        SubmittedClaim(
            "summary",
            ClaimType.FACT,
            f"Revenue for FY{earlier.period_end.year} was {ph(earlier.fact_id)}.",
            (earlier.fact_id,),
        ),
        SubmittedClaim(
            "summary",
            ClaimType.FACT,
            f"The 8-K says {ph(passage.passage_id)}.",
            (passage.passage_id,),
        ),
    ]
    context = CheckContext(view=view, archive=archive, subject_cik=GOOGL, companies=COMPANIES)
    claims = []
    for submission in submissions:
        rendered = review_claim(submission, view)
        assert isinstance(rendered, RenderedClaim), rendered
        assert check_claim(rendered, context) == [], rendered
        claims.append(rendered)
    candidates = [
        *store.as_of(cik=GOOGL, field="us-gaap:Revenues", as_of=AS_OF),
        *store.as_of(cik=GOOGL, field="us-gaap:NetIncomeLoss", as_of=AS_OF),
        apple,
    ]
    return claims, candidates


def context_for(world: tuple[FactStore, SourceArchive]) -> CheckContext:
    store, archive = world
    return CheckContext(
        view=store.at(AS_OF), archive=archive, subject_cik=GOOGL, companies=COMPANIES
    )


@pytest.mark.integration
def test_every_category_is_planted_and_every_planted_claim_is_caught(world) -> None:  # type: ignore[no-untyped-def]
    claims, candidates = verified_claims(world)
    planted = plant(claims, candidates, context_for(world).view, seed=PLANTED_SEED, per_category=25)
    assert {p.category for p in planted} == set(PlantedCategory)
    report = run_planted(planted, context_for(world))
    for category in PlantedCategory:
        row = report.by_category[category]
        assert row.planted > 0, category
        assert row.accepted == 0, (category, row)
    assert report.overall.accepted == 0
    assert report.overall.false_accept_rate == 0.0
    assert 0 < report.overall.upper_bound < 1


@pytest.mark.integration
def test_planting_is_reproducible_from_the_seed(world) -> None:  # type: ignore[no-untyped-def]
    claims, candidates = verified_claims(world)
    view = context_for(world).view
    first = plant(claims, candidates, view, seed=PLANTED_SEED, per_category=2)
    second = plant(claims, candidates, view, seed=PLANTED_SEED, per_category=2)
    assert [(p.category, p.source_claim_id, p.description) for p in first] == [
        (p.category, p.source_claim_id, p.description) for p in second
    ]


@pytest.mark.integration
def test_a_shortfall_is_reported_not_topped_up(world) -> None:  # type: ignore[no-untyped-def]
    claims, candidates = verified_claims(world)
    planted = plant(claims, candidates, context_for(world).view, seed=PLANTED_SEED, per_category=25)
    report = run_planted(planted, context_for(world))
    quotes = report.by_category[PlantedCategory.ALTERED_QUOTE]
    assert quotes.planted == 1  # only one verified claim quotes a passage
    assert quotes.shortfall == 24


@pytest.mark.integration
def test_a_checker_that_accepts_everything_is_measured_as_such(world) -> None:  # type: ignore[no-untyped-def]
    claims, candidates = verified_claims(world)
    planted = plant(claims, candidates, context_for(world).view, seed=PLANTED_SEED, per_category=25)
    report = run_planted(planted, context_for(world), check=lambda claim, context: [])
    assert report.overall.accepted == report.overall.planted > 0
    assert report.overall.false_accept_rate == 1.0


@pytest.mark.unit
def test_the_seed_is_the_one_the_scorecard_fixes() -> None:
    assert PLANTED_SEED == 2026111103


@pytest.mark.unit
@pytest.mark.parametrize(
    ("span", "expected"),
    [
        ("$402.8 billion (Revenues, x)", "$402.8 million (Revenues, x)"),
        ("$2.500 trillion (Revenues, x)", "$2.500 billion (Revenues, x)"),
        ("+15.09% (growth in Revenues, x)", "+0.1509 (growth in Revenues, x)"),
        ("19,157 shares (owned_after, x)", "19,157,000 shares (owned_after, x)"),
        ("Ernst & Young LLP (AuditorName, x)", None),
    ],
)
def test_unit_swaps_change_the_scale_by_a_hundred_or_a_thousand(
    span: str, expected: str | None
) -> None:
    from foxcast.claims.planted import _wrong_unit

    assert _wrong_unit(span) == expected


@pytest.mark.unit
def test_number_and_quote_edits_leave_spans_without_them_alone() -> None:
    from foxcast.claims.planted import _altered_number, _altered_quote

    assert _altered_number("Ernst & Young LLP (AuditorName, x)") is None
    assert _altered_number("$402.8 billion (Revenues, x)") == "$431.0 billion (Revenues, x)"
    assert _altered_quote("(a b c)") is None
