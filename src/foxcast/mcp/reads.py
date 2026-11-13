"""The SEC read tools: the run's company, as of the run's date, from the local record only.

Nothing here touches the network. The agent reads what ``foxcast ingest`` stored, through
the store's as-of reads, so it cannot see a fact made public after the run's date and
cannot reach a company or a URL the operator did not ingest. Every value is shown as code
renders it, beside its ID; the agent cites the ID and code inserts the value.

Filing passages, 8-K item codes and Form 4 names are third-party text. They are returned
under an ``untrusted`` key, and the result header lists those paths.
"""

from datetime import date
from typing import Any, Final

from foxcast.claims.render import render_item
from foxcast.facts.derived import DerivedFact
from foxcast.facts.record import Fact, Passage, TextFact
from foxcast.mcp.envelope import ErrorCode, Outcome, ToolError, paged
from foxcast.mcp.inputs import (
    SecGetFacts,
    SecGetTextFacts,
    SecListFilings,
    SecReadPassages,
    SecSearchPassages,
)
from foxcast.mcp.runs import RunInfo
from foxcast.store.pit import FactStore

DERIVED_PREFIX: Final = "derived:"
UNTRUSTED_ITEMS: Final = ("items[].untrusted",)
MAX_SEARCH_DOCUMENTS: Final = 200


def _as_of(info: RunInfo) -> date:
    return date.fromisoformat(info.as_of)


def _day(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def number_json(fact: Fact | DerivedFact) -> dict[str, Any]:
    if isinstance(fact, DerivedFact):
        return {
            "id": fact.fact_id,
            "field": fact.field,
            "method": str(fact.method),
            "basis": str(fact.basis),
            "inputs": list(fact.inputs),
            "period_start": _day(fact.period_start),
            "period_end": _day(fact.period_end),
            "shown": render_item(fact),
        }
    fiscal = (
        f"{fact.fiscal_period} FY{fact.fiscal_year}"
        if fact.fiscal_year is not None and fact.fiscal_period is not None
        else None
    )
    return {
        "id": fact.fact_id,
        "field": fact.field,
        "unit": fact.unit,
        "period_start": _day(fact.period_start),
        "period_end": _day(fact.period_end),
        "fiscal_label": fiscal,
        "accession": fact.accession,
        "public_on": _day(fact.public_on),
        "shown": render_item(fact),
    }


def text_json(fact: TextFact) -> dict[str, Any]:
    return {
        "id": fact.fact_id,
        "field": fact.field,
        "period_end": _day(fact.period_end),
        "accession": fact.accession,
        "public_on": _day(fact.public_on),
        "untrusted": {"value": fact.value, "shown": render_item(fact)},
    }


def passage_json(passage: Passage) -> dict[str, Any]:
    return {
        "id": passage.passage_id,
        "accession": passage.accession,
        "form": passage.form,
        "section": passage.section,
        "start": passage.start,
        "end": passage.end,
        "untrusted": {"text": passage.text},
    }


def get_facts(store: FactStore, info: RunInfo, args: SecGetFacts) -> Outcome:
    as_of = _as_of(info)
    numbers: list[Fact | DerivedFact] = []
    if not args.field_prefix.startswith(DERIVED_PREFIX):
        numbers.extend(store.numbers_for(cik=info.cik, field_prefix=args.field_prefix, as_of=as_of))
    if DERIVED_PREFIX.startswith(args.field_prefix) or args.field_prefix.startswith(DERIVED_PREFIX):
        numbers.extend(
            d
            for d in store.derived_as_of(cik=info.cik, as_of=as_of)
            if d.field.startswith(args.field_prefix)
        )
    items, page = paged([number_json(f) for f in numbers], args.offset, args.limit)
    return Outcome({"items": items}, page=page)


def get_text_facts(store: FactStore, info: RunInfo, args: SecGetTextFacts) -> Outcome:
    texts = store.texts_for(cik=info.cik, field_prefix=args.field_prefix, as_of=_as_of(info))
    items, page = paged([text_json(t) for t in texts], args.offset, args.limit)
    return Outcome({"items": items}, untrusted=UNTRUSTED_ITEMS, page=page)


def list_filings(store: FactStore, info: RunInfo, _: SecListFilings) -> Outcome:
    documents = store.documents_for(cik=info.cik, as_of=_as_of(info))
    items = [
        {
            "accession": d.accession,
            "form": d.form,
            "public_on": d.public_on.isoformat(),
            "passages": d.passages,
        }
        for d in documents
    ]
    return Outcome({"items": items})


def read_passages(store: FactStore, info: RunInfo, args: SecReadPassages) -> Outcome:
    passages = store.passages_as_of(cik=info.cik, accession=args.accession, as_of=_as_of(info))
    if not passages:
        raise ToolError(
            ErrorCode.NOT_FOUND,
            f"no passages for filing {args.accession} as of {info.as_of}; see sec_list_filings",
        )
    items, page = paged([passage_json(p) for p in passages], args.offset, args.limit)
    return Outcome({"items": items}, untrusted=UNTRUSTED_ITEMS, page=page)


def search_passages(store: FactStore, info: RunInfo, args: SecSearchPassages) -> Outcome:
    """Passages holding every word of the query, in any case, newest filing first."""
    as_of = _as_of(info)
    words = [w.casefold() for w in args.query.split()]
    found: list[Passage] = []
    for document in store.documents_for(cik=info.cik, as_of=as_of)[:MAX_SEARCH_DOCUMENTS]:
        for passage in store.passages_as_of(
            cik=info.cik, accession=document.accession, as_of=as_of
        ):
            text = passage.text.casefold()
            if all(word in text for word in words):
                found.append(passage)
    items, page = paged([passage_json(p) for p in found], args.offset, args.limit)
    return Outcome({"query": args.query, "items": items}, untrusted=UNTRUSTED_ITEMS, page=page)
