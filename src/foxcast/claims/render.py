"""Reviewing and rendering claims (Stage 1A.4; brief changes AV, BC, R; row 1b).

The model submits a :class:`SubmittedClaim`: a type, its own words as a template with
placeholders, the IDs it cites and, for estimates and forecasts, a probability. Code then:

1. checks the citations: each is a fact or passage ID, listed once, public on the as-of
   date of the view, and every placeholder is among them;
2. checks the model's words with the numbers rule (:mod:`foxcast.claims.numbers_rule`);
3. fills each placeholder from the record: a number with its metric, period and unit, a
   text value, or an archived passage in quotation marks (change AV);
4. sets the likelihood word from the probability, the two source grades from the cited
   items, and the confidence from the grades.

Anything wrong withholds the whole claim, with reasons; a withheld claim is listed in the
dossier but never shown as a claim. ``code_spans`` marks every span code inserted, so a
test can show that no number or quote outside them came from the model.
"""

import json
import math
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

from foxcast.claims.numbers_rule import PLACEHOLDER, check_model_text
from foxcast.claims.scale import (
    Confidence,
    Credibility,
    Reliability,
    SourceKind,
    confidence_from,
    grade_of,
    likelihood_word,
    weakest,
)
from foxcast.facts.derived import Basis, DerivedFact, Method, UntraceableError, trace
from foxcast.facts.ids import CLAIM_PREFIX, canonical_id
from foxcast.facts.record import Fact, Passage, TextFact, has_hidden_characters
from foxcast.store.pit import FactView

MAX_CITES: Final = 12
MAX_SECTION_CHARS: Final = 60
OPEN_QUOTE: Final = chr(0x201C)
CLOSE_QUOTE: Final = chr(0x201D)
SCALES: Final = ((1e12, "trillion"), (1e9, "billion"), (1e6, "million"))
SIGNIFICANT: Final = 4  # at least three significant figures (scorecard row 1c); we show four


class ClaimType(StrEnum):
    FACT = "fact"
    ESTIMATE = "estimate"
    OPINION = "opinion"
    FORECAST = "forecast"


NEEDS_PROBABILITY: Final = frozenset({ClaimType.ESTIMATE, ClaimType.FORECAST})


@dataclass(frozen=True)
class SubmittedClaim:
    section: str
    type: ClaimType
    text: str
    cites: tuple[str, ...]
    probability: float | None = None


@dataclass(frozen=True)
class RenderedClaim:
    claim_id: str
    section: str
    type: ClaimType
    text: str
    code_spans: tuple[tuple[int, int], ...]
    cites: tuple[str, ...]
    probability: float | None
    likelihood: str | None
    reliability: Reliability
    credibility: Credibility
    confidence: Confidence
    model_text: str


@dataclass(frozen=True)
class WithheldClaim:
    claim_id: str
    section: str
    type: ClaimType
    model_text: str
    cites: tuple[str, ...]
    reasons: tuple[str, ...]


Cited = Fact | TextFact | DerivedFact | Passage


# --- formatting ---


def _decimals(value: float, minimum: int = 0) -> int:
    """Decimals that show ``SIGNIFICANT`` figures of a positive value.

    Taken from the value rounded to those figures, so 0.99999 counts as 1.000, not 0.9999.
    """
    rounded = float(f"{value:.{SIGNIFICANT - 1}e}")
    return max(minimum, SIGNIFICANT - 1 - math.floor(math.log10(rounded)))


def _significant(value: float) -> str:
    """A non-negative value with at least ``SIGNIFICANT`` figures; whole numbers in full."""
    if value == 0:
        return "0"
    if value == int(value) and value < 1e15:
        return f"{int(value):,}"
    return f"{value:,.{_decimals(value)}f}"


def _with_scale(magnitude: float) -> str:
    """Round first, then choose the scale, so 999,999,999.9 reads 1.000 billion."""
    chosen = None
    for index, (size, _) in enumerate(SCALES):
        if magnitude >= size:
            chosen = index
            break
    if chosen is None:
        return _significant(magnitude)
    while True:
        size, word = SCALES[chosen]
        scaled = magnitude / size
        text = f"{scaled:,.{_decimals(scaled)}f}"
        if float(text.replace(",", "")) < 1000 or chosen == 0:
            return f"{text} {word}"
        chosen -= 1  # SCALES runs largest first, so one step up the scale


def format_value(value: float, unit: str) -> str:
    """A fact's value in words a reader expects, e.g. ``$402.8 billion``."""
    if not math.isfinite(value):
        raise ValueError(f"cannot render a non-finite value: {value!r}")
    sign = "-" if value < 0 else ""
    digits = _with_scale(abs(value))
    if digits == "0":
        sign = ""
    if unit == "USD":
        return f"{sign}${digits}"
    if unit == "USD/shares":
        return f"{sign}${digits} per share"
    if unit == "shares":
        return f"{sign}{digits} shares"
    return f"{sign}{digits} {unit}"


def _signed(value: float) -> str:
    return "+" if value > 0 else "-" if value < 0 else ""


def _points(value: float) -> str:
    """A magnitude in percent or percentage points, with at least one decimal."""
    magnitude = abs(value)
    if magnitude == 0:
        return "0.0"
    return f"{magnitude:,.{_decimals(magnitude, minimum=1)}f}"


def _percent(value: float, *, signed: bool) -> str:
    if not math.isfinite(value):
        raise ValueError(f"cannot render a non-finite value: {value!r}")
    sign = _signed(value) if signed else ("-" if value < 0 else "")
    return f"{sign}{_points(value * 100)}%"


def _period(start: date | None, end: date | None) -> str:
    if end is None:
        return "undated"
    return f"at {end.isoformat()}" if start is None else f"{start.isoformat()} to {end.isoformat()}"


def _metric(field: str) -> str:
    return field.split(":")[-1]


def _derived_metric(fact: DerivedFact) -> str:
    """The metric inside a derived field, unwrapping nested derivations.

    ``derived:difference(derived:ratio(a:X/a:Y))`` gives ``X/Y``.
    """
    inner = fact.field
    while inner.startswith("derived:") and inner.endswith(")") and "(" in inner:
        inner = inner[inner.index("(") + 1 : -1]
    return "/".join(_metric(part) for part in inner.split("/"))


def _render_derived(fact: DerivedFact) -> str:
    if fact.method is Method.GROWTH:
        value = _percent(fact.value, signed=True)
    elif fact.method is Method.RATIO:
        value = _percent(fact.value, signed=False)
    elif fact.unit == "pure":
        value = f"{_signed(fact.value)}{_points(fact.value * 100)} percentage points"
    else:
        value = ("+" if fact.value > 0 else "") + format_value(fact.value, fact.unit)
    basis = "" if fact.basis is Basis.SAME_PERIOD else f", {fact.basis}"
    period = _period(fact.period_start, fact.period_end)
    return f"{value} ({fact.method} in {_derived_metric(fact)}{basis}, {period})"


def render_item(item: Cited) -> str:
    """The text code inserts for one cited item."""
    if isinstance(item, Passage):
        # Shown with its white space collapsed, as the citation checker compares it
        # (scorecard row 1c); the archived span itself is untouched.
        shown = " ".join(item.text.split())
        return f"{OPEN_QUOTE}{shown}{CLOSE_QUOTE} ({item.form} {item.accession}, {item.section})"
    if isinstance(item, TextFact):
        return f"{item.value} ({_metric(item.field)}, {_period(None, item.period_end)})"
    if isinstance(item, DerivedFact):
        return _render_derived(item)
    period = _period(item.period_start, item.period_end)
    return f"{format_value(item.value, item.unit)} ({_metric(item.field)}, {period})"


# --- grading ---


XBRL_TAXONOMIES: Final = ("us-gaap:", "dei:", "ifrs-full:", "srt:", "invest:")


class UngradableSourceError(ValueError):
    """A cited item whose kind of source has no grade: it is never graded by default."""


def _kind(item: Fact | TextFact | Passage) -> SourceKind:
    """The kind of source behind an item. Unknown kinds fail closed."""
    if isinstance(item, Passage):
        return SourceKind.SEC_FILING_TEXT
    if item.field.startswith("sec:form4:"):
        return SourceKind.SEC_FORM4
    if item.field.startswith("sec:8-K:"):
        return SourceKind.SEC_INDEX
    if item.field.startswith(XBRL_TAXONOMIES):
        return SourceKind.SEC_XBRL
    raise UngradableSourceError(f"no grade for the source of {item.field!r}")


def _grades(items: list[Cited], view: FactView) -> Iterator[tuple[Reliability, Credibility]]:
    for item in items:
        if isinstance(item, DerivedFact):
            for source in trace(item, view):
                yield grade_of(_kind(source))
        else:
            yield grade_of(_kind(item))


# --- review ---


def _citation_problems(claim: SubmittedClaim) -> list[str]:
    problems = []
    if not claim.cites:
        problems.append("the claim cites nothing; every claim rests on cited facts")
    if len(claim.cites) > MAX_CITES:
        problems.append(f"the claim cites too many items (over {MAX_CITES})")
    if len(set(claim.cites)) != len(claim.cites):
        problems.append("the claim cites the same item twice")
    for cite in claim.cites:
        if not PLACEHOLDER.fullmatch("{" + cite + "}"):
            problems.append(f"{cite!r} is not a fact or passage ID")
    for placed in PLACEHOLDER.findall(claim.text):
        if placed not in claim.cites:
            problems.append(f"{placed} is placed in the text but not cited")
    return problems


def _probability_problems(claim: SubmittedClaim) -> list[str]:
    p = claim.probability
    if isinstance(p, bool) or (p is not None and not isinstance(p, int | float)):
        return ["the probability must be a number between 0 and 1"]
    if claim.type in NEEDS_PROBABILITY:
        if p is None:
            return [f"an {claim.type} claim needs a probability"]
        if not (math.isfinite(p) and 0.0 <= p <= 1.0):
            return ["the probability must be between 0 and 1"]
        return []
    if p is not None:
        return [f"a {claim.type} claim takes no probability"]
    return []


def _section_problems(section: str) -> list[str]:
    if not section.strip() or len(section) > MAX_SECTION_CHARS or has_hidden_characters(section):
        return ["the section name is empty, too long or has hidden characters"]
    return []


def _resolve(claim: SubmittedClaim, view: FactView) -> tuple[dict[str, Cited], list[str]]:
    facts = view.get_many(c for c in claim.cites if c.startswith("f_"))
    passages = view.get_passages(c for c in claim.cites if c.startswith("p_"))
    found: dict[str, Cited] = {**facts, **passages}
    missing = [c for c in claim.cites if c not in found and PLACEHOLDER.fullmatch("{" + c + "}")]
    return found, [f"{c} is not in the record as of {view.as_of.isoformat()}" for c in missing]


def _fill(text: str, found: dict[str, Cited]) -> tuple[str, tuple[tuple[int, int], ...]]:
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    position = 0
    length = 0
    for match in PLACEHOLDER.finditer(text):
        before = text[position : match.start()]
        parts.append(before)
        length += len(before)
        inserted = render_item(found[match.group(1)])
        spans.append((length, length + len(inserted)))
        parts.append(inserted)
        length += len(inserted)
        position = match.end()
    parts.append(text[position:])
    return "".join(parts), tuple(spans)


def claim_id_of(claim: SubmittedClaim) -> str:
    """A stable ID for any submission, even one that will be withheld.

    The fields are JSON-encoded first, so no character the model sends can break the ID.
    """
    fields = [claim.section, str(claim.type), claim.text, list(claim.cites), claim.probability]
    return canonical_id("claim", json.dumps(fields, ensure_ascii=True), prefix=CLAIM_PREFIX)


def review_claim(claim: SubmittedClaim, view: FactView) -> RenderedClaim | WithheldClaim:
    """Render a claim from the record as of the view's date, or withhold it with reasons."""
    claim_id = claim_id_of(claim)
    problems = [
        *_section_problems(claim.section),
        *_citation_problems(claim),
        *_probability_problems(claim),
    ]
    found, missing = _resolve(claim, view)
    problems.extend(missing)
    placed = {found[i] for i in PLACEHOLDER.findall(claim.text) if i in found}
    placed_derived = [(f.method, f.value) for f in placed if isinstance(f, DerivedFact)]
    problems.extend(check_model_text(claim.text, placed_derived=placed_derived))
    grades: list[tuple[Reliability, Credibility]] = []
    text: str = ""
    spans: tuple[tuple[int, int], ...] = ()
    if not problems:
        try:
            grades = list(_grades(list(found.values()), view))
            text, spans = _fill(claim.text, found)
        except UntraceableError as error:
            problems.append(f"a derived fact cannot be traced: {error}")
        except ValueError as error:  # an ungradable source, or a value that cannot render
            problems.append(f"a cited item cannot be shown: {error}")
    worst = weakest(grades)
    if problems or worst is None:
        return WithheldClaim(
            claim_id, claim.section, claim.type, claim.text, claim.cites,
            tuple(problems or ["the claim has no gradable source"]),
        )  # fmt: skip
    return RenderedClaim(
        claim_id=claim_id,
        section=claim.section,
        type=claim.type,
        text=text,
        code_spans=spans,
        cites=claim.cites,
        probability=claim.probability,
        likelihood=None if claim.probability is None else likelihood_word(claim.probability),
        reliability=worst[0],
        credibility=worst[1],
        confidence=confidence_from(grades),
        model_text=claim.text,
    )
