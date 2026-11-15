"""What a dossier holds, and the rule that nothing in it is silently missing (row 1d).

The 19 sections are those of brief 2.1. Each section is either **filled** (it has at least
one line, table row or claim) or **unavailable**, with its reason stated. A filled section
may still list **gaps**: parts of its brief that could not be filled, each with a reason.
:class:`Dossier` refuses to exist with a section missing, out of order, or empty without a
reason, so a renderer can never print a silent gap.

Every value shown is a :class:`Cell` written by code: either a fixed label or the text code
rendered from the fact and passage IDs in ``cites``. Claims are the model's words with
code-filled values (:mod:`foxcast.claims.render`), shown only after the citation checker
has verified them.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Final

from foxcast.claims.render import RenderedClaim
from foxcast.facts.record import has_hidden_characters


@dataclass(frozen=True)
class SectionSpec:
    number: int
    key: str
    title: str


SECTIONS: Final = (
    SectionSpec(1, "header", "Header"),
    SectionSpec(2, "what_changed", "What changed"),
    SectionSpec(3, "business", "The business"),
    SectionSpec(4, "management", "Management, governance and ownership"),
    SectionSpec(5, "industry_peers", "Industry and peers"),
    SectionSpec(6, "financial_health", "Financial health"),
    SectionSpec(7, "capital_structure", "Capital structure and dilution"),
    SectionSpec(8, "price_implies", "What the price implies"),
    SectionSpec(9, "wall_street", "What Wall Street says"),
    SectionSpec(10, "market_pricing", "What the market is pricing"),
    SectionSpec(11, "news_events", "News and events"),
    SectionSpec(12, "calendar", "Calendar"),
    SectionSpec(13, "bull_bear", "Bull case, bear case"),
    SectionSpec(14, "event_outlook", "Event outlook"),
    SectionSpec(15, "forecast", "Forecast"),
    SectionSpec(16, "three_questions", "Three questions"),
    SectionSpec(17, "track_record", "Track record"),
    SectionSpec(18, "sources", "Sources"),
    SectionSpec(19, "notice", "Notice"),
)
SECTION_COUNT: Final = 19
SPEC_BY_KEY: Final = {spec.key: spec for spec in SECTIONS}

# Where the research step may place claims: the claim's ``section`` names one of these,
# and each maps to a dossier section (13 has a bull and a bear part). Sections built by
# code alone (the header, the diff, market data, the forecast, the ledger, the sources
# and the notice) take no model claims.
CLAIM_TARGETS: Final = {
    "business": "business",
    "management": "management",
    "financial_health": "financial_health",
    "capital_structure": "capital_structure",
    "news_events": "news_events",
    "bull_case": "bull_bear",
    "bear_case": "bull_bear",
    "three_questions": "three_questions",
}

MAX_REASON_CHARS: Final = 1200


class Status(StrEnum):
    FILLED = "filled"
    UNAVAILABLE = "unavailable"


class Holding(StrEnum):
    """The operator's own declaration about a position in the stock (brief 2.1, row 1)."""

    HOLDS = "holds a position"
    NONE = "holds no position"
    NOT_DECLARED = "not declared"


class SilentGapError(ValueError):
    """A section is missing, out of order, or empty without a stated reason (row 1d)."""


def _check_reason(reason: str) -> None:
    if not reason.strip() or len(reason) > MAX_REASON_CHARS or has_hidden_characters(reason):
        raise SilentGapError("a reason must be stated in plain, short text")


@dataclass(frozen=True)
class Cell:
    """A shown value: a fixed label (no cites) or text rendered by code from ``cites``."""

    text: str
    cites: tuple[str, ...] = ()


@dataclass(frozen=True)
class Line:
    label: str
    value: Cell


@dataclass(frozen=True)
class Table:
    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...]

    def __post_init__(self) -> None:
        if not self.rows:
            raise SilentGapError(f"table {self.title!r} has no rows; state a gap instead")
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError(f"table {self.title!r}: every row needs one cell per column")


@dataclass(frozen=True)
class Gap:
    """A part of a filled section that could not be filled, and why."""

    item: str
    reason: str

    def __post_init__(self) -> None:
        _check_reason(self.reason)


@dataclass(frozen=True)
class Section:
    number: int
    key: str
    title: str
    status: Status
    reason: str | None = None
    lines: tuple[Line, ...] = ()
    tables: tuple[Table, ...] = ()
    claims: tuple[RenderedClaim, ...] = ()
    gaps: tuple[Gap, ...] = ()

    @property
    def has_content(self) -> bool:
        return bool(self.lines or self.tables or self.claims)

    def __post_init__(self) -> None:
        if self.status is Status.FILLED:
            if not self.has_content:
                raise SilentGapError(f"section {self.number} is marked filled but is empty")
            if self.reason is not None:
                raise ValueError(f"section {self.number} is filled, so it takes no reason")
            return
        if self.has_content or self.gaps:
            raise ValueError(f"section {self.number} is unavailable but has content")
        if self.reason is None:
            raise SilentGapError(f"section {self.number} is unavailable with no reason")
        _check_reason(self.reason)


def unavailable(spec_key: str, reason: str) -> Section:
    spec = SPEC_BY_KEY[spec_key]
    return Section(spec.number, spec.key, spec.title, Status.UNAVAILABLE, reason=reason)


def fill_or_unavailable(
    spec_key: str,
    reason: str,
    *,
    lines: tuple[Line, ...] = (),
    tables: tuple[Table, ...] = (),
    claims: tuple[RenderedClaim, ...] = (),
    gaps: tuple[Gap, ...] = (),
) -> Section:
    """Filled if there is any content; otherwise unavailable, stating ``reason`` and gaps."""
    spec = SPEC_BY_KEY[spec_key]
    if lines or tables or claims:
        return Section(
            spec.number,
            spec.key,
            spec.title,
            Status.FILLED,
            lines=lines,
            tables=tables,
            claims=claims,
            gaps=gaps,
        )
    stated = "; ".join([reason, *(f"{gap.item}: {gap.reason}" for gap in gaps)])
    return unavailable(spec_key, stated)


@dataclass(frozen=True)
class WithheldEntry:
    """A claim not shown, listed in section 18 with every reason.

    The model's own words are not kept: they failed the checks that keep model-written
    numbers and quotes out of a dossier (row 1b). The raw submission belongs to the run
    record, not the dossier.
    """

    claim_id: str
    section: str
    type: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Certification:
    """The machine-written line on every dossier (change AQ)."""

    model: str | None  # self-reported by the agent (change BH); None: built without a model
    stated_cutoff: str | None  # self-reported
    prompt_sha256: str | None
    claims_counted: int  # row 1c's denominator for this dossier
    claims_verified: int
    claims_withheld: int
    items_shown: int  # facts and passages shown, each listed in section 18
    words_check_version: str
    code_version: str


# --- the research-claims section (Stage 1B skeleton) ---

NO_RESEARCH: Final = (
    "no research step ran for this dossier, so it holds no research claims; they stay "
    "unavailable until a research backend runs one (Stage 1B)"
)


class ResearchStatus(StrEnum):
    RAN = "ran"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class TargetClaims:
    """One claim target: the IDs of its claims shown, and how many were withheld."""

    target: str
    shown: tuple[str, ...] = ()
    withheld: int = 0

    def __post_init__(self) -> None:
        if self.target not in CLAIM_TARGETS:
            raise ValueError(f"{self.target!r} is not a claim target")
        if self.withheld < 0:
            raise ValueError("a withheld count cannot be negative")

    @property
    def section_number(self) -> int:
        return SPEC_BY_KEY[CLAIM_TARGETS[self.target]].number


@dataclass(frozen=True)
class ResearchClaims:
    """What the research step submitted, accounted for claim by claim, or why there is none.

    Not one of the 19 sections of brief 2.1: it is the index of the claims placed in them.
    Unavailable (with a reason) unless a research step ran; a run lists every claim target
    in order, so a target with nothing shown is visible rather than missing.
    ``withheld_elsewhere`` counts claims that named a section taking no claims.
    """

    status: ResearchStatus
    reason: str | None = None
    model: str | None = None  # self-reported by the agent (change BH)
    targets: tuple[TargetClaims, ...] = ()
    withheld_elsewhere: int = 0

    def __post_init__(self) -> None:
        if self.withheld_elsewhere < 0:
            raise ValueError("a withheld count cannot be negative")
        if self.status is ResearchStatus.UNAVAILABLE:
            if self.model is not None or self.targets or self.withheld_elsewhere:
                raise ValueError("research claims that are unavailable carry no model or claims")
            if self.reason is None:
                raise SilentGapError("research claims are unavailable with no reason")
            _check_reason(self.reason)
            return
        if self.reason is not None:
            raise ValueError("a research run takes no reason; its claims are listed")
        if not (self.model or "").strip():
            raise ValueError("a research run needs the model that wrote its claims")
        if tuple(t.target for t in self.targets) != tuple(CLAIM_TARGETS):
            raise ValueError("a research run lists every claim target once, in order")

    @property
    def shown_count(self) -> int:
        return sum(len(t.shown) for t in self.targets)

    @property
    def withheld_count(self) -> int:
        return sum(t.withheld for t in self.targets) + self.withheld_elsewhere


def no_research(reason: str = NO_RESEARCH) -> ResearchClaims:
    return ResearchClaims(ResearchStatus.UNAVAILABLE, reason=reason)


@dataclass(frozen=True)
class Dossier:
    ticker: str
    cik: int
    company: str | None
    as_of: date
    built_at: datetime
    certification: Certification
    sections: tuple[Section, ...]
    withheld: tuple[WithheldEntry, ...] = ()
    verified_claim_ids: frozenset[str] = field(default_factory=frozenset)
    research: ResearchClaims = field(default_factory=no_research)

    def __post_init__(self) -> None:
        problems = silent_gaps(self.sections)
        if problems:
            raise SilentGapError("; ".join(problems))
        mismatch = research_mismatch(self)
        if mismatch:
            raise ValueError(f"the research claims disagree with the dossier: {mismatch}")

    def section(self, key: str) -> Section:
        return next(s for s in self.sections if s.key == key)


def research_mismatch(dossier: Dossier) -> str | None:
    """How the research section misstates the claims the dossier shows or withholds.

    Checked against the claims in the sections, not ``verified_claim_ids``, so the row 1a
    check at render time stays the one guard on verification.
    """
    research = dossier.research
    shown = [c for s in dossier.sections for c in s.claims]
    if research.status is ResearchStatus.UNAVAILABLE:
        if shown or dossier.withheld:
            return "claims are shown or withheld, but the research step is marked unavailable"
        return None
    if research.model != dossier.certification.model:
        return "the model differs from the certification's"
    # Per target, not per section: bull and bear claims share section 13.
    placed = {
        target: tuple(dict.fromkeys(c.claim_id for c in shown if c.section == target))
        for target in CLAIM_TARGETS
    }
    if any(t.shown != placed[t.target] for t in research.targets):
        return "the claim IDs listed are not the claims shown under each target"
    listed = Counter(w.section for w in dossier.withheld)
    counted = {t.target: t.withheld for t in research.targets}
    elsewhere = sum(n for section, n in listed.items() if section not in CLAIM_TARGETS)
    if any(counted[t] != listed[t] for t in CLAIM_TARGETS) or (
        research.withheld_elsewhere != elsewhere
    ):
        return "the withheld counts are not the claims listed in section 18"
    return None


def silent_gaps(sections: tuple[Section, ...]) -> list[str]:
    """Every way the sections fall short of row 1d; empty means none."""
    problems = []
    if len(sections) != SECTION_COUNT:
        problems.append(f"a dossier has {SECTION_COUNT} sections, not {len(sections)}")
    for spec, section in zip(SECTIONS, sections, strict=False):
        if (section.number, section.key) != (spec.number, spec.key):
            problems.append(f"section {spec.number} should be {spec.key!r}, not {section.key!r}")
        elif section.status is Status.UNAVAILABLE and not (section.reason or "").strip():
            problems.append(f"section {spec.number} is unavailable with no reason")
        elif section.status is Status.FILLED and not section.has_content:
            problems.append(f"section {spec.number} is marked filled but is empty")
    return problems
