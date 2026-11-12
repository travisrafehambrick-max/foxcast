"""Planted false claims, and the checker's false-accept rate beside row 1c (Stage 1A.5).

SCORECARD.md fixes the method: take verified claims from a run, change one thing in each,
25 per category, drawn with seed ``2026111103``, and run every planted claim through the
same review and checker. The six categories:

- **altered number:** a shown figure moved by at least 5%;
- **altered quote:** one word of a quoted passage changed;
- **wrong unit:** a scale word swapped (billion for million, a factor of 1,000) or a
  percent shown as a raw ratio (a factor of 100);
- **wrong period**, **wrong company**, **wrong metric:** the cited fact swapped for a
  genuine one that differs in that respect, while the claim's words stay the same. These
  are drawn only from claims whose words name that thing (or, for the company, from any
  claim, since every claim is about the dossier's company).

If fewer than 25 claims are eligible for a category, all are used and the shortfall is
reported. Any accepted planted claim is a checker bug, fixed before the 1A gate is read.
"""

import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Final

from foxcast.claims.checker import (
    BARE_YEAR,
    FISCAL_YEAR,
    METRIC_PATTERNS,
    QUARTER,
    CheckContext,
    check_claim,
)
from foxcast.claims.numbers_rule import PLACEHOLDER
from foxcast.claims.render import RenderedClaim, SubmittedClaim, WithheldClaim, review_claim
from foxcast.facts.derived import DerivedFact
from foxcast.facts.record import Fact, Passage
from foxcast.scoring.bounds import clopper_pearson_upper
from foxcast.store.pit import FactView

PLANTED_SEED: Final = 2026111103
PER_CATEGORY: Final = 25
NUMBER_SHIFT: Final = 1.07  # at least 5%, beyond any rounding tolerance
SCALE_SWAPS: Final = ((" trillion", " billion"), (" billion", " million"), (" million", " billion"))


class PlantedCategory(StrEnum):
    ALTERED_NUMBER = "altered number"
    ALTERED_QUOTE = "altered quote"
    WRONG_PERIOD = "wrong period"
    WRONG_UNIT = "wrong unit"
    WRONG_COMPANY = "wrong company"
    WRONG_METRIC = "wrong metric"


@dataclass(frozen=True)
class PlantedClaim:
    category: PlantedCategory
    source_claim_id: str
    description: str
    claim: RenderedClaim | WithheldClaim  # a swap may already be withheld at review


@dataclass(frozen=True)
class CategoryResult:
    planted: int
    accepted: int
    shortfall: int

    @property
    def false_accept_rate(self) -> float | None:
        return self.accepted / self.planted if self.planted else None

    @property
    def upper_bound(self) -> float | None:
        return clopper_pearson_upper(self.accepted, self.planted) if self.planted else None


@dataclass(frozen=True)
class PlantedReport:
    by_category: dict[PlantedCategory, CategoryResult]
    overall: CategoryResult
    seed: int


# --- editing a shown span ---


def _swap_span(claim: RenderedClaim, index: int, new_span: str) -> RenderedClaim:
    start, end = claim.code_spans[index]
    shift = len(new_span) - (end - start)
    spans = list(claim.code_spans)
    spans[index] = (start, start + len(new_span))
    for i in range(index + 1, len(spans)):
        spans[i] = (spans[i][0] + shift, spans[i][1] + shift)
    text = claim.text[:start] + new_span + claim.text[end:]
    return replace(claim, text=text, code_spans=tuple(spans))


NUMBER_IN_SPAN: Final = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?")


def _altered_number(span: str) -> str | None:
    match = NUMBER_IN_SPAN.search(span)
    if match is None:
        return None
    old = match.group(0)
    decimals = len(old.split(".")[1]) if "." in old else 0
    shifted = float(old.replace(",", "")) * NUMBER_SHIFT
    return span[: match.start()] + f"{shifted:,.{decimals}f}" + span[match.end() :]


def _wrong_unit(span: str) -> str | None:
    value_part, _, label = span.partition(" (")
    for old, new in SCALE_SWAPS:
        if old in value_part:
            return value_part.replace(old, new, 1) + " (" + label
    if value_part.endswith("%"):
        match = NUMBER_IN_SPAN.search(value_part)
        if match:
            raw = float(match.group(0).replace(",", "")) / 100
            return value_part[: match.start()] + f"{raw:.4f}" + " (" + label
    match = NUMBER_IN_SPAN.search(value_part)
    if match is None:
        return None
    old = match.group(0)
    decimals = len(old.split(".")[1]) if "." in old else 0
    bigger = float(old.replace(",", "")) * 1000
    shown = f"{bigger:,.{decimals}f}"
    return value_part[: match.start()] + shown + value_part[match.end() :] + " (" + label


WORD: Final = re.compile(r"[A-Za-z]{4,}")


def _altered_quote(span: str) -> str | None:
    match = WORD.search(span)
    if match is None:
        return None
    return span[: match.start()] + match.group(0)[::-1].lower() + span[match.end() :]


# --- swapping a cited fact ---


def _placed(claim: RenderedClaim, view: FactView) -> list[tuple[int, object]]:
    """Each placed item with its position among the claim's code spans."""
    ids = PLACEHOLDER.findall(claim.model_text)
    facts = view.get_many(i for i in ids if i.startswith("f_"))
    passages = view.get_passages(i for i in ids if i.startswith("p_"))
    found: dict[str, object] = {**facts, **passages}
    return [(n, found[i]) for n, i in enumerate(ids) if i in found]


def _swapped(
    claim: RenderedClaim, old: str, new: str, view: FactView
) -> RenderedClaim | WithheldClaim:
    submission = SubmittedClaim(
        section=claim.section,
        type=claim.type,
        text=claim.model_text.replace("{" + old + "}", "{" + new + "}"),
        cites=tuple(new if c == old else c for c in claim.cites),
        probability=claim.probability,
    )
    return review_claim(submission, view)


def _names_period(claim: RenderedClaim) -> bool:
    words = PLACEHOLDER.sub(" ", claim.model_text)
    return bool(FISCAL_YEAR.search(words) or QUARTER.search(words) or BARE_YEAR.search(words))


def _names_metric(claim: RenderedClaim) -> bool:
    words = PLACEHOLDER.sub(" ", claim.model_text)
    return any(pattern.search(words) for pattern, _ in METRIC_PATTERNS)


@dataclass(frozen=True)
class _Planter:
    view: FactView
    candidates: Sequence[Fact]

    def number_like(self, claim: RenderedClaim, edit: Callable[[str], str | None],
                    category: PlantedCategory, want: Any) -> list[PlantedClaim]:  # fmt: skip
        out = []
        for index, item in _placed(claim, self.view):
            if not isinstance(item, want):
                continue
            start, end = claim.code_spans[index]
            new_span = edit(claim.text[start:end])
            if new_span and new_span != claim.text[start:end]:
                out.append(PlantedClaim(category, claim.claim_id, f"span {index}: {new_span}",
                                        _swap_span(claim, index, new_span)))  # fmt: skip
        return out

    def swaps(self, claim: RenderedClaim, category: PlantedCategory) -> list[PlantedClaim]:
        out = []
        for _, item in _placed(claim, self.view):
            if not isinstance(item, Fact):
                continue
            for other in self.candidates:
                if other.fact_id == item.fact_id or not self._differs(category, item, other):
                    continue
                result = _swapped(claim, item.fact_id, other.fact_id, self.view)
                out.append(PlantedClaim(category, claim.claim_id,
                                        f"{item.fact_id} -> {other.fact_id}", result))  # fmt: skip
        return out

    @staticmethod
    def _differs(category: PlantedCategory, item: Fact, other: Fact) -> bool:
        same_company = other.cik == item.cik
        if category is PlantedCategory.WRONG_COMPANY:
            return not same_company and other.unit == item.unit
        if not same_company or other.unit != item.unit:
            return False
        if category is PlantedCategory.WRONG_PERIOD:
            # another period of the same metric: another year, or another quarter in-year
            return other.field == item.field and other.period_end != item.period_end
        return other.field != item.field and other.period_end == item.period_end  # metric


def plant(
    verified: Sequence[RenderedClaim],
    candidates: Sequence[Fact],
    view: FactView,
    *,
    seed: int = PLANTED_SEED,
    per_category: int = PER_CATEGORY,
) -> list[PlantedClaim]:
    """Planted claims, up to ``per_category`` of each, drawn reproducibly from ``seed``.

    ``candidates`` are genuine facts the swaps may draw on (other periods, metrics and
    companies).
    """
    planter = _Planter(view, candidates)
    pools: dict[PlantedCategory, list[PlantedClaim]] = {c: [] for c in PlantedCategory}
    for claim in sorted(verified, key=lambda c: c.claim_id):
        pools[PlantedCategory.ALTERED_NUMBER] += planter.number_like(
            claim, _altered_number, PlantedCategory.ALTERED_NUMBER, Fact | DerivedFact
        )
        pools[PlantedCategory.WRONG_UNIT] += planter.number_like(
            claim, _wrong_unit, PlantedCategory.WRONG_UNIT, Fact | DerivedFact
        )
        pools[PlantedCategory.ALTERED_QUOTE] += planter.number_like(
            claim, _altered_quote, PlantedCategory.ALTERED_QUOTE, Passage
        )
        pools[PlantedCategory.WRONG_COMPANY] += planter.swaps(claim, PlantedCategory.WRONG_COMPANY)
        if _names_period(claim):
            pools[PlantedCategory.WRONG_PERIOD] += planter.swaps(
                claim, PlantedCategory.WRONG_PERIOD
            )
        if _names_metric(claim):
            pools[PlantedCategory.WRONG_METRIC] += planter.swaps(
                claim, PlantedCategory.WRONG_METRIC
            )
    rng = random.Random(seed)  # noqa: S311 (a reproducible sample, not security)
    planted = []
    for category in PlantedCategory:
        pool = pools[category]
        planted += pool if len(pool) <= per_category else rng.sample(pool, per_category)
    return planted


Check = Callable[[RenderedClaim, CheckContext], list[str]]


def run_planted(
    planted: Sequence[PlantedClaim],
    context: CheckContext,
    *,
    per_category: int = PER_CATEGORY,
    seed: int = PLANTED_SEED,
    check: Check = check_claim,
) -> PlantedReport:
    """Run every planted claim through the checker; count those it wrongly accepts."""
    by_category: dict[PlantedCategory, CategoryResult] = {}
    for category in PlantedCategory:
        rows = [p for p in planted if p.category is category]
        accepted = sum(
            1 for p in rows if isinstance(p.claim, RenderedClaim) and not check(p.claim, context)
        )
        by_category[category] = CategoryResult(
            len(rows), accepted, max(0, per_category - len(rows))
        )
    overall = CategoryResult(
        sum(r.planted for r in by_category.values()),
        sum(r.accepted for r in by_category.values()),
        sum(r.shortfall for r in by_category.values()),
    )
    return PlantedReport(by_category, overall, seed)
