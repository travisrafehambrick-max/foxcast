"""Rendering claims: code inserts every number and quote (Stage 1A.4; row 1b)."""

import unicodedata
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from foxcast.claims.numbers_rule import ALLOWED_TOKENS, PLACEHOLDER
from foxcast.claims.render import (
    ClaimType,
    RenderedClaim,
    SubmittedClaim,
    WithheldClaim,
    format_value,
    review_claim,
)
from foxcast.claims.scale import Confidence, Credibility, Reliability
from foxcast.facts.derived import Method, derive
from foxcast.facts.record import Fact, passage_from_span, text_fact
from foxcast.store.pit import FactStore, FactView

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)
AS_OF = date(2026, 11, 11)
ACCESSION = "0001652044-26-000018"
DOCUMENT = "Item 1A. Risk Factors\n\nOur business depends on advertising revenue."


def number(value: float, fact_id: str, *, field: str = "us-gaap:Revenues", unit: str = "USD",
           year: int = 2025, start: bool = True) -> Fact:  # fmt: skip
    return Fact(
        fact_id=fact_id,
        cik=1652044,
        field=field,
        unit=unit,
        period_start=date(year, 1, 1) if start else None,
        period_end=date(year, 12, 31),
        value=value,
        public_on=date(year + 1, 2, 6),
        fetched_at=NOW,
        source_url="https://data.sec.gov/x",
        content_sha256="a" * 64,
        accession=ACCESSION,
    )


REV_2024 = number(350_018_000_000.0, "f_" + "1" * 16, year=2024)
REV_2025 = number(402_836_000_000.0, "f_" + "2" * 16)
EPS = number(10.91, "f_" + "3" * 16, field="us-gaap:EarningsPerShareDiluted", unit="USD/shares")
SHARES = number(19157.0, "f_" + "4" * 16, field="sec:form4:t1:owned_after", unit="shares")
AUDITOR = text_fact(
    cik=1652044, field="dei:AuditorName", value="Ernst & Young LLP", period_end=date(2025, 12, 31),
    public_on=date(2026, 2, 6), fetched_at=NOW, source_url="https://data.sec.gov/x",
    content_sha256="a" * 64, accession=ACCESSION,
)  # fmt: skip
PASSAGE = passage_from_span(
    DOCUMENT.encode(), 23, len(DOCUMENT), cik=1652044, accession=ACCESSION, form="10-K",
    section="full text", public_on=date(2026, 2, 6), fetched_at=NOW,
    source_url="https://www.sec.gov/x",
)  # fmt: skip
GROWTH = derive(Method.GROWTH, REV_2024, REV_2025)


@pytest.fixture
def view(tmp_path: Path) -> FactView:
    store = FactStore(tmp_path / "facts.duckdb")
    store.add([REV_2024, REV_2025, EPS, SHARES])
    store.add_text([AUDITOR])
    store.add_passages([PASSAGE])
    store.add_derived([GROWTH])
    return store.at(AS_OF)


def claim(text: str, cites: tuple[str, ...], kind: ClaimType = ClaimType.FACT,
          probability: float | None = None) -> SubmittedClaim:  # fmt: skip
    return SubmittedClaim(
        section="summary", type=kind, text=text, cites=cites, probability=probability
    )


def ph(fact_id: str) -> str:
    return "{" + fact_id + "}"


# --- formatting values (scorecard: at least three significant figures) ---


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (402_836_000_000.0, "USD", "$402.8 billion"),
        (-1_234_000_000.0, "USD", "-$1.234 billion"),
        (2_500_000_000_000.0, "USD", "$2.500 trillion"),
        (45_600_000.0, "USD", "$45.60 million"),
        (950_000.0, "USD", "$950,000"),
        (10.91, "USD/shares", "$10.91 per share"),
        (0.5, "USD/shares", "$0.5000 per share"),
        (999_999_999.9, "USD", "$1.000 billion"),
        (999_999_999_999.9, "USD", "$1.000 trillion"),
        (0.0004, "USD", "$0.0004000"),
        (-0.0, "USD", "$0"),
        (1.234e-05, "x", "0.00001234 x"),
        (19157.0, "shares", "19,157 shares"),
        (1_234_567_890.0, "shares", "1.235 billion shares"),
        (7.0, "EUR", "7 EUR"),
        (7.25, "EUR", "7.250 EUR"),
    ],
)
def test_values_are_rendered_to_at_least_three_significant_figures(
    value: float, unit: str, expected: str
) -> None:
    assert format_value(value, unit) == expected


@pytest.mark.unit
def test_derived_values_take_their_scale_from_the_method(view: FactView) -> None:
    rendered = review_claim(claim(f"Revenue grew {ph(GROWTH.fact_id)}.", (GROWTH.fact_id,)), view)
    assert isinstance(rendered, RenderedClaim), rendered
    assert "+15.09%" in rendered.text
    assert "year-on-year" in rendered.text


# --- rendering ---


@pytest.mark.unit
def test_a_fact_claim_is_rendered_with_metric_period_and_grades(view: FactView) -> None:
    rendered = review_claim(claim(f"Revenue in FY2025 was {ph(REV_2025.fact_id)}.",
                                  (REV_2025.fact_id,)), view)  # fmt: skip
    assert isinstance(rendered, RenderedClaim), rendered
    assert rendered.text == (
        "Revenue in FY2025 was $402.8 billion (Revenues, 2025-01-01 to 2025-12-31)."
    )
    assert rendered.reliability is Reliability.A
    assert rendered.credibility is Credibility.PROBABLY_TRUE
    assert rendered.confidence is Confidence.HIGH
    assert rendered.likelihood is None
    assert rendered.claim_id.startswith("c_")


@pytest.mark.unit
def test_passages_and_text_values_are_inserted_by_code(view: FactView) -> None:
    text = f"The auditor is {ph(AUDITOR.fact_id)}; the 10-K warns {ph(PASSAGE.passage_id)}."
    rendered = review_claim(claim(text, (AUDITOR.fact_id, PASSAGE.passage_id)), view)
    assert isinstance(rendered, RenderedClaim), rendered
    quote = chr(0x201C) + "Our business depends on advertising revenue." + chr(0x201D)
    assert quote in rendered.text
    assert "Ernst & Young LLP" in rendered.text
    assert rendered.reliability is Reliability.B  # the narrative passage is the weaker source
    assert rendered.confidence is Confidence.MODERATE


@pytest.mark.unit
def test_estimates_get_the_likelihood_word_from_code(view: FactView) -> None:
    rendered = review_claim(
        claim(f"Revenue growth continues {ph(GROWTH.fact_id)}.", (GROWTH.fact_id,),
              ClaimType.ESTIMATE, probability=0.62),
        view,
    )  # fmt: skip
    assert isinstance(rendered, RenderedClaim), rendered
    assert rendered.likelihood == "likely"
    assert rendered.probability == 0.62


@pytest.mark.unit
def test_an_opinion_may_cite_facts_without_placing_them(view: FactView) -> None:
    rendered = review_claim(
        claim("Advertising dependence is the main risk.", (PASSAGE.passage_id,), ClaimType.OPINION),
        view,
    )
    assert isinstance(rendered, RenderedClaim), rendered
    assert rendered.text == "Advertising dependence is the main risk."


@pytest.mark.unit
def test_claim_ids_are_stable(view: FactView) -> None:
    c = claim(f"Revenue was {ph(REV_2025.fact_id)}.", (REV_2025.fact_id,))
    first, second = review_claim(c, view), review_claim(c, view)
    assert isinstance(first, RenderedClaim)
    assert isinstance(second, RenderedClaim)
    assert first.claim_id == second.claim_id


# --- withholding ---


@pytest.mark.unit
@pytest.mark.parametrize(
    ("submitted", "why"),
    [
        (claim("Revenue was 402 billion.", (REV_2025.fact_id,)), "number"),
        (claim("Revenue rose.", ()), "cites nothing"),
        (claim(f"Revenue was {ph('f_' + '9' * 16)}.", ("f_" + "9" * 16,)), "not in the record"),
        (claim(f"Revenue was {ph(REV_2025.fact_id)}.", ()), "not cited"),
        (claim("Revenue rose.", (REV_2025.fact_id, REV_2025.fact_id)), "twice"),
        (claim("Revenue rose.", ("not-an-id",)), "not a fact or passage ID"),
        (claim("Growth continues.", (GROWTH.fact_id,), ClaimType.ESTIMATE), "probability"),
        (claim("Revenue rose.", (REV_2025.fact_id,), ClaimType.FACT, probability=0.9),
         "probability"),
        (claim("Growth continues.", (GROWTH.fact_id,), ClaimType.FORECAST, probability=1.5),
         "probability"),
        (claim("Revenue rose.", tuple(f"f_{i:016x}" for i in range(20))), "too many"),
    ],
)  # fmt: skip
def test_bad_claims_are_withheld_with_reasons(
    view: FactView, submitted: SubmittedClaim, why: str
) -> None:
    result = review_claim(submitted, view)
    assert isinstance(result, WithheldClaim), result
    assert any(why in reason for reason in result.reasons), result.reasons


@pytest.mark.unit
def test_a_fact_from_after_the_as_of_date_cannot_be_cited(tmp_path: Path) -> None:
    store = FactStore(tmp_path / "facts.duckdb")
    store.add([REV_2025])
    early = store.at(date(2026, 2, 5))  # the day before the 10-K was public
    result = review_claim(claim(f"Revenue was {ph(REV_2025.fact_id)}.", (REV_2025.fact_id,)), early)
    assert isinstance(result, WithheldClaim)
    assert any("not in the record" in r for r in result.reasons)


@pytest.mark.unit
def test_a_quantity_word_passes_only_with_a_derived_fact_that_shows_it(tmp_path: Path) -> None:
    store = FactStore(tmp_path / "facts.duckdb")
    small = number(100.0, "f_" + "8" * 16, year=2024)
    big = number(200.0, "f_" + "9" * 16)
    doubling = derive(Method.GROWTH, small, big)
    store.add([REV_2024, REV_2025, small, big])
    store.add_derived([GROWTH, doubling])
    view = store.at(AS_OF)
    plain = review_claim(
        claim(f"Revenue doubled {ph(REV_2025.fact_id)}.", (REV_2025.fact_id,)), view
    )
    assert isinstance(plain, WithheldClaim)
    modest = review_claim(claim(f"Revenue doubled {ph(GROWTH.fact_id)}.", (GROWTH.fact_id,)), view)
    assert isinstance(modest, WithheldClaim)  # +15% is not a doubling
    shown = review_claim(
        claim(f"Revenue doubled {ph(doubling.fact_id)}.", (doubling.fact_id,)), view
    )
    assert isinstance(shown, RenderedClaim), shown
    assert "+100.0%" in shown.text


@pytest.mark.unit
def test_a_cited_but_unplaced_derived_fact_does_not_license_a_quantity_word(view: FactView) -> None:
    result = review_claim(claim("Revenue doubled.", (GROWTH.fact_id,)), view)
    assert isinstance(result, WithheldClaim)


@pytest.mark.unit
def test_non_finite_values_cannot_be_rendered() -> None:
    with pytest.raises(ValueError):
        format_value(float("inf"), "USD")


@pytest.mark.unit
def test_an_item_from_an_unknown_kind_of_source_is_withheld_not_graded(tmp_path: Path) -> None:
    odd = number(5.0, "f_" + "a" * 16, field="newsource:Thing")
    store = FactStore(tmp_path / "facts.duckdb")
    store.add([odd])
    result = review_claim(claim(f"It was {ph(odd.fact_id)}.", (odd.fact_id,)), store.at(AS_OF))
    assert isinstance(result, WithheldClaim)
    assert any("no grade" in r for r in result.reasons)


@pytest.mark.unit
def test_a_boolean_is_not_a_probability(view: FactView) -> None:
    result = review_claim(
        claim(f"Growth continues {ph(GROWTH.fact_id)}.", (GROWTH.fact_id,), ClaimType.ESTIMATE,
              probability=True),  # type: ignore[arg-type]
        view,
    )  # fmt: skip
    assert isinstance(result, WithheldClaim)


@pytest.mark.unit
def test_a_passage_is_shown_on_one_line(tmp_path: Path) -> None:
    document = "Risk one." + chr(10) + chr(10) + "Risk two."
    passage = passage_from_span(
        document.encode(), 0, len(document), cik=1652044, accession=ACCESSION, form="10-K",
        section="full text", public_on=date(2026, 2, 6), fetched_at=NOW,
        source_url="https://www.sec.gov/x",
    )  # fmt: skip
    store = FactStore(tmp_path / "facts.duckdb")
    store.add_passages([passage])
    result = review_claim(claim(f"It warns {ph(passage.passage_id)}.", (passage.passage_id,)),
                          store.at(AS_OF))  # fmt: skip
    assert isinstance(result, RenderedClaim), result
    assert chr(0x201C) + "Risk one. Risk two." + chr(0x201D) in result.text


# --- row 1b: no model-written number or quote can reach a dossier ---


def outside_code(rendered: RenderedClaim) -> str:
    """The rendered text with every code-inserted span blanked out."""
    chars = list(rendered.text)
    for start, end in rendered.code_spans:
        for i in range(start, end):
            chars[i] = " "
    return "".join(chars)


FORBIDDEN_QUOTES = {chr(c) for c in (0x22, 0x201C, 0x201D, 0xAB, 0xBB)}


def assert_model_wrote_no_number_or_quote(rendered: RenderedClaim) -> None:
    model_part = ALLOWED_TOKENS.sub(" ", outside_code(rendered))
    assert not any(unicodedata.category(c) in {"Nd", "Nl", "No"} for c in model_part), rendered
    assert not any(c in FORBIDDEN_QUOTES for c in model_part), rendered


IDS = [REV_2024.fact_id, REV_2025.fact_id, EPS.fact_id, AUDITOR.fact_id, PASSAGE.passage_id,
       GROWTH.fact_id]  # fmt: skip
pieces = st.one_of(
    st.text(max_size=40),
    st.sampled_from(IDS).map(ph),
    st.sampled_from(
        [
            "Q3",
            "FY2025",
            "10-K",
            "S&P 500",
            "October 28",
            "doubled",
            "402",
            "$1.2",
            chr(0x201C),
            '"',
            "12%",
            "{",
            "twelve",
        ]
    ),
)


@settings(
    max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(st.lists(pieces, max_size=8), st.lists(st.sampled_from(IDS), max_size=4, unique=True),
       st.sampled_from(list(ClaimType)))  # fmt: skip
def test_no_model_written_number_or_quote_survives_rendering(
    view: FactView, parts: list[str], cites: list[str], kind: ClaimType
) -> None:
    text = "".join(parts)
    placed = PLACEHOLDER.findall(text)
    probability = 0.5 if kind in (ClaimType.ESTIMATE, ClaimType.FORECAST) else None
    result = review_claim(
        claim(text, tuple(dict.fromkeys([*placed, *cites])), kind, probability), view
    )
    if isinstance(result, RenderedClaim):
        assert_model_wrote_no_number_or_quote(result)
        assert result.text.count("{") == 0
    else:
        assert result.reasons


@pytest.mark.unit
def test_code_spans_cover_exactly_the_inserted_values(view: FactView) -> None:
    rendered = review_claim(claim(f"A {ph(REV_2025.fact_id)} b {ph(EPS.fact_id)}.",
                                  (REV_2025.fact_id, EPS.fact_id)), view)  # fmt: skip
    assert isinstance(rendered, RenderedClaim)
    spans = [rendered.text[s:e] for s, e in rendered.code_spans]
    assert spans == [
        "$402.8 billion (Revenues, 2025-01-01 to 2025-12-31)",
        "$10.91 per share (EarningsPerShareDiluted, 2025-01-01 to 2025-12-31)",
    ]
    assert replace(rendered, code_spans=()).text == rendered.text


@pytest.mark.unit
def test_any_character_in_a_submission_still_gets_an_id_and_a_reason(view: FactView) -> None:
    odd = claim("Revenue" + chr(0x1F) + "rose.", (REV_2025.fact_id,))
    result = review_claim(odd, view)
    assert isinstance(result, WithheldClaim)
    assert result.claim_id.startswith("c_")
    assert any("characters" in r for r in result.reasons)


@pytest.mark.unit
def test_changes_in_margins_and_in_dollars_render_with_their_units(tmp_path: Path) -> None:
    ni_2024 = number(70_000_000_000.0, "f_" + "5" * 16, field="us-gaap:NetIncomeLoss", year=2024)
    ni_2025 = number(100_000_000_000.0, "f_" + "6" * 16, field="us-gaap:NetIncomeLoss")
    margin_2024 = derive(Method.RATIO, ni_2024, REV_2024)
    margin_2025 = derive(Method.RATIO, ni_2025, REV_2025)
    margin_change = derive(Method.DIFFERENCE, margin_2024, margin_2025)
    revenue_change = derive(Method.DIFFERENCE, REV_2024, REV_2025)
    store = FactStore(tmp_path / "facts.duckdb")
    store.add([REV_2024, REV_2025, ni_2024, ni_2025])
    store.add_derived([margin_2024, margin_2025, margin_change, revenue_change])
    view = store.at(AS_OF)
    text = f"Margin moved {ph(margin_change.fact_id)}; revenue moved {ph(revenue_change.fact_id)}."
    rendered = review_claim(claim(text, (margin_change.fact_id, revenue_change.fact_id)), view)
    assert isinstance(rendered, RenderedClaim), rendered
    assert "percentage points (difference in NetIncomeLoss/Revenues, year-on-year" in rendered.text
    assert "+$52.82 billion (difference in Revenues, year-on-year" in rendered.text
    margin = review_claim(
        claim(f"Margin was {ph(margin_2025.fact_id)}.", (margin_2025.fact_id,)), view
    )
    assert isinstance(margin, RenderedClaim)
    assert "24.82% (ratio in NetIncomeLoss/Revenues, 2025-01-01 to 2025-12-31)" in margin.text


@pytest.mark.unit
def test_an_undated_text_value_says_so(tmp_path: Path) -> None:
    undated = replace(AUDITOR, fact_id="f_" + "7" * 16, period_end=None)
    store = FactStore(tmp_path / "facts.duckdb")
    store.add_text([undated])
    rendered = review_claim(
        claim(f"Audited by {ph(undated.fact_id)}.", (undated.fact_id,)), store.at(AS_OF)
    )
    assert isinstance(rendered, RenderedClaim), rendered
    assert "Ernst & Young LLP (AuditorName, undated)" in rendered.text
