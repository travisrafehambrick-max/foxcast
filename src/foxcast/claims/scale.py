"""The likelihood scale, source grades and confidence (brief principles 12, 13, 15).

All three are set by code, never by the model:

- **Likelihood** turns a stated probability into one of seven fixed words, so the model
  cannot pick a softer or stronger word than its own number supports (ICD 203 bands; a
  probability on a boundary takes the higher band).
- **Grades** follow the NATO/Admiralty system: how reliable the source is (A to F) and how
  credible this item is (1 to 6), set independently, from the kind of source.
- **Confidence** in the evidence is separate from likelihood and comes from the weakest
  grades among the cited items.

The starting grades for SEC sources are a decision recorded in the Stage 1 plan (1A.4):
regulatory filings are reliable (A) and their figures probably true (2) until another
source confirms them; management's own narrative is usually reliable (B) and possibly
true (3). News grading in task 1B.1 adds kinds to this table.
"""

import math
from collections.abc import Iterable
from enum import StrEnum
from typing import Final


class Reliability(StrEnum):
    A = "A"  # completely reliable
    B = "B"  # usually reliable
    C = "C"  # fairly reliable
    D = "D"  # not usually reliable
    E = "E"  # unreliable
    F = "F"  # reliability cannot be judged


class Credibility(StrEnum):
    CONFIRMED = "1"  # confirmed by other independent sources
    PROBABLY_TRUE = "2"
    POSSIBLY_TRUE = "3"
    DOUBTFUL = "4"
    IMPROBABLE = "5"
    CANNOT_BE_JUDGED = "6"


class Confidence(StrEnum):
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"


class SourceKind(StrEnum):
    SEC_XBRL = "sec_xbrl"  # a figure from XBRL company facts
    SEC_FORM4 = "sec_form4"  # an insider trade
    SEC_INDEX = "sec_index"  # the filing index (8-K item codes)
    SEC_FILING_TEXT = "sec_filing_text"  # a passage of a filing's narrative


GRADES: Final[dict[SourceKind, tuple[Reliability, Credibility]]] = {
    SourceKind.SEC_XBRL: (Reliability.A, Credibility.PROBABLY_TRUE),
    SourceKind.SEC_FORM4: (Reliability.A, Credibility.PROBABLY_TRUE),
    SourceKind.SEC_INDEX: (Reliability.A, Credibility.PROBABLY_TRUE),
    SourceKind.SEC_FILING_TEXT: (Reliability.B, Credibility.POSSIBLY_TRUE),
}

# (lower bound, word), highest first. A probability on a boundary takes the higher band.
LIKELIHOOD_BANDS: Final = (
    (0.95, "almost certain"),
    (0.80, "very likely"),
    (0.55, "likely"),
    (0.45, "roughly even"),
    (0.20, "unlikely"),
    (0.05, "very unlikely"),
    (0.0, "almost no chance"),
)
RELIABILITY_ORDER: Final = tuple(Reliability)
CREDIBILITY_ORDER: Final = tuple(Credibility)


def likelihood_word(probability: float) -> str:
    """The fixed word for a probability between 0 and 1."""
    if not (math.isfinite(probability) and 0.0 <= probability <= 1.0):
        raise ValueError(f"a probability must be between 0 and 1, got {probability!r}")
    return next(word for bound, word in LIKELIHOOD_BANDS if probability >= bound)


def grade_of(kind: SourceKind) -> tuple[Reliability, Credibility]:
    return GRADES[kind]


def weakest(
    grades: Iterable[tuple[Reliability, Credibility]],
) -> tuple[Reliability, Credibility] | None:
    """The lowest reliability and the lowest credibility among the grades, if any."""
    items = list(grades)
    if not items:
        return None
    reliability = max((r for r, _ in items), key=RELIABILITY_ORDER.index)
    credibility = max((c for _, c in items), key=CREDIBILITY_ORDER.index)
    return reliability, credibility


def confidence_from(grades: Iterable[tuple[Reliability, Credibility]]) -> Confidence:
    """High needs every source at A or B and every item at 1 or 2; moderate allows C and 3."""
    worst = weakest(grades)
    if worst is None:
        return Confidence.LOW
    reliability, credibility = worst
    r, c = RELIABILITY_ORDER.index(reliability), CREDIBILITY_ORDER.index(credibility)
    if r <= RELIABILITY_ORDER.index(Reliability.B) and c <= 1:
        return Confidence.HIGH
    if r <= RELIABILITY_ORDER.index(Reliability.C) and c <= 2:
        return Confidence.MODERATE
    return Confidence.LOW
