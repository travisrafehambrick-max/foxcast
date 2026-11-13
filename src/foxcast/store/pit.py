"""The point-in-time fact store (brief F3, scorecard row 2d).

Facts live in DuckDB, one table per kind: numbers, text values, passages and derived
facts. Nothing is read without a date:

- every ``*_as_of`` query returns only rows whose ``public_on`` is on or before the date;
- cited IDs are resolved through :meth:`FactStore.at`, a view that cannot see anything
  made public after its date, so a dossier cannot cite a fact from its own future.

Adding is idempotent: an ID that is already stored with the same content is skipped. The
same ID with different content is refused, never silently dropped. A derived fact is
stored only if it rebuilds exactly from inputs already in the store and public by its own
``public_on``.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import astuple, dataclass, fields, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

import duckdb

from foxcast.facts.derived import Basis, DerivedFact, Method, recompute
from foxcast.facts.record import Fact, Passage, TextFact

PROVENANCE_DDL: Final = """
    public_on      DATE NOT NULL,
    fetched_at     VARCHAR NOT NULL,  -- ISO 8601 with its UTC offset
    source_url     VARCHAR NOT NULL,
    content_sha256 VARCHAR NOT NULL"""

SCHEMA: Final = f"""
CREATE TABLE IF NOT EXISTS facts (
    fact_id        VARCHAR PRIMARY KEY,
    cik            BIGINT NOT NULL,
    field          VARCHAR NOT NULL,
    unit           VARCHAR NOT NULL,
    period_start   DATE,
    period_end     DATE NOT NULL,
    value          DOUBLE NOT NULL,
    {PROVENANCE_DDL},
    accession      VARCHAR,
    fiscal_year    INTEGER,
    fiscal_period  VARCHAR
);
CREATE TABLE IF NOT EXISTS text_facts (
    fact_id        VARCHAR PRIMARY KEY,
    cik            BIGINT NOT NULL,
    field          VARCHAR NOT NULL,
    value          VARCHAR NOT NULL,
    period_end     DATE,
    {PROVENANCE_DDL},
    accession      VARCHAR
);
CREATE TABLE IF NOT EXISTS passages (
    passage_id     VARCHAR PRIMARY KEY,
    cik            BIGINT NOT NULL,
    accession      VARCHAR NOT NULL,
    form           VARCHAR NOT NULL,
    section        VARCHAR NOT NULL,
    "start"        INTEGER NOT NULL,
    "end"          INTEGER NOT NULL,
    text           VARCHAR NOT NULL,
    text_sha256    VARCHAR NOT NULL,
    {PROVENANCE_DDL}
);
CREATE TABLE IF NOT EXISTS derived_facts (
    fact_id        VARCHAR PRIMARY KEY,
    cik            BIGINT NOT NULL,
    field          VARCHAR NOT NULL,
    method         VARCHAR NOT NULL,
    basis          VARCHAR NOT NULL,
    inputs         VARCHAR[] NOT NULL,
    unit           VARCHAR NOT NULL,
    period_start   DATE,
    period_end     DATE NOT NULL,
    value          DOUBLE NOT NULL,
    public_on      DATE NOT NULL
);
"""
# Columns added after a table was first made; a store made before them gains them on open.
MIGRATIONS: Final = """
ALTER TABLE facts ADD COLUMN IF NOT EXISTS fiscal_year INTEGER;
ALTER TABLE facts ADD COLUMN IF NOT EXISTS fiscal_period VARCHAR;
"""

# DuckDB hands rows back untyped; each builder below restores the record's own types.
Row = tuple[Any, ...]
AnyFact = Fact | TextFact | DerivedFact


class StoreConflictError(ValueError):
    """An ID already stored with different content."""


@dataclass(frozen=True)
class DocumentSummary:
    """An archived filing document, summarised from its passages."""

    accession: str
    form: str
    public_on: date
    passages: int
    fetched_at: datetime
    source_url: str
    content_sha256: str


def _parse_fetched_at[T: (Fact, TextFact, Passage)](cls: type[T]) -> Callable[[Row], T]:
    index = [f.name for f in fields(cls)].index("fetched_at")

    def build(row: Row) -> T:
        return replace(cls(*row), fetched_at=datetime.fromisoformat(str(row[index])))

    return build


def _derived_from_row(row: Row) -> DerivedFact:
    fact = DerivedFact(*row)
    return replace(
        fact,
        method=Method(str(fact.method)),
        basis=Basis(str(fact.basis)),
        inputs=tuple(fact.inputs),
    )


def _same_content(stored: Any, incoming: Any) -> bool:
    """Equal apart from when it was fetched: a re-fetch of the same content is not news."""
    if hasattr(stored, "fetched_at") and hasattr(incoming, "fetched_at"):
        stored = replace(stored, fetched_at=incoming.fetched_at)
    return bool(stored == incoming)


def _adds_fiscal_label(stored: Any, incoming: Any) -> bool:
    """A fact stored before fiscal labels existed, arriving again with its label.

    The label is filled in; nothing else may differ, and a stored label never changes.
    """
    if not isinstance(stored, Fact) or not isinstance(incoming, Fact):
        return False
    if stored.fiscal_year is not None or stored.fiscal_period is not None:
        return False
    unlabelled = replace(incoming, fiscal_year=None, fiscal_period=None)
    return _same_content(stored, unlabelled)


@dataclass(frozen=True)
class _Table[T]:
    """One table. ``key`` and the column names come from constants, never from input."""

    name: str
    record: type[T]
    key: str
    from_row: Callable[[Row], T]

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(f.name for f in fields(self.record))  # type: ignore[arg-type]

    @property
    def column_list(self) -> str:
        return ", ".join(f'"{c}"' for c in self.columns)

    def key_of(self, item: T) -> str:
        return str(getattr(item, self.key))

    def to_row(self, item: T) -> Row:
        row = list(astuple(item))  # type: ignore[call-overload]
        for i, value in enumerate(row):
            if isinstance(value, datetime):
                row[i] = value.isoformat()
            elif isinstance(value, Method | Basis):
                row[i] = str(value)
            elif isinstance(value, tuple):
                row[i] = list(value)
        return tuple(row)


FACTS: Final = _Table("facts", Fact, "fact_id", _parse_fetched_at(Fact))
TEXT_FACTS: Final = _Table("text_facts", TextFact, "fact_id", _parse_fetched_at(TextFact))
PASSAGES: Final = _Table("passages", Passage, "passage_id", _parse_fetched_at(Passage))
DERIVED_FACTS: Final = _Table("derived_facts", DerivedFact, "fact_id", _derived_from_row)

COLUMNS: Final = FACTS.columns


class FactStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        with duckdb.connect(str(path)) as connection:
            connection.execute(SCHEMA)
            connection.execute(MIGRATIONS)

    # --- adding ---

    def _add[T](self, table: _Table[T], items: Sequence[T]) -> int:
        incoming: dict[str, T] = {}
        for item in items:
            key = table.key_of(item)
            if key in incoming and not _same_content(incoming[key], item):
                raise StoreConflictError(f"{table.name}: two different records for {key}")
            incoming.setdefault(key, item)
        if not incoming:
            return 0
        # Check and insert in one transaction, so a concurrent writer cannot slip between.
        key_filter = f'"{table.key}" IN (SELECT unnest(?::VARCHAR[]))'
        with duckdb.connect(str(self._path)) as connection:
            connection.begin()
            rows = connection.execute(
                f"SELECT {table.column_list} FROM {table.name} WHERE {key_filter}",  # noqa: S608
                [list(incoming)],
            ).fetchall()
            stored = {table.key_of(s): s for s in map(table.from_row, rows)}
            for key, item in incoming.items():
                if key not in stored or _same_content(stored[key], item):
                    continue
                if _adds_fiscal_label(stored[key], item):
                    connection.execute(
                        "UPDATE facts SET fiscal_year = ?, fiscal_period = ? WHERE fact_id = ?",
                        [item.fiscal_year, item.fiscal_period, key],  # type: ignore[attr-defined]
                    )
                    continue
                connection.rollback()
                raise StoreConflictError(f"{table.name}: {key} is stored with other content")
            new = [item for key, item in incoming.items() if key not in stored]
            if new:
                placeholders = ", ".join("?" for _ in table.columns)
                connection.executemany(
                    f"INSERT INTO {table.name} ({table.column_list}) "  # noqa: S608
                    f"VALUES ({placeholders})",
                    [table.to_row(item) for item in new],
                )
            connection.commit()
        return len(new)

    def add(self, facts: Sequence[Fact]) -> int:
        """Store new numeric facts; return how many were new."""
        return self._add(FACTS, facts)

    def add_text(self, facts: Sequence[TextFact]) -> int:
        """Store new text facts; return how many were new."""
        return self._add(TEXT_FACTS, facts)

    def add_passages(self, passages: Sequence[Passage]) -> int:
        """Store new passages; return how many were new."""
        return self._add(PASSAGES, passages)

    def add_derived(self, facts: Sequence[DerivedFact]) -> int:
        """Store derived facts that rebuild exactly from inputs public by their own date.

        Facts in the same call may build on each other, in order.
        """
        accepted: dict[str, DerivedFact] = {}
        for fact in facts:
            recompute(fact, _Chained(accepted, self.at(fact.public_on)))
            accepted[fact.fact_id] = fact
        return self._add(DERIVED_FACTS, list(accepted.values()))

    # --- reading ---

    def _select[T](
        self, table: _Table[T], where: str, params: Sequence[object], order: str
    ) -> list[T]:
        """``where`` and ``order`` are SQL written in this module, never from input."""
        with duckdb.connect(str(self._path), read_only=True) as connection:
            rows = connection.execute(
                f"SELECT {table.column_list} FROM {table.name} "  # noqa: S608
                f"WHERE {where} ORDER BY {order}",
                list(params),
            ).fetchall()
        return [table.from_row(row) for row in rows]

    def by_keys[T](self, table: _Table[T], keys: list[str], as_of: date | None) -> list[T]:
        """Rows by ID in one query; with ``as_of``, only rows public by then."""
        where = f'"{table.key}" IN (SELECT unnest(?::VARCHAR[]))'
        params: list[object] = [keys]
        if as_of is not None:
            where += " AND public_on <= ?"
            params.append(as_of)
        return self._select(table, where, params, f'"{table.key}"')

    def as_of(self, *, cik: int, field: str, as_of: date) -> list[Fact]:
        """Numeric facts about one company and field that were public on ``as_of``."""
        return self._select(
            FACTS,
            "cik = ? AND field = ? AND public_on <= ?",
            [cik, field, as_of],
            "period_end, public_on, fact_id",
        )

    def text_as_of(self, *, cik: int, field: str, as_of: date) -> list[TextFact]:
        """Text facts about one company and field that were public on ``as_of``."""
        return self._select(
            TEXT_FACTS,
            "cik = ? AND field = ? AND public_on <= ?",
            [cik, field, as_of],
            "period_end NULLS FIRST, public_on, fact_id",
        )

    def passages_as_of(self, *, cik: int, accession: str, as_of: date) -> list[Passage]:
        """One filing's passages, in document order, if the filing was public on ``as_of``."""
        return self._select(
            PASSAGES,
            "cik = ? AND accession = ? AND public_on <= ?",
            [cik, accession, as_of],
            '"start", passage_id',
        )

    def derived_as_of(self, *, cik: int, as_of: date) -> list[DerivedFact]:
        """Derived facts about one company that were public on ``as_of``."""
        return self._select(
            DERIVED_FACTS,
            "cik = ? AND public_on <= ?",
            [cik, as_of],
            "period_end, field, fact_id",
        )

    # --- whole-company reads, for building a dossier ---

    def numbers_for(self, *, cik: int, field_prefix: str, as_of: date) -> list[Fact]:
        """Numeric facts about one company whose field starts with ``field_prefix``."""
        return self._select(
            FACTS,
            "cik = ? AND starts_with(field, ?) AND public_on <= ?",
            [cik, field_prefix, as_of],
            "field, period_end, public_on, fact_id",
        )

    def texts_for(self, *, cik: int, field_prefix: str, as_of: date) -> list[TextFact]:
        """Text facts about one company whose field starts with ``field_prefix``."""
        return self._select(
            TEXT_FACTS,
            "cik = ? AND starts_with(field, ?) AND public_on <= ?",
            [cik, field_prefix, as_of],
            "field, period_end NULLS FIRST, public_on, fact_id",
        )

    def documents_for(self, *, cik: int, as_of: date) -> list[DocumentSummary]:
        """One line per archived filing document with passages public on ``as_of``."""
        with duckdb.connect(str(self._path), read_only=True) as connection:
            rows = connection.execute(
                "SELECT accession, form, min(public_on), count(*), max(fetched_at), "
                "min(source_url), min(content_sha256) FROM passages "
                "WHERE cik = ? AND public_on <= ? GROUP BY accession, form "
                "ORDER BY min(public_on) DESC, accession",
                [cik, as_of],
            ).fetchall()
        return [
            DocumentSummary(a, f, p, int(n), datetime.fromisoformat(t), u, h)
            for a, f, p, n, t, u, h in rows
        ]

    def at(self, as_of: date) -> "FactView":
        """A view that resolves IDs, but only for facts public on ``as_of``."""
        return FactView(self, as_of)


class FactView:
    """Resolve cited IDs as of one date. Anything made public later looks absent.

    Records found are cached: a stored ID never changes content (conflicts are refused),
    so a hit stays true. Misses are not cached, so a fact added later is still found.
    """

    def __init__(self, store: FactStore, as_of: date) -> None:
        self._store = store
        self.as_of = as_of
        self._facts: dict[str, AnyFact] = {}
        self._passages: dict[str, Passage] = {}

    def get_many(self, fact_ids: Iterable[str]) -> dict[str, AnyFact]:
        wanted = set(fact_ids)
        missing = sorted(wanted - self._facts.keys())
        if missing:
            numeric = self._store.by_keys(FACTS, missing, self.as_of)
            self._remember(numeric)
            missing = [i for i in missing if i not in self._facts]
        if missing:
            derived = self._store.by_keys(DERIVED_FACTS, missing, self.as_of)
            self._remember(derived)
            missing = [i for i in missing if i not in self._facts]
        if missing:
            text = self._store.by_keys(TEXT_FACTS, missing, self.as_of)
            self._remember(text)
        return {i: self._facts[i] for i in sorted(wanted) if i in self._facts}

    def _remember(self, rows: Sequence[Fact] | Sequence[DerivedFact] | Sequence[TextFact]) -> None:
        self._facts.update({row.fact_id: row for row in rows})

    def get(self, fact_id: str, /) -> AnyFact | None:
        return self.get_many([fact_id]).get(fact_id)

    def get_passages(self, passage_ids: Iterable[str]) -> dict[str, Passage]:
        wanted = set(passage_ids)
        missing = sorted(wanted - self._passages.keys())
        if missing:
            rows = self._store.by_keys(PASSAGES, missing, self.as_of)
            self._passages.update({p.passage_id: p for p in rows})
        return {i: self._passages[i] for i in sorted(wanted) if i in self._passages}

    def get_passage(self, passage_id: str) -> Passage | None:
        return self.get_passages([passage_id]).get(passage_id)


class _Chained:
    """Look in a batch being added first, then in the store's view."""

    def __init__(self, batch: Mapping[str, DerivedFact], view: FactView) -> None:
        self._batch = batch
        self._view = view

    def get(self, fact_id: str, /) -> AnyFact | None:
        found = self._batch.get(fact_id)
        if found is not None and found.public_on <= self._view.as_of:
            return found
        return self._view.get(fact_id)
