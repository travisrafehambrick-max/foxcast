"""Write a dossier as Markdown for people and JSON for programs (brief 2.1).

Every value shown came from the record or from fixed text, and third-party text (filing
passages, insider names, the model's words around them) is **data**: in Markdown each
string is escaped and its white space collapsed, so a filing cannot add a heading, a link,
an image, HTML or a table cell to the dossier (carry-forward from the 1A.4 review).

Both writers refuse a dossier that would show a fact-type claim without a verified
citation (row 1a), so the run fails rather than print one.
"""

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

from foxcast.claims.checker import row_1a_failures
from foxcast.claims.render import NEEDS_PROBABILITY, RenderedClaim
from foxcast.dossier.schema import (
    CLAIM_TARGETS,
    SPEC_BY_KEY,
    Cell,
    Dossier,
    ResearchClaims,
    ResearchStatus,
    Section,
    Status,
    Table,
)
from foxcast.facts.record import is_hidden_character
from foxcast.notice import NOTICE_HEADLINE

# /2 adds "research_claims" (the Stage 1B research-claims section).
SCHEMA_VERSION: Final = "foxcast.dossier/2"
# Backslash-escapable ASCII punctuation that can start markup inline. ":" and "." stop
# bare-URL and www autolinks, "." also an ordered-list marker such as "1." after a bullet,
# and "@" email autolinks (best effort: GFM finds those after parsing).
MARKDOWN_SPECIAL: Final = frozenset("\\`*_[]<>|$~&!#{}():.@")
LEADING_MARKERS: Final = frozenset("-+=")
TICKER: Final = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,9}$")
ITEM_ID: Final = re.compile(r"^[fpc]_[0-9a-f]{16}$")
SIDE_HEADINGS: Final = {"bull_case": "Bull case", "bear_case": "Bear case"}


class UnverifiedClaimError(RuntimeError):
    """A fact-type claim would be shown without a verified citation (row 1a)."""


def require_row_1a(dossier: Dossier) -> None:
    shown = [claim for section in dossier.sections for claim in section.claims]
    failures = row_1a_failures(shown, verified_ids=set(dossier.verified_claim_ids))
    if failures:
        raise UnverifiedClaimError(f"fact claims without a verified citation: {failures}")


# --- Markdown ---


def escape(text: str) -> str:
    """Plain text that Markdown shows exactly as written, on one line."""
    visible = "".join(c for c in text if not is_hidden_character(c))
    flat = " ".join(visible.split())
    escaped = "".join("\\" + c if c in MARKDOWN_SPECIAL else c for c in flat)
    return "\\" + escaped if escaped[:1] in LEADING_MARKERS else escaped


def _ids(cites: Iterable[str]) -> str:
    unique = list(dict.fromkeys(cites))
    return " ".join(f"`{i}`" for i in unique)  # IDs are [a-z0-9_] only


def _cell(cell: Cell) -> str:
    if ITEM_ID.fullmatch(cell.text):
        return f"`{cell.text}`"
    return escape(cell.text)


def _table(table: Table) -> list[str]:
    has_cites = any(cell.cites for row in table.rows for cell in row)
    columns = [escape(c) for c in table.columns] + (["Fact IDs"] if has_cites else [])
    out = [
        f"**{escape(table.title)}**",
        "",
        "| " + " | ".join(columns) + " |",
        "|" + "---|" * len(columns),
    ]
    for row in table.rows:
        cells = [_cell(c) for c in row]
        if has_cites:
            cells.append(_ids(i for c in row for i in c.cites))
        out.append("| " + " | ".join(cells) + " |")
    return [*out, ""]


def claim_tag(claim: RenderedClaim) -> str:
    """The code-set labels shown after a claim: type, likelihood, confidence and grade."""
    parts = [str(claim.type)]
    if claim.type in NEEDS_PROBABILITY and claim.probability is not None:
        parts.append(f"{claim.likelihood} (model estimate {claim.probability * 100:.0f}%)")
    parts.append(f"confidence {claim.confidence}")
    parts.append(f"grade {claim.reliability}{claim.credibility}")
    return " · ".join(parts)


def _claims(section: Section) -> list[str]:
    out: list[str] = []
    groups = (
        [(SIDE_HEADINGS[k], [c for c in section.claims if c.section == k]) for k in SIDE_HEADINGS]
        if section.key == "bull_bear"
        else [("Claims", list(section.claims))]
    )
    for heading, claims in groups:
        if not claims:
            continue
        out += [f"**{heading}**", ""]
        out += [f"- {escape(c.text)} \\[{escape(claim_tag(c))}\\] `{c.claim_id}`" for c in claims]
        out.append("")
    return out


def _section(section: Section) -> list[str]:
    out = [f"## {section.number}. {escape(section.title)}", ""]
    if section.status is Status.UNAVAILABLE:
        return [*out, f"**Unavailable:** {escape(section.reason or '')}", ""]
    if section.lines:
        out += ["| | |", "|---|---|"]
        for line in section.lines:
            ids = f" {_ids(line.value.cites)}" if line.value.cites else ""
            out.append(f"| {escape(line.label)} | {_cell(line.value)}{ids} |")
        out.append("")
    for table in section.tables:
        out += _table(table)
    out += _claims(section)
    if section.gaps:
        out += ["**Not available:**", ""]
        out += [f"- {escape(g.item)}: {escape(g.reason)}" for g in section.gaps]
        out.append("")
    return out


def certification_line(dossier: Dossier) -> str:
    c = dossier.certification
    model = (
        f"model {c.model} (self-reported), stated training cutoff {c.stated_cutoff} "
        f"(self-reported), prompt hash {c.prompt_sha256 or 'not recorded'}"
        if c.model
        else "no model (built without a research step)"
    )
    return (
        f"Certified by foxcast {c.code_version}: {model}; {c.claims_verified} of "
        f"{c.claims_counted} cited claims verified and shown, {c.claims_withheld} withheld; "
        f"{c.items_shown} facts and passages shown, each listed in section 18; "
        f"words check {c.words_check_version}."
    )


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def research_block(research: ResearchClaims) -> list[str]:
    """The research-claims section: an index of the claims, never the model's words."""
    if research.status is ResearchStatus.UNAVAILABLE:
        return [f"**Research claims.** Unavailable: {escape(research.reason or '')}", ""]
    written = f"Written by the research step: model {research.model} (self-reported)"
    if not (research.shown_count or research.withheld_count):
        nothing = f"{written}; the research step submitted no claims."
        return [f"**Research claims.** {escape(nothing)}", ""]
    summary = (
        f"{written}; {_plural(research.shown_count, 'claim')} shown in its section below, "
        f"{research.withheld_count} withheld and listed in section 18."
    )
    out = [
        f"**Research claims.** {escape(summary)}",
        "",
        "| Claim section | Dossier section | Shown | Withheld | Claim IDs shown |",
        "|---|---|---|---|---|",
    ]
    for t in research.targets:
        title = SPEC_BY_KEY[CLAIM_TARGETS[t.target]].title
        ids = _ids(t.shown) or "none"
        out.append(
            f"| {escape(t.target)} | {escape(f'{t.section_number}. {title}')} "
            f"| {len(t.shown)} | {t.withheld} | {ids} |"
        )
    out.append("")
    if research.withheld_elsewhere:
        claims = _plural(research.withheld_elsewhere, "claim")
        verb = "was" if research.withheld_elsewhere == 1 else "were"
        line = f"{claims} named a section that takes no claims and {verb} withheld; see section 18."
        out += [escape(line), ""]
    return out


def render_markdown(dossier: Dossier) -> str:
    require_row_1a(dossier)
    out = [
        f"# {escape(dossier.ticker)} dossier as of {dossier.as_of.isoformat()}",
        "",
        f"> {escape(NOTICE_HEADLINE)}",
        "",
        f"**Certification.** {escape(certification_line(dossier))}",
        "",
        *research_block(dossier.research),
    ]
    for section in dossier.sections:
        out += _section(section)
    return "\n".join(out).rstrip("\n") + "\n"


# --- JSON ---


def _cell_json(cell: Cell) -> dict[str, Any]:
    return {"text": cell.text, "cites": list(cell.cites)}


def _claim_json(claim: RenderedClaim) -> dict[str, Any]:
    return {
        "claim_id": claim.claim_id,
        "section": claim.section,
        "type": str(claim.type),
        "text": claim.text,
        "code_spans": [list(span) for span in claim.code_spans],
        "cites": list(claim.cites),
        "model_estimate": claim.probability,
        "likelihood": claim.likelihood,
        "confidence": str(claim.confidence),
        "reliability": str(claim.reliability),
        "credibility": str(claim.credibility),
    }


def _section_json(section: Section) -> dict[str, Any]:
    return {
        "number": section.number,
        "key": section.key,
        "title": section.title,
        "status": str(section.status),
        "reason": section.reason,
        "lines": [{"label": ln.label, "value": _cell_json(ln.value)} for ln in section.lines],
        "tables": [
            {
                "title": t.title,
                "columns": list(t.columns),
                "rows": [[_cell_json(c) for c in row] for row in t.rows],
            }
            for t in section.tables
        ],
        "claims": [_claim_json(c) for c in section.claims],
        "gaps": [{"item": g.item, "reason": g.reason} for g in section.gaps],
    }


def research_json(research: ResearchClaims) -> dict[str, Any]:
    return {
        "status": str(research.status),
        "reason": research.reason,
        "model": research.model,
        "model_self_reported": research.model is not None,
        "targets": [
            {
                "target": t.target,
                "section": t.section_number,
                "shown": list(t.shown),
                "withheld": t.withheld,
            }
            for t in research.targets
        ],
        "withheld_elsewhere": research.withheld_elsewhere,
    }


def to_json_data(dossier: Dossier) -> dict[str, Any]:
    require_row_1a(dossier)
    c = dossier.certification
    return {
        "schema": SCHEMA_VERSION,
        "ticker": dossier.ticker,
        "cik": dossier.cik,
        "company": dossier.company,
        "as_of": dossier.as_of.isoformat(),
        "built_at": dossier.built_at.isoformat(),
        "certification": {
            "line": certification_line(dossier),
            "model": c.model,
            "model_self_reported": c.model is not None,
            "stated_cutoff": c.stated_cutoff,
            "prompt_sha256": c.prompt_sha256,
            "claims_counted": c.claims_counted,
            "claims_verified": c.claims_verified,
            "claims_withheld": c.claims_withheld,
            "items_shown": c.items_shown,
            "words_check_version": c.words_check_version,
            "code_version": c.code_version,
        },
        "research_claims": research_json(dossier.research),
        "sections": [_section_json(s) for s in dossier.sections],
        "withheld": [
            {
                "claim_id": w.claim_id,
                "section": w.section,
                "type": w.type,
                "reasons": list(w.reasons),
            }
            for w in dossier.withheld
        ],
    }


def render_json(dossier: Dossier) -> str:
    return json.dumps(to_json_data(dossier), indent=2, ensure_ascii=False) + "\n"


def write_dossier(dossier: Dossier, folder: Path) -> tuple[Path, Path]:
    """Write ``<TICKER>-<as of>.md`` and ``.json`` into ``folder``; return both paths."""
    if not TICKER.fullmatch(dossier.ticker):
        raise ValueError(f"not a ticker that can name a file: {dossier.ticker!r}")
    markdown, data = render_markdown(dossier), render_json(dossier)
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{dossier.ticker}-{dossier.as_of.isoformat()}"
    md_path, json_path = folder / f"{stem}.md", folder / f"{stem}.json"
    staged = [
        (md_path.with_name(md_path.name + ".tmp"), md_path, markdown),
        (json_path.with_name(json_path.name + ".tmp"), json_path, data),
    ]
    for temporary, _, text in staged:  # write both before either replaces a final file
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    for temporary, final, _ in staged:
        temporary.replace(final)
    return md_path, json_path
