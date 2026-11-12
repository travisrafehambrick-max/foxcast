"""The citation checker (Stage 1A.5; scorecard rows 1a and 1c).

A rendered claim is **verified** only if all of these hold (SCORECARD.md, "How rows 1c and
1e are measured"):

1. **The sources hold.** Each cited fact is rebuilt from the content-hashed copy of its
   source saved at fetch time, with the same code that first built it, and must come out
   identical. A passage must match its archived bytes. A derived fact must recompute from
   inputs that themselves hold.
2. **The rendered text holds.** Each inserted span must be exactly what the renderer
   writes for its item, and its number, read back, must lie within half a unit of the
   last digit shown of the item's value.
3. **The words hold.** Every period, company, unit and metric the claim's own words name
   must be carried by a cited item, and every cited item must be about the dossier's
   company. ``WORDS_CHECK_VERSION`` is a hash of the patterns and vocabulary, recorded
   with each run (plan 1A.10).

A claim that fails any check is withheld and listed with its reasons. Row 1c's rate is
verified claims over counted claims: every claim that cites at least one fact or passage,
whatever type it was given, including claims the numbers rule already withheld.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Final

from foxcast.claims.numbers_rule import PLACEHOLDER
from foxcast.claims.render import ClaimType, RenderedClaim, WithheldClaim, render_item
from foxcast.facts.derived import DerivedFact, UntraceableError, recompute, trace
from foxcast.facts.record import (
    SEC_PUBLIC_LAG,
    Fact,
    Passage,
    TextFact,
    facts_from_sec,
    verify_passage,
)
from foxcast.sources.base import Fetched
from foxcast.sources.insider import SecForm4Provider, form4_facts
from foxcast.sources.sec import SecConceptProvider
from foxcast.sources.sec_filings import SecSubmissionsProvider, eight_k_item_facts
from foxcast.store.archive import SourceArchive
from foxcast.store.pit import FactView

Item = Fact | TextFact | DerivedFact | Passage
CHECKER_CONTACT: Final = "foxcast citation checker"  # parsing only; nothing is fetched

# --- the words check: patterns and vocabulary, fixed before the gate (plan 1A.10) ---

METRICS: Final[dict[str, tuple[str, ...]]] = {
    "cost of revenue|cost of revenues|cost of sales": ("CostOfRevenue",),
    "operating cash flow|cash from operations|cash provided by operating activities": (
        "NetCashProvidedByUsedInOperatingActivities",
    ),
    "capital expenditures?|capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "cash and cash equivalents": ("CashAndCashEquivalentsAtCarryingValue",),
    "earnings per share|EPS": ("EarningsPerShareDiluted", "EarningsPerShareBasic"),
    "operating income|operating profit": ("OperatingIncomeLoss",),
    "net income|net profit|net loss": ("NetIncomeLoss",),
    "revenues?|sales": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ),
    "total assets|assets": ("Assets",),
    "total liabilities|liabilities": ("Liabilities",),
    "stockholders'? equity|shareholders'? equity": ("StockholdersEquity",),
}
METRIC_PATTERNS: Final = tuple(
    (re.compile(rf"\b(?:{words})\b", re.IGNORECASE), concepts)
    for words, concepts in METRICS.items()
)
YEAR: Final = r"(?:199[0-9]|20[0-4][0-9])"
MONTH_NUMBERS: Final = {
    m: i + 1
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
    )
}
FULL_DATE: Final = re.compile(
    rf"\b(?:({YEAR})-(0[1-9]|1[0-2])-([0-3][0-9])|"
    rf"({'|'.join(MONTH_NUMBERS)}) ([0-3]?[0-9]), ({YEAR}))\b",
    re.IGNORECASE,
)
QUARTER: Final = re.compile(rf"\bQ([1-4])(?: ({YEAR}))?\b|\b(first|second|third|fourth) quarter\b",
                            re.IGNORECASE)  # fmt: skip
HALF: Final = re.compile(rf"\bH([12])(?: ({YEAR}))?\b|\b(first|second) half\b", re.IGNORECASE)
FISCAL_YEAR: Final = re.compile(rf"\b(?:FY ?|fiscal (?:year )?)({YEAR})\b", re.IGNORECASE)
BARE_YEAR: Final = re.compile(rf"\b({YEAR})\b")
PER_SHARE: Final = re.compile(r"\bper share\b|\bEPS\b", re.IGNORECASE)
SHARE_COUNT: Final = re.compile(r"\bshares\b", re.IGNORECASE)
QUARTER_DAYS: Final = (80, 100)
HALF_DAYS: Final = (170, 190)
YEAR_DAYS: Final = (355, 375)

WORDS_CHECK_VERSION: Final = hashlib.sha256(
    json.dumps(
        {
            "metrics": METRICS,
            "patterns": [FULL_DATE.pattern, QUARTER.pattern, HALF.pattern, FISCAL_YEAR.pattern,
                         BARE_YEAR.pattern, PER_SHARE.pattern, SHARE_COUNT.pattern],
            "days": [QUARTER_DAYS, HALF_DAYS, YEAR_DAYS],
        },
        sort_keys=True,
    ).encode()
).hexdigest()[:16]  # fmt: skip


@dataclass
class CheckContext:
    """What the checker needs: the record as of the dossier date, the archive, the
    dossier's company, and the names and tickers of companies the words may name."""

    view: FactView
    archive: SourceArchive
    subject_cik: int
    companies: Mapping[str, int]
    _rebuilt: dict[tuple[str, str], frozenset[object]] = field(default_factory=dict)


# --- 1. the sources hold ---


def _fetched(raw: bytes, item: Fact | TextFact, table: Any) -> Fetched:
    return Fetched(
        table=table,
        raw=raw,
        content_sha256=item.content_sha256,
        url=item.source_url,
        fetched_at=item.fetched_at,
    )


def _rebuild(item: Fact | TextFact, raw: bytes) -> list[Fact] | list[TextFact]:
    """The facts the archived payload yields, built by the code that built ``item``."""
    accession = item.accession or ""
    filed = item.public_on - SEC_PUBLIC_LAG
    if item.field.startswith("sec:form4:"):
        table = SecForm4Provider(CHECKER_CONTACT).shape(raw)
        numbers, texts = form4_facts(_fetched(raw, item, table), accession=accession, filed=filed)
        return numbers if isinstance(item, Fact) else texts
    if item.field.startswith("sec:8-K:"):
        table = SecSubmissionsProvider(CHECKER_CONTACT).shape(raw)
        return eight_k_item_facts(_fetched(raw, item, table))
    table = SecConceptProvider(CHECKER_CONTACT).shape(raw)
    return facts_from_sec(_fetched(raw, item, table))


def _source_problem(item: Fact | TextFact, context: CheckContext) -> str | None:
    key = (item.content_sha256, item.field.split(":")[0] + ":" + type(item).__name__)
    if key not in context._rebuilt:
        try:
            raw = context.archive.load(item.content_sha256)
            rebuilt = _rebuild(item, raw)
        except Exception as error:
            return f"{item.fact_id}: the archived source cannot be read ({error})"
        context._rebuilt[key] = frozenset(replace(f, fetched_at=item.fetched_at) for f in rebuilt)
    if item not in context._rebuilt[key]:
        return f"{item.fact_id}: the archived source does not hold this fact"
    return None


def _sources_problems(items: Iterable[Item], context: CheckContext) -> list[str]:
    problems = []
    for item in items:
        if isinstance(item, Passage):
            try:
                ok = verify_passage(item, context.archive.load(item.content_sha256))
            except (OSError, ValueError) as error:
                problems.append(f"{item.passage_id}: the archived source cannot be read ({error})")
                continue
            if not ok:
                problems.append(
                    f"{item.passage_id}: the archived source does not hold this passage"
                )
        elif isinstance(item, DerivedFact):
            try:
                recompute(item, context.view)
                sources = trace(item, context.view)
            except UntraceableError as error:
                problems.append(f"{item.fact_id}: the derived value does not hold ({error})")
                continue
            problems.extend(p for s in sources if (p := _source_problem(s, context)))
        else:
            problem = _source_problem(item, context)
            if problem:
                problems.append(problem)
    return problems


# --- 2. the rendered text holds ---

SCALE_WORDS: Final = {"trillion": 1e12, "billion": 1e9, "million": 1e6}
READ_BACK: Final = re.compile(
    r"^(?P<sign>[+-])?(?P<open>\()?\$?(?P<digits>[0-9][0-9,]*(?:\.(?P<decimals>[0-9]+))?)\)?"
    r"(?: (?P<scale>trillion|billion|million))?(?P<rest>.*)$"
)


def read_back(shown: str) -> tuple[float, float] | None:
    """The value a rendered number shows, and half a unit in its last shown digit.

    Handles currency signs, thousands separators, bracketed negatives, percent signs and
    scale words. Percent and percentage points read back in points (``15.09%`` is 15.09).
    """
    match = READ_BACK.match(shown.strip())
    if match is None:
        return None
    number = float(match["digits"].replace(",", ""))
    places = len(match["decimals"] or "")
    scale = SCALE_WORDS.get(match["scale"] or "", 1.0)
    negative = match["sign"] == "-" or bool(match["open"])
    value = (-number if negative else number) * scale
    return value, 0.5 * 10.0**-places * scale


def _expected_value(item: Fact | DerivedFact) -> float:
    if isinstance(item, DerivedFact) and (item.method != "difference" or item.unit == "pure"):
        return item.value * 100  # growth, ratios and margin changes are shown in points
    return item.value


def _render_problems(claim: RenderedClaim, items: Mapping[str, Item]) -> list[str]:
    placed = PLACEHOLDER.findall(claim.model_text)
    if len(placed) != len(claim.code_spans):
        return ["the rendered text does not match its template"]
    problems = []
    outside: list[str] = []
    position = 0
    for item_id, (start, end) in zip(placed, claim.code_spans, strict=True):
        outside.append(claim.text[position:start])
        position = end
        item = items.get(item_id)
        if item is None:
            problems.append(f"{item_id} is placed but not cited")
            continue
        span = claim.text[start:end]
        if span != render_item(item):
            problems.append(f"{item_id}: the rendered text does not match the record")
            continue
        if isinstance(item, Fact | DerivedFact):
            got = read_back(span.split(" (")[0])
            expected = _expected_value(item)
            if got is None or abs(got[0] - expected) > got[1] * (1 + 1e-9):
                problems.append(f"{item_id}: the shown number does not match the value")
    outside.append(claim.text[position:])
    if outside != PLACEHOLDER.split(claim.model_text)[::2]:
        problems.append("the words around the rendered values differ from the template")
    return problems


# --- 3. the words hold ---

# Tickers that are also English words count only inside parentheses (SCORECARD.md).
WORD_TICKERS: Final = frozenset(
    {
        "A",
        "ALL",
        "AN",
        "ANY",
        "ARE",
        "BALL",
        "BE",
        "BIG",
        "CAT",
        "DO",
        "EAT",
        "FAST",
        "FIVE",
        "GO",
        "GOOD",
        "HAS",
        "IT",
        "KEY",
        "LIFE",
        "LOW",
        "NOW",
        "ON",
        "ONE",
        "OPEN",
        "OR",
        "PLAY",
        "RE",
        "SAFE",
        "SO",
        "T",
        "TRUE",
        "WELL",
        "CASH",
    }
)
NAMED_QUARTER: Final = {"first": 1, "second": 2, "third": 3, "fourth": 4}
NAMED_HALF: Final = {"first": 1, "second": 2}
# Fiscal periods end within a week of a calendar quarter end; shift back to land inside it.
PERIOD_END_SHIFT: Final = timedelta(days=7)


def _length(item: Fact | DerivedFact) -> int | None:
    return None if item.period_start is None else (item.period_end - item.period_start).days


def _within(days: int | None, bounds: tuple[int, int]) -> bool:
    return days is not None and bounds[0] <= days <= bounds[1]


def _calendar_end(item: Fact | DerivedFact) -> date:
    """The calendar month a period really ends in (2025-10-01 counts as September)."""
    return item.period_end - PERIOD_END_SHIFT if item.period_end.day <= 7 else item.period_end


def _years_of(item: Item) -> set[int]:
    if isinstance(item, Passage):
        return {int(y) for y in BARE_YEAR.findall(item.text)}
    if isinstance(item, TextFact):
        return {item.period_end.year} if item.period_end else set()
    years = {item.period_end.year}
    if item.period_start is not None:
        years.add(item.period_start.year)
    return years


def _dates_of(item: Item) -> set[date]:
    if isinstance(item, Passage):
        return {item.public_on - SEC_PUBLIC_LAG}
    dates = {item.public_on - SEC_PUBLIC_LAG} if not isinstance(item, DerivedFact) else set()
    end = item.period_end
    if end is not None:
        dates.add(end)
    start = getattr(item, "period_start", None)
    if start is not None:
        dates.add(start)
    return dates


def _carries(
    items: Sequence[Item], bounds: tuple[int, int], *, index: int | None, parts: int,
    year: int | None,
) -> bool:  # fmt: skip
    """Some cited fact covers a period of this length, in this quarter or half and year.

    Quarters and halves are calendar ones, judged by where the period ends. A company's
    own fiscal labels need the XBRL fiscal-period field, which the fact record does not
    keep yet, so a fiscal "Q1" that ends in December is withheld rather than accepted.
    """
    months_per_part = 12 // parts
    for item in items:
        if not isinstance(item, Fact | DerivedFact) or not _within(_length(item), bounds):
            continue
        end = _calendar_end(item)
        if index is not None and (end.month - 1) // months_per_part + 1 != index:
            continue
        if year is not None and end.year != year:
            continue
        return True
    return False


def _named_dates(words: str) -> tuple[list[date], list[str]]:
    found, impossible = [], []
    for m in FULL_DATE.finditer(words):
        try:
            if m.group(1):
                found.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            else:
                month = MONTH_NUMBERS[m.group(4).capitalize()]
                found.append(date(int(m.group(6)), month, int(m.group(5))))
        except ValueError:
            impossible.append(m.group(0))
    return found, impossible


def _part(match: re.Match[str], names: Mapping[str, int]) -> tuple[int | None, int | None]:
    number = match.group(1) or None
    word = match.group(3)
    index = int(number) if number else (names[word.lower()] if word else None)
    year = int(match.group(2)) if match.group(2) else None
    return index, year


def _period_problems(words: str, items: Sequence[Item]) -> list[str]:
    problems = []
    carried_years = set().union(*(_years_of(i) for i in items)) if items else set()
    carried_dates = set().union(*(_dates_of(i) for i in items)) if items else set()
    named, impossible = _named_dates(words)
    problems += [f"the date {d!r} does not exist" for d in impossible]
    for day in named:
        if day not in carried_dates:
            problems.append(f"the period {day.isoformat()} is not carried by a cited item")
    rest = FULL_DATE.sub(" ", words)
    for m in QUARTER.finditer(rest):
        index, year = _part(m, NAMED_QUARTER)
        if not _carries(items, QUARTER_DAYS, index=index, parts=4, year=year):
            problems.append(f"the period {m.group(0)!r} is not carried by a cited quarterly fact")
    for m in HALF.finditer(rest):
        index, year = _part(m, NAMED_HALF)
        if not _carries(items, HALF_DAYS, index=index, parts=2, year=year):
            problems.append(f"the period {m.group(0)!r} is not carried by a cited half-year fact")
    for m in FISCAL_YEAR.finditer(rest):
        if not _carries(items, YEAR_DAYS, index=None, parts=1, year=int(m.group(1))):
            problems.append(f"the period {m.group(0)!r} is not carried by a cited annual fact")
    rest = QUARTER.sub(" ", HALF.sub(" ", FISCAL_YEAR.sub(" ", rest)))
    for m in BARE_YEAR.finditer(rest):
        if int(m.group(1)) not in carried_years:
            problems.append(f"the period {m.group(1)} is not carried by a cited item")
    return problems


def _concept(item: Fact | DerivedFact) -> set[str]:
    inner = item.field
    while inner.startswith("derived:") and "(" in inner:
        inner = inner[inner.index("(") + 1 : -1]
    return {part.split(":")[-1] for part in inner.split("/")}


def _metric_problems(words: str, items: Sequence[Item]) -> list[str]:
    problems = []
    rest = words
    for pattern, concepts in METRIC_PATTERNS:
        match = pattern.search(rest)
        if match is None:
            continue
        rest = pattern.sub(" ", rest)
        carried = any(
            (isinstance(i, Fact | DerivedFact) and _concept(i) & set(concepts))
            or (isinstance(i, Passage) and pattern.search(i.text))
            for i in items
        )
        if not carried:
            problems.append(f"the metric {match.group(0)!r} is not carried by a cited item")
    return problems


CURRENCY_WORDS: Final = (
    (re.compile(r"\b(?:dollars?|USD)\b", re.IGNORECASE), "USD"),
    (re.compile(r"\b(?:euros?|EUR)\b", re.IGNORECASE), "EUR"),
    (re.compile(r"\b(?:pounds? sterling|GBP)\b", re.IGNORECASE), "GBP"),
    (re.compile(r"\b(?:yen|JPY)\b", re.IGNORECASE), "JPY"),
)


def _unit_problems(words: str, items: Sequence[Item]) -> list[str]:
    units = {i.unit for i in items if isinstance(i, Fact | DerivedFact)}
    problems = []
    if PER_SHARE.search(words) and "USD/shares" not in units:
        problems.append("the unit 'per share' is not carried by a cited item")
    if SHARE_COUNT.search(PER_SHARE.sub(" ", words)) and "shares" not in units:
        problems.append("the unit 'shares' is not carried by a cited item")
    for pattern, currency in CURRENCY_WORDS:
        match = pattern.search(words)
        if match and not any(u.startswith(currency) for u in units):
            problems.append(f"the unit {match.group(0)!r} is not carried by a cited item")
    return problems


def _is_ticker(name: str) -> bool:
    return name.isupper() and name.replace(".", "").replace("-", "").isalpha() and len(name) <= 5


def _names(name: str, words: str) -> bool:
    """Whether the words name a company: names in any case, tickers as the scorecard says."""
    escaped = re.escape(name)
    if not _is_ticker(name):
        return bool(re.search(rf"(?<!\w){escaped}(?!\w)", words, re.IGNORECASE))
    if name in WORD_TICKERS or len(name) <= 2:
        return bool(re.search(rf"\(\s*{escaped}\s*\)", words))
    return bool(re.search(rf"(?<![\w.]){escaped}(?![\w])", words))


def _company_problems(words: str, items: Sequence[Item], context: CheckContext) -> list[str]:
    problems = []
    for item in items:
        if item.cik != context.subject_cik:
            item_id = item.passage_id if isinstance(item, Passage) else item.fact_id
            problems.append(f"{item_id} is about another company (CIK {item.cik})")
    for name, cik in context.companies.items():
        if cik != context.subject_cik and _names(name, words):
            problems.append(f"the company {name!r} is not the dossier's company")
    return problems


def _words_problems(
    claim: RenderedClaim, items: Sequence[Item], context: CheckContext
) -> list[str]:
    """The words check. A passage carries periods and metrics only if the claim quotes it,
    so the reader sees the evidence; a passage merely cited carries nothing."""
    words = PLACEHOLDER.sub(" ", claim.model_text)
    placed = set(PLACEHOLDER.findall(claim.model_text))
    carriers = [i for i in items if not isinstance(i, Passage) or i.passage_id in placed]
    return [
        *_company_problems(words, items, context),
        *_period_problems(words, carriers),
        *_metric_problems(words, carriers),
        *_unit_problems(words, carriers),
    ]


# --- claims and dossiers ---


def _cited_items(claim: RenderedClaim, view: FactView) -> tuple[dict[str, Item], list[str]]:
    facts = view.get_many(c for c in claim.cites if c.startswith("f_"))
    passages = view.get_passages(c for c in claim.cites if c.startswith("p_"))
    items: dict[str, Item] = {**facts, **passages}
    missing = [f"{c} is not in the record" for c in claim.cites if c not in items]
    return items, missing


def check_claim(claim: RenderedClaim, context: CheckContext) -> list[str]:
    """Every reason the claim is not verified; an empty list means verified."""
    items, missing = _cited_items(claim, context.view)
    if missing:
        return missing
    cited = [items[c] for c in claim.cites]
    return [
        *_sources_problems(cited, context),
        *_render_problems(claim, items),
        *_words_problems(claim, cited, context),
    ]


@dataclass(frozen=True)
class CheckedWithheld:
    claim_id: str
    section: str
    type: ClaimType
    model_text: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DossierCheck:
    shown: tuple[RenderedClaim, ...]
    withheld: tuple[CheckedWithheld, ...]
    counted: int  # row 1c's denominator
    verified: int
    words_check_version: str

    @property
    def rate(self) -> float | None:
        return self.verified / self.counted if self.counted else None


def check_dossier(
    claims: Sequence[RenderedClaim | WithheldClaim], context: CheckContext
) -> DossierCheck:
    """Check every claim; verified ones are shown, the rest withheld with reasons."""
    shown: list[RenderedClaim] = []
    withheld: list[CheckedWithheld] = []
    counted = 0
    for claim in claims:
        if claim.cites:
            counted += 1
        if isinstance(claim, WithheldClaim):
            withheld.append(CheckedWithheld(claim.claim_id, claim.section, claim.type,
                                            claim.model_text, claim.reasons))  # fmt: skip
            continue
        problems = check_claim(claim, context)
        if problems:
            withheld.append(CheckedWithheld(claim.claim_id, claim.section, claim.type,
                                            claim.model_text, tuple(problems)))  # fmt: skip
        else:
            shown.append(claim)
    return DossierCheck(tuple(shown), tuple(withheld), counted, len(shown), WORDS_CHECK_VERSION)


def row_1a_failures(shown: Iterable[RenderedClaim], *, verified_ids: set[str]) -> list[str]:
    """Fact-type claims shown without a verified citation. Any one fails the run (row 1a)."""
    return [
        c.claim_id for c in shown if c.type is ClaimType.FACT and c.claim_id not in verified_ids
    ]


__all__ = [
    "WORDS_CHECK_VERSION",
    "CheckContext",
    "CheckedWithheld",
    "DossierCheck",
    "check_claim",
    "check_dossier",
    "read_back",
    "row_1a_failures",
]
