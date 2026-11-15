"""Assemble a dossier from the record as of one date (Stage 1A.6; rows 1a, 1b and 1d).

:func:`build_dossier` reads only through the store's as-of reads and an as-of
:class:`~foxcast.store.pit.FactView`, so a dossier cannot show its own future. The model's
claims (if a research step ran) are reviewed and rendered by code, then checked; only
verified claims are shown, and the rest are listed in section 18 by ID with their reasons.
The model's own words of a withheld claim are never shown, since they failed the checks
that keep model-written numbers out (row 1b).

Each section is filled from the record or marked unavailable with its reason; the
reasons for Stage 1A are written here, in one place, so a reader sees why a part is empty.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from foxcast import __version__
from foxcast.claims.checker import (
    WORDS_CHECK_VERSION,
    CheckContext,
    DossierCheck,
    check_dossier,
)
from foxcast.claims.render import (
    RenderedClaim,
    SubmittedClaim,
    WithheldClaim,
    claim_id_of,
    review_claim,
)
from foxcast.dossier import content
from foxcast.dossier.research import research_claims
from foxcast.dossier.schema import (
    CLAIM_TARGETS,
    Cell,
    Certification,
    Dossier,
    Gap,
    Holding,
    Line,
    Section,
    WithheldEntry,
    fill_or_unavailable,
    unavailable,
)
from foxcast.dossier.sources import sources_section
from foxcast.facts.record import Fact, TextFact, has_hidden_characters
from foxcast.notice import NOTICE_HEADLINE, NOTICE_TEXT
from foxcast.store.archive import SourceArchive
from foxcast.store.pit import FactStore, FactView

SPLITTING: Final = "10-K section splitting, which waits on edgartools 6.x (from 2026-11-16)"
REASONS: Final = {
    "what_changed": "comparing with an earlier dossier is built in Stage 1B.4; "
    "no earlier dossier was compared on this run",
    "industry_peers": "the peer rule is proposed but not yet approved, and peers' figures "
    "are not fetched (Stage 1B.4)",
    "price_implies": "valuation needs prices and peers, and neither is in the Stage 1A record "
    "(Stage 1B.4). No price target is ever given",
    "wall_street": "analyst ratings, price targets and estimates are not in the fact store; "
    "the Yahoo snapshot and the DoltHub estimate history are wired in Stage 1B.2",
    "calendar": "no calendar source in Stage 1A: the earnings calendar and FRED release "
    f"dates arrive in Stage 1B.2, and dates named in filings need {SPLITTING}",
    "event_outlook": "the event outlook needs the event classes and base rates of brief 2.2, "
    "built in Stage 2",
    "forecast": "forecasts are attached to dossiers once the simulator family is built "
    "(Stage 2); the research forecast also needs the event outlook (section 14)",
    "track_record": "reading the forecast ledger into dossiers is built with settlement "
    "scoring (Stage 3); this run did not read the ledger",
}
NO_MODEL: Final = (
    "built without a model: this section is written by the research step, and none ran"
)
NO_CLAIMS: Final = "the research step submitted no claims for this section"
ALL_WITHHELD: Final = (
    "every claim submitted for this section was withheld; each is listed in section 18"
)
NOT_A_SECTION: Final = "claims are not taken in this section; see the dossier's claim sections"
SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
# A withheld claim's reasons may quote its words ("the period 'Q3 2025' is not carried");
# the dossier shows the reason without them (row 1b).
QUOTED: Final = re.compile(r"'[^']*'|\"[^\"]*\"")
NAMED_PERIOD: Final = re.compile(r"\bthe period \S+ is\b")
FROM_CLAIM: Final = "(words from the claim)"
MAX_DECLARED_CHARS: Final = 80


def _plain(label: str, text: str) -> str:
    cleaned = text.strip()
    if not cleaned or len(cleaned) > MAX_DECLARED_CHARS or has_hidden_characters(cleaned):
        raise ValueError(f"{label} must be short plain text")
    return cleaned


@dataclass(frozen=True)
class ModelDeclaration:
    """What the agent says it is. Shown marked "self-reported" (change BH)."""

    name: str
    stated_cutoff: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _plain("the model name", self.name))
        object.__setattr__(self, "stated_cutoff", _plain("the cutoff", self.stated_cutoff))


@dataclass(frozen=True)
class RunCost:
    """The tool server's cost meter (row 4c): the calls before the render call."""

    tool_calls: int
    result_bytes: int
    wall_seconds: float


@dataclass(frozen=True)
class DossierRequest:
    ticker: str
    cik: int
    as_of: date
    built_at: datetime
    company: str | None = None
    model: ModelDeclaration | None = None  # None: built without a model (--no-llm)
    prompt_sha256: str | None = None
    holding: Holding = Holding.NOT_DECLARED
    cost: RunCost | None = None
    code_commit: str | None = None
    source_problems: tuple[str, ...] = ()  # from IngestReport.unavailable

    def __post_init__(self) -> None:
        _plain("the ticker", self.ticker)
        if self.company is not None:
            _plain("the company name", self.company)
        if self.prompt_sha256 is not None and not SHA256.fullmatch(self.prompt_sha256):
            raise ValueError("the prompt hash must be 64 lowercase hex characters")
        if self.model is None and self.prompt_sha256 is not None:
            raise ValueError("a prompt hash needs a model")


# --- claims ---


def shown_reason(reason: str) -> str:
    """A withheld claim's reason, with any words quoted from the claim taken out."""
    return NAMED_PERIOD.sub("a period named in the claim is", QUOTED.sub(FROM_CLAIM, reason))


def review_claims(
    claims: Sequence[SubmittedClaim], view: FactView
) -> list[RenderedClaim | WithheldClaim]:
    """Render each claim, or withhold it; a claim outside the claim sections is withheld.

    The tool server calls this too, so the verdicts it returns are the dossier's own.
    """
    reviewed: list[RenderedClaim | WithheldClaim] = []
    for claim in claims:
        if claim.section not in CLAIM_TARGETS:
            reviewed.append(
                WithheldClaim(
                    claim_id_of(claim),
                    claim.section,
                    claim.type,
                    claim.text,
                    claim.cites,
                    (NOT_A_SECTION,),
                )
            )
        else:
            reviewed.append(review_claim(claim, view))
    return reviewed


@dataclass(frozen=True)
class _Claims:
    ran: bool  # a research step ran
    check: DossierCheck | None

    def shown(self, *targets: str) -> tuple[RenderedClaim, ...]:
        if self.check is None:
            return ()
        return tuple(c for c in self.check.shown if c.section in targets)

    def gaps(self, *targets: str) -> tuple[Gap, ...]:
        """A note for a section filled from the record that shows no research claims."""
        if self.shown(*targets) or self.reason(*targets) == NO_CLAIMS:
            return ()
        return (Gap("Research claims", self.reason(*targets)),)

    def reason(self, *targets: str) -> str:
        if not self.ran:
            return NO_MODEL
        if self.check and any(w.section in targets for w in self.check.withheld):
            return ALL_WITHHELD
        return NO_CLAIMS


# --- sections ---


def _freshness(store: FactStore, cik: int, as_of: date) -> tuple[Line, ...]:
    def line(label: str, count: int, latest: datetime | None) -> Line:
        if not count or latest is None:
            return Line(label, Cell(f"none in the record as of {as_of.isoformat()}"))
        return Line(label, Cell(f"{count} in the record, last fetched {latest.isoformat()}"))

    xbrl = store.numbers_for(cik=cik, field_prefix=content.XBRL_PREFIX, as_of=as_of)
    form4: list[Fact | TextFact] = [
        *store.numbers_for(cik=cik, field_prefix=content.FORM4_PREFIX, as_of=as_of),
        *store.texts_for(cik=cik, field_prefix=content.FORM4_PREFIX, as_of=as_of),
    ]
    items = [
        i
        for i in store.texts_for(cik=cik, field_prefix=content.EIGHT_K_FIELD, as_of=as_of)
        if i.field == content.EIGHT_K_FIELD
    ]
    documents = store.documents_for(cik=cik, as_of=as_of)
    return (
        line("Fresh: SEC XBRL figures", len(xbrl), max((f.fetched_at for f in xbrl), default=None)),
        line("Fresh: Form 4 values", len(form4), max((f.fetched_at for f in form4), default=None)),
        line("Fresh: 8-K items", len(items), max((f.fetched_at for f in items), default=None)),
        line(
            "Fresh: filing documents",
            len(documents),
            max((d.fetched_at for d in documents), default=None),
        ),
    )


def _header(request: DossierRequest, store: FactStore) -> Section:
    model = request.model
    commit = request.code_commit or "commit not recorded"
    cost = request.cost
    lines = [
        Line("Ticker", Cell(request.ticker)),
        Line("Company", Cell(request.company or "not given")),
        Line("SEC CIK", Cell(str(request.cik))),
        Line("As of", Cell(request.as_of.isoformat())),
        Line("Built at", Cell(request.built_at.isoformat())),
        Line(
            "Model",
            Cell(f"{model.name} (self-reported)" if model else "none: built without a model"),
        ),
        Line("Code version", Cell(f"foxcast {__version__} ({commit})")),
        Line(
            "Run cost",
            Cell(
                f"{cost.tool_calls} tool calls, {cost.result_bytes} result bytes, "
                f"{cost.wall_seconds:.1f} seconds"
                if cost
                else "unavailable: built without the tool server, which meters a run"
            ),
        ),
        Line("Operator's holding", Cell(f"{request.holding} (the operator's own declaration)")),
        *_freshness(store, request.cik, request.as_of),
    ]
    if request.source_problems:
        lines.append(Line("Sources not fetched", Cell("; ".join(request.source_problems))))
    return fill_or_unavailable("header", "", lines=tuple(lines))


def _business(
    request: DossierRequest, store: FactStore, annual: content.Annual, claims: _Claims
) -> Section:
    documents = store.documents_for(cik=request.cik, as_of=request.as_of)
    lines = []
    for label, form in (("Latest annual report", "10-K"), ("Latest quarterly report", "10-Q")):
        document = content.latest_document(documents, form)
        if document:
            lines.append(content.document_line(label, document))
    revenue = content.latest_revenue(annual)
    if revenue:
        lines.append(
            Line(
                f"Latest annual {revenue.field.split(':')[-1]}",
                Cell(
                    f"{content.value_cell(revenue).text} ({content.period_text(revenue)})",
                    (revenue.fact_id,),
                ),
            )
        )
    gaps = (
        Gap(
            "Segments, geography, main customers and suppliers",
            f"these need {SPLITTING}; until then the 10-K is held as full-text passages, "
            "which the research step may cite",
        ),
    )
    return fill_or_unavailable(
        "business",
        claims.reason("business"),
        lines=tuple(lines),
        claims=claims.shown("business"),
        gaps=(*gaps, *claims.gaps("business")),
    )


def _management(trades: list[content.Trade], claims: _Claims) -> Section:
    table = content.holdings_table(trades)
    gaps: tuple[Gap, ...] = (
        Gap("Executives, board and pay", "proxy statements (DEF 14A) are not ingested in Stage 1A"),
        Gap("Major holders", "13F holdings arrive in Stage 1B.2"),
        Gap("Share classes and voting control", f"not in the record; needs {SPLITTING}"),
    )
    if table is None:
        gaps = (Gap("Insider holdings", "no Form 4 trade in the record reports a holding"), *gaps)
    return fill_or_unavailable(
        "management",
        claims.reason("management"),
        tables=(table,) if table else (),
        claims=claims.shown("management"),
        gaps=(*gaps, *claims.gaps("management")),
    )


def _annual_section(
    key: str,
    annual: content.Annual,
    concepts: Sequence[str],
    title: str,
    extra: tuple[Gap, ...],
    claims: _Claims,
    as_of: date,
) -> Section:
    table, missing = content.annual_table(annual, concepts, title=title, as_of=as_of)
    return fill_or_unavailable(
        key,
        claims.reason(key),
        tables=(table,) if table else (),
        claims=claims.shown(key),
        gaps=(*extra, *missing, *claims.gaps(key)),
    )


def _market(trades: list[content.Trade]) -> Section:
    table = content.trades_table(trades)
    gaps = (
        Gap(
            "Option-implied distribution, implied move and implied versus realised volatility",
            "option chains are not cited facts in the Stage 1A record",
        ),
        Gap("Short interest", "FINRA short interest arrives in Stage 1B.2"),
        Gap("Institutional trades", "13F holdings arrive in Stage 1B.2"),
    )
    return fill_or_unavailable(
        "market_pricing",
        "no Form 4 trade is in the record",
        tables=(table,) if table else (),
        gaps=gaps,
    )


def _news(request: DossierRequest, store: FactStore, claims: _Claims) -> Section:
    table = content.events_table(store, cik=request.cik, as_of=request.as_of)
    news = Gap("News", "news feeds and their source grading arrive in Stage 1B.1")
    gaps: tuple[Gap, ...] = (news,)
    if table is None:
        gaps = (
            Gap("8-K filings", "no 8-K item in the record for the year before the as-of date"),
            *gaps,
        )
    return fill_or_unavailable(
        "news_events",
        claims.reason("news_events"),
        tables=(table,) if table else (),
        claims=claims.shown("news_events"),
        gaps=(*gaps, *claims.gaps("news_events")),
    )


def _bull_bear(claims: _Claims) -> Section:
    shown = claims.shown("bull_case", "bear_case")
    gaps = tuple(
        Gap(f"{side.split('_')[0].capitalize()} case", claims.reason(side))
        for side in ("bull_case", "bear_case")
        if shown and not claims.shown(side)
    )
    return fill_or_unavailable(
        "bull_bear", claims.reason("bull_case", "bear_case"), claims=shown, gaps=gaps
    )


def _notice() -> Section:
    return fill_or_unavailable("notice", "", lines=(Line(NOTICE_HEADLINE, Cell(NOTICE_TEXT)),))


# --- the dossier ---


HEALTH_GAPS: Final = (Gap("Forensic scores", "the five forensic scores are built in Stage 1B.3"),)
CAPITAL_GAPS: Final = (
    Gap("Debt maturities and convertibles", f"not in the record; needs {SPLITTING}"),
    Gap("Shelf filings (S-3)", "the filing index is stored only as 8-K items in Stage 1A"),
)


def build_dossier(
    request: DossierRequest,
    store: FactStore,
    archive: SourceArchive,
    *,
    claims: Sequence[SubmittedClaim] | None = None,
    companies: Mapping[str, int] | None = None,
) -> Dossier:
    """Build the 19 sections for ``request``.

    ``claims`` are the research step's submissions; ``None`` means no research step ran
    (``--no-llm``). ``companies`` maps names and tickers to CIKs for the words check.
    """
    if request.model is None and claims is not None:
        raise ValueError("claims need the model that wrote them to be declared")
    view = store.at(request.as_of)
    check = None
    if claims is not None:
        context = CheckContext(
            view=view, archive=archive, subject_cik=request.cik, companies=dict(companies or {})
        )
        check = check_dossier(review_claims(claims, view), context)
    by = _Claims(ran=claims is not None, check=check)
    annual = content.annual_figures(store, cik=request.cik, as_of=request.as_of)
    trades = content.form4_trades(store, cik=request.cik, as_of=request.as_of)
    as_of = request.as_of
    body = [
        _header(request, store),
        unavailable("what_changed", REASONS["what_changed"]),
        _business(request, store, annual, by),
        _management(trades, by),
        unavailable("industry_peers", REASONS["industry_peers"]),
        _annual_section(
            "financial_health",
            annual,
            content.HEALTH_CONCEPTS,
            "Annual figures, up to the five latest years in the record per concept",
            HEALTH_GAPS,
            by,
            as_of,
        ),
        _annual_section(
            "capital_structure",
            annual,
            content.CAPITAL_CONCEPTS,
            "Debt, shares, buybacks and stock pay, up to the five latest years per concept",
            CAPITAL_GAPS,
            by,
            as_of,
        ),
        unavailable("price_implies", REASONS["price_implies"]),
        unavailable("wall_street", REASONS["wall_street"]),
        _market(trades),
        _news(request, store, by),
        unavailable("calendar", REASONS["calendar"]),
        _bull_bear(by),
        unavailable("event_outlook", REASONS["event_outlook"]),
        unavailable("forecast", REASONS["forecast"]),
        fill_or_unavailable(
            "three_questions", by.reason("three_questions"), claims=by.shown("three_questions")
        ),
        unavailable("track_record", REASONS["track_record"]),
    ]
    withheld = tuple(
        WithheldEntry(
            w.claim_id,
            w.section if w.section in CLAIM_TARGETS else "not a section",
            str(w.type),
            tuple(shown_reason(r) for r in w.reasons),
        )
        for w in (check.withheld if check else ())
    )
    sources, items_shown = sources_section(body, withheld, view)
    sections = (*body, sources, _notice())
    model = request.model
    certification = Certification(
        model=model.name if model else None,
        stated_cutoff=model.stated_cutoff if model else None,
        prompt_sha256=request.prompt_sha256,
        claims_counted=check.counted if check else 0,
        claims_verified=check.verified if check else 0,
        claims_withheld=len(withheld),
        items_shown=items_shown,
        words_check_version=WORDS_CHECK_VERSION,
        code_version=__version__,
    )
    return Dossier(
        ticker=request.ticker,
        cik=request.cik,
        company=request.company,
        as_of=request.as_of,
        built_at=request.built_at,
        certification=certification,
        sections=sections,
        withheld=withheld,
        verified_claim_ids=frozenset(c.claim_id for c in (check.shown if check else ())),
        research=research_claims(model.name if model else None, check, withheld),
    )
