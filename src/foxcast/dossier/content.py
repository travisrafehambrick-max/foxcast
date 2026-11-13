"""Code-filled content for the dossier, read from the record as of the dossier date.

Every value is rendered by code from a stored fact (:func:`foxcast.claims.render.
format_value` and friends) and carries the fact's ID. Nothing here guesses: a figure the
record lacks becomes a :class:`~foxcast.dossier.schema.Gap` with its reason.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from foxcast.claims.render import format_value
from foxcast.dossier.schema import Cell, Gap, Line, Table
from foxcast.facts.fiscal import YEAR_DAYS, within
from foxcast.facts.record import Fact, TextFact
from foxcast.store.pit import DocumentSummary, FactStore

XBRL_PREFIX: Final = "us-gaap:"
FORM4_PREFIX: Final = "sec:form4:"
EIGHT_K_FIELD: Final = "sec:8-K:item"
ANNUAL_YEARS: Final = 5
MAX_TRADES: Final = 20
MAX_EVENTS: Final = 25
EVENT_LOOKBACK: Final = timedelta(days=365)

# Brief 2.1: section 6 shows multi-year trends, section 7 debt, share count, buybacks and
# stock pay. Concept names are shown as they are filed.
HEALTH_CONCEPTS: Final = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "CostOfRevenue",
    "OperatingIncomeLoss",
    "NetIncomeLoss",
    "EarningsPerShareDiluted",
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "CashAndCashEquivalentsAtCarryingValue",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
)
CAPITAL_CONCEPTS: Final = (
    "LongTermDebt",
    "LongTermDebtNoncurrent",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "PaymentsForRepurchaseOfCommonStock",
    "ShareBasedCompensation",
    "PaymentsOfDividends",
)
REVENUE_CONCEPTS: Final = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax")

# SEC Form 4 transaction codes (General Instructions, item 8).
FORM4_CODES: Final = {
    "P": "open market or private purchase",
    "S": "open market or private sale",
    "V": "voluntarily reported",
    "A": "grant or award",
    "D": "disposition to the issuer",
    "F": "tax or exercise price paid in shares",
    "I": "discretionary transaction",
    "M": "exercise or conversion of a derivative",
    "C": "conversion of a derivative",
    "E": "expiration of a short derivative position",
    "H": "expiration or cancellation of a long derivative position",
    "O": "exercise of an out-of-the-money derivative",
    "X": "exercise of an in-the-money derivative",
    "G": "gift",
    "L": "small acquisition",
    "W": "acquisition or disposition by will or the laws of descent",
    "Z": "deposit into or withdrawal from a voting trust",
    "J": "other acquisition or disposition",
    "K": "equity swap or similar",
    "U": "disposition in a change of control",
}
# SEC Form 8-K items.
EIGHT_K_ITEMS: Final = {
    "1.01": "Entry into a material definitive agreement",
    "1.02": "Termination of a material definitive agreement",
    "1.03": "Bankruptcy or receivership",
    "1.04": "Mine safety",
    "1.05": "Material cybersecurity incidents",
    "2.01": "Completion of acquisition or disposition of assets",
    "2.02": "Results of operations and financial condition",
    "2.03": "Creation of a direct financial obligation",
    "2.04": "Triggering events that accelerate a financial obligation",
    "2.05": "Costs associated with exit or disposal activities",
    "2.06": "Material impairments",
    "3.01": "Notice of delisting or failure to meet a listing rule",
    "3.02": "Unregistered sales of equity securities",
    "3.03": "Material modification of rights of security holders",
    "4.01": "Changes in the certifying accountant",
    "4.02": "Non-reliance on previously issued financial statements",
    "5.01": "Changes in control of the registrant",
    "5.02": "Departure or appointment of directors or officers; compensation arrangements",
    "5.03": "Amendments to articles of incorporation or bylaws; change in fiscal year",
    "5.04": "Temporary suspension of trading under employee benefit plans",
    "5.05": "Amendments to the code of ethics",
    "5.06": "Change in shell company status",
    "5.07": "Submission of matters to a vote of security holders",
    "5.08": "Shareholder director nominations",
    "6.01": "ABS informational and computational material",
    "6.02": "Change of servicer or trustee",
    "6.03": "Change in credit enhancement",
    "6.04": "Failure to make a required distribution",
    "6.05": "Securities Act updating disclosure",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
    "9.01": "Financial statements and exhibits",
}


def metric_of(field: str) -> str:
    return field.split(":")[-1]


def period_text(fact: Fact) -> str:
    """The period in dates, with the company's fiscal label where the record has one."""
    if fact.period_start is None:
        shown = f"at {fact.period_end.isoformat()}"
    else:
        shown = f"{fact.period_start.isoformat()} to {fact.period_end.isoformat()}"
    if fact.fiscal_year is not None and fact.fiscal_period is not None:
        label = (
            f"FY{fact.fiscal_year}"
            if fact.fiscal_period == "FY"
            else f"{fact.fiscal_period} FY{fact.fiscal_year}"
        )
        shown += f" ({label})"
    return shown


def value_cell(fact: Fact) -> Cell:
    return Cell(format_value(fact.value, fact.unit), (fact.fact_id,))


def _is_annual(fact: Fact) -> bool:
    if fact.period_start is None:
        return False
    return within((fact.period_end - fact.period_start).days, YEAR_DAYS)


def latest_known(facts: Iterable[Fact]) -> list[Fact]:
    """One fact per concept, unit and period: the one most recently made public.

    A later filing that restates a period replaces the earlier figure, as a reader on the
    as-of date would have known it. Ties go to the higher fact ID, so the choice is stable.
    """
    best: dict[tuple[str, str, date | None, date], Fact] = {}
    for fact in facts:
        key = (fact.field, fact.unit, fact.period_start, fact.period_end)
        held = best.get(key)
        if held is None or (fact.public_on, fact.fact_id) > (held.public_on, held.fact_id):
            best[key] = fact
    return list(best.values())


@dataclass(frozen=True)
class Annual:
    """Annual figures by concept, newest first, and the fiscal year ends they fall on."""

    by_concept: dict[str, list[Fact]]
    year_ends: frozenset[date]


def annual_figures(store: FactStore, *, cik: int, as_of: date) -> Annual:
    """Each concept's annual figures: full-year flows, and stocks at fiscal year ends."""
    known = latest_known(store.numbers_for(cik=cik, field_prefix=XBRL_PREFIX, as_of=as_of))
    year_ends = frozenset(f.period_end for f in known if _is_annual(f))
    grouped: defaultdict[str, list[Fact]] = defaultdict(list)
    for fact in known:
        if _is_annual(fact) or (fact.period_start is None and fact.period_end in year_ends):
            grouped[metric_of(fact.field)].append(fact)
    by_concept = {
        concept: sorted(facts, key=lambda f: (f.period_end, f.unit), reverse=True)
        for concept, facts in grouped.items()
    }
    return Annual(by_concept, year_ends)


def _missing(concepts: Sequence[str], as_of: date) -> Gap:
    return Gap(
        "No annual figure for " + ", ".join(concepts),
        f"none in the record as of {as_of.isoformat()} "
        "(not fetched, or not reported by the company under these concepts)",
    )


def annual_table(
    annual: Annual, concepts: Sequence[str], *, title: str, as_of: date
) -> tuple[Table | None, tuple[Gap, ...]]:
    """Up to :data:`ANNUAL_YEARS` years per concept, and one gap naming every concept missing."""
    rows: list[tuple[Cell, ...]] = []
    missing = []
    for concept in concepts:
        facts = annual.by_concept.get(concept, [])
        if not facts:
            missing.append(concept)
            continue
        for fact in facts[:ANNUAL_YEARS]:
            rows.append((Cell(concept), Cell(period_text(fact), (fact.fact_id,)), value_cell(fact)))
    table = Table(title, ("Metric", "Period", "Value"), tuple(rows)) if rows else None
    return table, (_missing(missing, as_of),) if missing else ()


def latest_revenue(annual: Annual) -> Fact | None:
    """The newest annual revenue under either concept (companies move between them)."""
    newest = [facts[0] for c in REVENUE_CONCEPTS if (facts := annual.by_concept.get(c))]
    return max(newest, key=lambda f: (f.period_end, f.public_on), default=None)


# --- filings ---


def latest_document(documents: Sequence[DocumentSummary], form: str) -> DocumentSummary | None:
    """The newest archived document of a form (documents come newest first)."""
    return next((d for d in documents if d.form == form), None)


def document_line(label: str, document: DocumentSummary) -> Line:
    return Line(
        label,
        Cell(
            f"{document.form} {document.accession}, public on {document.public_on.isoformat()}, "
            f"{document.passages} full-text passages archived"
        ),
    )


# --- Form 4 ---


@dataclass(frozen=True)
class Trade:
    """One Form 4 transaction: its text facts and numbers, by name."""

    accession: str
    index: int  # the transaction's place in its filing; later rows report later holdings
    texts: dict[str, TextFact]
    numbers: dict[str, Fact]

    @property
    def dated(self) -> TextFact | Fact | None:
        """The fact that carries the transaction date, if any."""
        facts: list[TextFact | Fact] = [*self.texts.values(), *self.numbers.values()]
        return next((f for f in facts if f.period_end is not None), None)

    @property
    def day(self) -> date | None:
        dated = self.dated
        return dated.period_end if dated else None


def _trade_key(field: str) -> tuple[int, str] | None:
    """``sec:form4:t3:shares`` gives ``(3, "shares")``."""
    parts = field.removeprefix(FORM4_PREFIX).split(":")
    if len(parts) != 2 or not parts[0].startswith("t") or not parts[0][1:].isdigit():
        return None
    return int(parts[0][1:]), parts[1]


def form4_trades(store: FactStore, *, cik: int, as_of: date) -> list[Trade]:
    """Every Form 4 transaction in the record, newest first."""
    texts: defaultdict[tuple[str, int], dict[str, TextFact]] = defaultdict(dict)
    numbers: defaultdict[tuple[str, int], dict[str, Fact]] = defaultdict(dict)
    for text in store.texts_for(cik=cik, field_prefix=FORM4_PREFIX, as_of=as_of):
        key = _trade_key(text.field)
        if key and text.accession:
            texts[(text.accession, key[0])][key[1]] = text
    for number in store.numbers_for(cik=cik, field_prefix=FORM4_PREFIX, as_of=as_of):
        key = _trade_key(number.field)
        if key and number.accession:
            numbers[(number.accession, key[0])][key[1]] = number
    keys = sorted({*texts, *numbers})
    trades = [Trade(acc, i, texts.get((acc, i), {}), numbers.get((acc, i), {})) for acc, i in keys]
    return sorted(trades, key=lambda t: (t.day or date.min, t.accession, t.index), reverse=True)


def _text_cell(trade: Trade, name: str) -> Cell:
    fact = trade.texts.get(name)
    return Cell(fact.value, (fact.fact_id,)) if fact else Cell("not reported")


def _number_cell(trade: Trade, name: str) -> Cell:
    fact = trade.numbers.get(name)
    return value_cell(fact) if fact else Cell("not reported")


def _code_cell(trade: Trade) -> Cell:
    fact = trade.texts.get("code")
    if fact is None:
        return Cell("not reported")
    meaning = FORM4_CODES.get(fact.value)
    return Cell(f"{fact.value} ({meaning})" if meaning else fact.value, (fact.fact_id,))


def _date_cell(trade: Trade) -> Cell:
    dated = trade.dated
    if dated is None or dated.period_end is None:
        return Cell("not reported")
    return Cell(dated.period_end.isoformat(), (dated.fact_id,))


def trades_table(trades: Sequence[Trade]) -> Table | None:
    rows = tuple(
        (
            _date_cell(t),
            _text_cell(t, "owner"),
            _text_cell(t, "role"),
            _code_cell(t),
            _text_cell(t, "acquired_disposed"),
            _number_cell(t, "shares"),
            _number_cell(t, "price"),
            _text_cell(t, "10b5-1"),
        )
        for t in trades[:MAX_TRADES]
    )
    if not rows:
        return None
    columns = ("Date", "Owner", "Role", "Code", "A/D", "Shares", "Price", "10b5-1 plan")
    return Table(f"Insider trades on Form 4 (newest {len(rows)})", columns, rows)


def holdings_table(trades: Sequence[Trade]) -> Table | None:
    """Each owner's holding after their latest reported trade, per security."""
    latest: dict[tuple[str, str], Trade] = {}
    for trade in trades:  # newest first (by day, filing, then row), so first seen is latest
        owner, security = trade.texts.get("owner"), trade.texts.get("security")
        if owner and security and "owned_after" in trade.numbers:
            latest.setdefault((owner.value, security.value), trade)
    rows = tuple(
        (
            _text_cell(t, "owner"),
            _text_cell(t, "role"),
            _text_cell(t, "security"),
            _number_cell(t, "owned_after"),
            _date_cell(t),
        )
        for t in latest.values()
    )
    if not rows:
        return None
    columns = ("Owner", "Role", "Security", "Owned after the trade", "Trade date")
    return Table("Insider holdings after each owner's latest Form 4 trade", columns, rows)


# --- 8-K items ---


def events_table(store: FactStore, *, cik: int, as_of: date) -> Table | None:
    """8-K items made public in the year before the as-of date, newest first."""
    since = as_of - EVENT_LOOKBACK
    items = [
        i
        for i in store.texts_for(cik=cik, field_prefix=EIGHT_K_FIELD, as_of=as_of)
        if i.field == EIGHT_K_FIELD and i.public_on >= since
    ]
    items.sort(key=lambda i: (i.public_on, i.accession or "", i.value), reverse=True)
    rows = tuple(
        (
            Cell(i.public_on.isoformat(), (i.fact_id,)),
            Cell(
                f"{i.value} {EIGHT_K_ITEMS.get(i.value, '(item not in the SEC list)')}",
                (i.fact_id,),
            ),
            Cell(i.accession or "not reported"),
        )
        for i in items[:MAX_EVENTS]
    )
    if not rows:
        return None
    return Table(
        f"8-K items public in the year to {as_of.isoformat()} (newest {len(rows)})",
        ("Public on", "Item", "Accession"),
        rows,
    )
