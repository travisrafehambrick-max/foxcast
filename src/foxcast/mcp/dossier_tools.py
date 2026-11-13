"""The writing tools: derive a figure, submit claims, render the dossier (Stage 1A.7).

``dossier_submit_claims`` keeps every distinct claim the agent submits, in order, and
returns the dossier's own verdict on each: the same review and citation check that
``dossier_render`` runs (:func:`foxcast.dossier.builder.review_claims` and
:func:`foxcast.claims.checker.check_dossier`). Nothing is ever taken back, so a claim that
was withheld stays listed in section 18 and in row 1c's denominator even after the agent
submits a corrected one. Submitting the same claim again is a no-op, which makes a retry
after a restart safe.
"""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

from foxcast.claims.checker import CheckContext, check_dossier
from foxcast.claims.render import ClaimType, SubmittedClaim, claim_id_of, render_item
from foxcast.dossier.builder import (
    DossierRequest,
    ModelDeclaration,
    RunCost,
    build_dossier,
    review_claims,
)
from foxcast.dossier.output import render_json, render_markdown, write_dossier
from foxcast.dossier.schema import Holding, Status
from foxcast.facts.derived import DerivedFact, Method, derive
from foxcast.facts.record import Fact, sha256_hex
from foxcast.mcp.envelope import ErrorCode, Outcome, ToolError, canonical
from foxcast.mcp.inputs import DossierSubmitClaims, FactsDerive
from foxcast.mcp.reads import number_json
from foxcast.mcp.runs import Run, RunInfo, write_json_atomic
from foxcast.store.archive import SourceArchive
from foxcast.store.pit import FactStore

MAX_CLAIMS_PER_RUN: Final = 300
HOLDINGS: Final = {
    "holds": Holding.HOLDS,
    "none": Holding.NONE,
    "not-declared": Holding.NOT_DECLARED,
}
UNTRUSTED_RESULTS: Final = ("results[].untrusted",)
# The rendered text of shown claims may quote long passages. Past this budget a result
# leaves the text out (the verdict stays), so a big batch never turns into a size error
# after its claims were already kept.
SHOWN_TEXT_BUDGET: Final = 40_000


@dataclass(frozen=True)
class Record:
    """The store and archive the tools read, and where dossiers are written."""

    store: FactStore
    archive: SourceArchive
    dossier_dir: Path


def _as_of(info: RunInfo) -> date:
    return date.fromisoformat(info.as_of)


def _open(run: Run) -> None:
    if run.rendered:
        raise ToolError(
            ErrorCode.RUN_CLOSED,
            "this run's dossier is rendered; nothing more can be added to it",
        )


# --- facts_derive ---


def derive_fact(record: Record, run: Run, args: FactsDerive) -> Outcome:
    _open(run)
    info = run.info
    view = record.store.at(_as_of(info))
    found = view.get_many([args.a, args.b])
    inputs: list[Fact | DerivedFact] = []
    for fact_id in (args.a, args.b):
        item = found.get(fact_id)
        if not isinstance(item, Fact | DerivedFact):
            raise ToolError(
                ErrorCode.NOT_FOUND,
                f"{fact_id} is not a numeric fact in the record as of {info.as_of}",
            )
        if item.cik != info.cik:
            raise ToolError(ErrorCode.INVALID_INPUT, f"{fact_id} is about another company")
        inputs.append(item)
    try:
        fact = derive(Method(args.method), inputs[0], inputs[1])
        record.store.add_derived([fact])
    except ValueError as error:
        raise ToolError(ErrorCode.INVALID_INPUT, f"cannot derive: {error}") from error
    return Outcome({"fact": number_json(fact)})


# --- dossier_submit_claims ---


def _submitted(raw: dict[str, Any]) -> SubmittedClaim:
    return SubmittedClaim(
        section=raw["section"],
        type=ClaimType(raw["type"]),
        text=raw["text"],
        cites=tuple(raw["cites"]),
        probability=raw["probability"],
    )


def _stored(claim: SubmittedClaim) -> dict[str, Any]:
    return {
        "claim_id": claim_id_of(claim),
        "section": claim.section,
        "type": str(claim.type),
        "text": claim.text,
        "cites": list(claim.cites),
        "probability": claim.probability,
    }


def submit_claims(record: Record, run: Run, args: DossierSubmitClaims) -> Outcome:
    _open(run)
    info = run.info
    claims = [
        SubmittedClaim(c.section, c.type, c.text, tuple(c.cites), c.probability)
        for c in args.claims
    ]
    stored = [_stored(c) for c in claims]
    already = len(run.claims())
    fresh = len({s["claim_id"] for s in stored} - {c["claim_id"] for c in run.claims()})
    if already + fresh > MAX_CLAIMS_PER_RUN:
        raise ToolError(
            ErrorCode.LIMIT,
            f"a run takes at most {MAX_CLAIMS_PER_RUN} claims; {already} are already submitted",
        )
    added = run.add_claims(stored)
    view = record.store.at(_as_of(info))
    context = CheckContext(view=view, archive=record.archive, subject_cik=info.cik, companies={})
    check = check_dossier(review_claims(claims, view), context)
    shown = {c.claim_id: c for c in check.shown}
    withheld = {w.claim_id: w for w in check.withheld}
    results = []
    budget = SHOWN_TEXT_BUDGET
    for claim, data, new in zip(claims, stored, added, strict=True):
        claim_id = data["claim_id"]
        entry: dict[str, Any] = {"claim_id": claim_id, "new": new, "section": claim.section}
        if claim_id in shown:
            text = shown[claim_id].text
            budget -= len(canonical(text))
            entry |= {"verdict": "shown", "reasons": []}
            if budget >= 0:
                entry["untrusted"] = {"text": text}
            else:
                entry["text_omitted"] = "the result size limit; the dossier shows it"
        else:
            entry |= {"verdict": "withheld", "reasons": list(withheld[claim_id].reasons)}
        results.append(entry)
    total = len(run.claims())
    return Outcome(
        {
            "results": results,
            "submitted_total": total,
            "claims_left": MAX_CLAIMS_PER_RUN - total,
        },
        untrusted=UNTRUSTED_RESULTS,
    )


# --- dossier_render ---


def render_dossier(
    record: Record, run: Run, *, built_at: datetime, cost_before: RunCost
) -> Outcome:
    """Build, check and write the dossier from every claim submitted; close the run."""
    existing = run.record()
    if existing is not None:
        return Outcome({"already_rendered": True, **existing["dossier"]})
    info = run.info
    claims = [_submitted(c) for c in run.claims()]
    request = DossierRequest(
        ticker=info.ticker,
        cik=info.cik,
        as_of=_as_of(info),
        built_at=built_at,
        company=info.company,
        model=ModelDeclaration(info.model_name, info.model_cutoff),
        prompt_sha256=info.prompt_sha256,
        holding=HOLDINGS[info.holding],
        cost=cost_before,
        code_commit=info.code_commit,
    )
    dossier = build_dossier(request, record.store, record.archive, claims=claims, companies={})
    md_path, json_path = write_dossier(dossier, record.dossier_dir)
    c = dossier.certification
    summary = {
        "markdown_file": md_path.name,
        "json_file": json_path.name,
        "markdown_sha256": sha256_hex(render_markdown(dossier).encode("utf-8")),
        "json_sha256": sha256_hex(render_json(dossier).encode("utf-8")),
        "built_at": built_at.isoformat(),
        "sections_filled": sum(1 for s in dossier.sections if s.status is Status.FILLED),
        "sections_unavailable": sum(1 for s in dossier.sections if s.status is Status.UNAVAILABLE),
        "claims_counted": c.claims_counted,
        "claims_verified": c.claims_verified,
        "claims_withheld": c.claims_withheld,
        "items_shown": c.items_shown,
        "cost": {
            "tool_calls": cost_before.tool_calls,
            "result_bytes": cost_before.result_bytes,
            "wall_seconds": cost_before.wall_seconds,
        },
    }
    write_json_atomic(run.record_path, {"run": info.run_id, "dossier": summary})
    return Outcome({"already_rendered": False, **summary})


__all__ = ["Record", "derive_fact", "render_dossier", "render_item", "submit_claims"]
