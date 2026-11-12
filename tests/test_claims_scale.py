"""The likelihood scale, source grades and confidence (brief principles 12, 13, 15)."""

import pytest

from foxcast.claims.scale import (
    Confidence,
    Credibility,
    Reliability,
    SourceKind,
    confidence_from,
    grade_of,
    likelihood_word,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("probability", "word"),
    [
        (0.0, "almost no chance"),
        (0.0499, "almost no chance"),
        (0.05, "very unlikely"),  # a boundary takes the higher band
        (0.19, "very unlikely"),
        (0.20, "unlikely"),
        (0.45, "roughly even"),
        (0.5499, "roughly even"),
        (0.55, "likely"),
        (0.80, "very likely"),
        (0.95, "almost certain"),
        (1.0, "almost certain"),
    ],
)
def test_probabilities_map_to_the_fixed_seven_bands(probability: float, word: str) -> None:
    assert likelihood_word(probability) == word


@pytest.mark.unit
@pytest.mark.parametrize("bad", [-0.01, 1.01, float("nan"), float("inf")])
def test_a_probability_outside_zero_to_one_is_refused(bad: float) -> None:
    with pytest.raises(ValueError):
        likelihood_word(bad)


@pytest.mark.unit
def test_every_source_kind_has_a_grade_pair() -> None:
    for kind in SourceKind:
        reliability, credibility = grade_of(kind)
        assert isinstance(reliability, Reliability)
        assert isinstance(credibility, Credibility)


@pytest.mark.unit
def test_filings_grade_higher_than_management_narrative() -> None:
    assert grade_of(SourceKind.SEC_XBRL) == (Reliability.A, Credibility.PROBABLY_TRUE)
    assert grade_of(SourceKind.SEC_FORM4) == (Reliability.A, Credibility.PROBABLY_TRUE)
    assert grade_of(SourceKind.SEC_FILING_TEXT) == (Reliability.B, Credibility.POSSIBLY_TRUE)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("grades", "expected"),
    [
        ([(Reliability.A, Credibility.CONFIRMED)], Confidence.HIGH),
        ([(Reliability.A, Credibility.PROBABLY_TRUE)], Confidence.HIGH),
        ([(Reliability.B, Credibility.POSSIBLY_TRUE)], Confidence.MODERATE),
        ([(Reliability.A, Credibility.PROBABLY_TRUE), (Reliability.D, Credibility.DOUBTFUL)],
         Confidence.LOW),  # the weakest source sets the confidence
        ([(Reliability.F, Credibility.CANNOT_BE_JUDGED)], Confidence.LOW),
        ([], Confidence.LOW),  # nothing cited, nothing to be confident in
    ],
)  # fmt: skip
def test_confidence_comes_from_the_weakest_cited_source(
    grades: list[tuple[Reliability, Credibility]], expected: Confidence
) -> None:
    assert confidence_from(grades) is expected
