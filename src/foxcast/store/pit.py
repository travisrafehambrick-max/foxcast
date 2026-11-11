"""The point-in-time fact store (brief F3, scorecard row 2d).

Facts live in DuckDB. ``as_of`` returns only facts whose ``public_on`` is on or before the
query date, so no query can see something the public did not yet know. Adding facts is
idempotent: a fact ID that is already stored is skipped.
"""

from dataclasses import astuple, fields, replace
from datetime import date, datetime
from pathlib import Path
from typing import Final

import duckdb

from foxcast.facts.record import Fact

COLUMNS: Final = tuple(f.name for f in fields(Fact))

SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS facts (
    fact_id        VARCHAR PRIMARY KEY,
    cik            BIGINT NOT NULL,
    field          VARCHAR NOT NULL,
    unit           VARCHAR NOT NULL,
    period_start   DATE,
    period_end     DATE NOT NULL,
    value          DOUBLE NOT NULL,
    public_on      DATE NOT NULL,
    fetched_at     VARCHAR NOT NULL,  -- ISO 8601 with offset, UTC
    source_url     VARCHAR NOT NULL,
    content_sha256 VARCHAR NOT NULL,
    accession      VARCHAR
)
"""


FETCHED_AT_INDEX: Final = COLUMNS.index("fetched_at")


def _to_row(fact: Fact) -> tuple[object, ...]:
    row = list(astuple(fact))
    row[FETCHED_AT_INDEX] = fact.fetched_at.isoformat()
    return tuple(row)


def _from_row(row: tuple[object, ...]) -> Fact:
    fact = Fact(*row)  # type: ignore[arg-type]
    return replace(fact, fetched_at=datetime.fromisoformat(str(row[FETCHED_AT_INDEX])))


class FactStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        with duckdb.connect(str(path)) as connection:
            connection.execute(SCHEMA)

    def add(self, facts: list[Fact]) -> int:
        """Store new facts; return how many were new."""
        if not facts:
            return 0
        placeholders = ", ".join("?" for _ in COLUMNS)
        with duckdb.connect(str(self._path)) as connection:
            before = self._count(connection)
            connection.executemany(
                f"INSERT OR IGNORE INTO facts ({', '.join(COLUMNS)}) VALUES ({placeholders})",  # noqa: S608
                [_to_row(fact) for fact in facts],
            )
            return self._count(connection) - before

    @staticmethod
    def _count(connection: duckdb.DuckDBPyConnection) -> int:
        row = connection.execute("SELECT count(*) FROM facts").fetchone()
        return int(row[0]) if row else 0

    def as_of(self, *, cik: int, field: str, as_of: date) -> list[Fact]:
        """Facts about one company and field that were public on ``as_of``."""
        with duckdb.connect(str(self._path), read_only=True) as connection:
            rows = connection.execute(
                f"SELECT {', '.join(COLUMNS)} FROM facts "  # noqa: S608
                "WHERE cik = ? AND field = ? AND public_on <= ? "
                "ORDER BY period_end, public_on, fact_id",
                [cik, field, as_of],
            ).fetchall()
        return [_from_row(row) for row in rows]
