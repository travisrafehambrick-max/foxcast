"""Section 18: every fact and passage the dossier shows, and every claim it withheld.

Each cited ID is resolved through the dossier's as-of view and listed with its source,
fetch time, content hash and grade (brief 2.1). A derived fact is listed with the inputs
it was computed from, so every shown figure traces to a fetched source.
"""

from collections.abc import Iterable, Sequence

from foxcast.claims.render import Cited, source_grade
from foxcast.dossier.content import metric_of
from foxcast.dossier.schema import Cell, Section, Table, WithheldEntry, fill_or_unavailable
from foxcast.facts.derived import DerivedFact
from foxcast.facts.record import Passage, TextFact
from foxcast.store.pit import FactView


class UnresolvedCitationError(ValueError):
    """A shown ID that the dossier's as-of view cannot find. Never shown unsourced."""


def cited_ids(sections: Iterable[Section]) -> set[str]:
    ids: set[str] = set()
    for section in sections:
        for line in section.lines:
            ids.update(line.value.cites)
        for table in section.tables:
            for row in table.rows:
                for cell in row:
                    ids.update(cell.cites)
        for claim in section.claims:
            ids.update(claim.cites)
    return ids


def _resolve(ids: set[str], view: FactView) -> dict[str, Cited]:
    found: dict[str, Cited] = {
        **view.get_many(i for i in ids if i.startswith("f_")),
        **view.get_passages(i for i in ids if i.startswith("p_")),
    }
    # Add the inputs of derived facts, and theirs, until nothing new appears.
    pending = [f for f in found.values() if isinstance(f, DerivedFact)]
    while pending:
        inputs = {i for fact in pending for i in fact.inputs if i not in found}
        more = view.get_many(inputs)
        found.update(more)
        pending = [f for f in more.values() if isinstance(f, DerivedFact)]
    missing = sorted(ids - found.keys())
    if missing:
        raise UnresolvedCitationError(f"not in the record as of {view.as_of}: {missing}")
    return found


def describe(item: Cited) -> str:
    if isinstance(item, Passage):
        return (
            f"{item.form} {item.accession}, {item.section}, characters {item.start} to {item.end}"
        )
    if isinstance(item, DerivedFact):
        return f"{item.method} of {', '.join(item.inputs)}"
    if isinstance(item, TextFact):
        day = item.period_end.isoformat() if item.period_end else "undated"
        return f"{metric_of(item.field)}, {day}, {item.accession or 'no accession'}"
    period = (
        f"at {item.period_end.isoformat()}"
        if item.period_start is None
        else f"{item.period_start.isoformat()} to {item.period_end.isoformat()}"
    )
    return f"{metric_of(item.field)}, {period}"


def _source_row(item_id: str, item: Cited, view: FactView) -> tuple[Cell, ...]:
    reliability, credibility = source_grade(item, view)
    grade = Cell(f"{reliability}{credibility}")
    if isinstance(item, DerivedFact):
        provenance = (Cell("computed by foxcast"), Cell("not fetched"), Cell("see its inputs"))
    else:
        provenance = (
            Cell(item.source_url),
            Cell(item.fetched_at.isoformat()),
            Cell(item.content_sha256),
        )
    return (Cell(item_id), Cell(describe(item)), *provenance, grade)


def _withheld_table(withheld: Sequence[WithheldEntry]) -> Table | None:
    if not withheld:
        return None
    rows = tuple(
        (Cell(w.claim_id), Cell(w.section), Cell(w.type), Cell("; ".join(w.reasons)))
        for w in withheld
    )
    return Table(
        "Withheld claims (not shown; the model's words are not reproduced)",
        ("Claim", "Section", "Type", "Reasons"),
        rows,
    )


def sources_section(
    body: Sequence[Section], withheld: Sequence[WithheldEntry], view: FactView
) -> tuple[Section, int]:
    """Section 18, and the number of facts and passages it lists."""
    found = _resolve(cited_ids(body), view)
    tables = []
    if found:
        rows = tuple(_source_row(i, found[i], view) for i in sorted(found))
        columns = ("ID", "Item", "Source", "Fetched at", "Content SHA-256", "Grade")
        tables.append(Table("Facts and passages shown, with their inputs", columns, rows))
    withheld_table = _withheld_table(withheld)
    if withheld_table:
        tables.append(withheld_table)
    section = fill_or_unavailable(
        "sources",
        "nothing in this dossier cites a fact or passage, and no claim was withheld",
        tables=tuple(tables),
    )
    return section, len(found)
