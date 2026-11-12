"""The fact record (brief F2, change AF).

A fact is one fetched value with its provenance and three times:

- the period it covers (``period_start`` to ``period_end``);
- ``public_on``: the first day the public could have known it;
- ``fetched_at``: when foxcast fetched it.

Point-in-time queries filter on ``public_on``. The ``fact_id`` is a hash of the source copy
and the fact's identifying fields, so the same fetch always yields the same IDs, and a
model can cite a fact only by an ID that code issued.
"""

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from foxcast.facts.ids import canonical_id
from foxcast.sources.base import Fetched

# SEC stores only the filing date, so a filing dated D is public from D + 1 (see sources.sec).
SEC_PUBLIC_LAG: Final = timedelta(days=1)


@dataclass(frozen=True)
class Fact:
    fact_id: str
    cik: int
    field: str
    unit: str
    period_start: date | None
    period_end: date
    value: float
    public_on: date
    fetched_at: datetime
    source_url: str
    content_sha256: str
    accession: str | None


def facts_from_sec(fetched: Fetched) -> list[Fact]:
    """Turn a fetched SEC company-concept table into facts."""
    facts = []
    for row in fetched.table.to_dict("records"):
        field = f"{row['taxonomy']}:{row['tag']}"
        start = row["period_start"]
        period_start: date | None = start if isinstance(start, date) else None
        period_end: date = row["period_end"]
        filed: date = row["filed"]
        accession = str(row["accession"])
        fact_id = canonical_id(
            fetched.content_sha256,
            str(row["cik"]),
            field,
            str(row["unit"]),
            period_start.isoformat() if period_start else "",
            period_end.isoformat(),
            accession,
        )
        facts.append(
            Fact(
                fact_id=fact_id,
                cik=int(row["cik"]),
                field=field,
                unit=str(row["unit"]),
                period_start=period_start,
                period_end=period_end,
                value=float(row["value"]),
                public_on=filed + SEC_PUBLIC_LAG,
                fetched_at=fetched.fetched_at,
                source_url=fetched.url,
                content_sha256=fetched.content_sha256,
                accession=accession,
            )
        )
    return facts


MAX_TEXT_CHARS: Final = 500
MAX_PASSAGE_CHARS: Final = 4000
ALLOWED_CONTROLS: Final = frozenset("\n\r\t")


def has_hidden_characters(text: str) -> bool:
    """Control characters (bar tab and line breaks) or invisible format characters.

    Format characters (Unicode category Cf) include bidirectional overrides and zero-width
    marks, which can hide or reorder text a reader sees. Text values and passages refuse
    them; the filing-text extractor removes them before the text is archived.
    """
    return any(
        (unicodedata.category(c) == "Cc" and c not in ALLOWED_CONTROLS)
        or unicodedata.category(c) == "Cf"
        for c in text
    )


@dataclass(frozen=True)
class TextFact:
    """A fetched text value: an auditor, an executive, a segment name, an 8-K item type."""

    fact_id: str
    cik: int
    field: str
    value: str
    period_end: date | None
    public_on: date
    fetched_at: datetime
    source_url: str
    content_sha256: str
    accession: str | None


def text_fact(
    *,
    cik: int,
    field: str,
    value: str,
    period_end: date | None,
    public_on: date,
    fetched_at: datetime,
    source_url: str,
    content_sha256: str,
    accession: str | None,
) -> TextFact:
    """Build a text fact, refusing empty, oversized or hidden-character values."""
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field}: a text fact needs a value")
    if len(cleaned) > MAX_TEXT_CHARS:
        raise ValueError(f"{field}: text value is longer than {MAX_TEXT_CHARS} characters")
    if has_hidden_characters(cleaned):
        raise ValueError(f"{field}: text value contains control or invisible characters")
    fact_id = canonical_id(
        "text",
        content_sha256,
        str(cik),
        field,
        period_end.isoformat() if period_end else "",
        accession or "",
        cleaned,
    )
    return TextFact(
        fact_id=fact_id,
        cik=cik,
        field=field,
        value=cleaned,
        period_end=period_end,
        public_on=public_on,
        fetched_at=fetched_at,
        source_url=source_url,
        content_sha256=content_sha256,
        accession=accession,
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode_archive(raw: bytes) -> str:
    """The one way archived text is decoded, for cutting passages and for checking them.

    Strict UTF-8, no newline translation and no normalisation, so the same bytes always
    give the same string and the same offsets. Offsets are Python string indices (code
    points), not byte offsets.
    """
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"archived text is not valid UTF-8: {error}") from error


@dataclass(frozen=True)
class Passage:
    """A span of an archived document, located by code-point offsets into its text.

    ``content_sha256`` is the hash of the archived bytes; ``text_sha256`` is the hash of
    the span itself, which the citation checker compares (scorecard row 1c).
    """

    passage_id: str
    cik: int
    accession: str
    form: str
    section: str
    start: int
    end: int
    text: str
    text_sha256: str
    public_on: date
    fetched_at: datetime
    source_url: str
    content_sha256: str


def passage_from_span(
    archived: bytes,
    start: int,
    end: int,
    *,
    cik: int,
    accession: str,
    form: str,
    section: str,
    public_on: date,
    fetched_at: datetime,
    source_url: str,
) -> Passage:
    """Cut a passage out of an archived document's bytes."""
    document = decode_archive(archived)
    if not 0 <= start < end <= len(document):
        raise ValueError(f"span {start}:{end} is outside a document of {len(document)} chars")
    if end - start > MAX_PASSAGE_CHARS:
        raise ValueError(f"span {start}:{end} is longer than {MAX_PASSAGE_CHARS} characters")
    text = document[start:end]
    if has_hidden_characters(text):
        raise ValueError(f"span {start}:{end} contains control or invisible characters")
    content_sha256 = sha256_hex(archived)
    passage_id = canonical_id(
        "passage", content_sha256, accession, section, str(start), str(end), prefix="p"
    )
    return Passage(
        passage_id=passage_id,
        cik=cik,
        accession=accession,
        form=form,
        section=section,
        start=start,
        end=end,
        text=text,
        text_sha256=sha256_hex(text.encode("utf-8")),
        public_on=public_on,
        fetched_at=fetched_at,
        source_url=source_url,
        content_sha256=content_sha256,
    )


def verify_passage(passage: Passage, archived: bytes) -> bool:
    """True only if these are the archived bytes and they hold this passage at its offsets."""
    if sha256_hex(archived) != passage.content_sha256:
        return False
    try:
        document = decode_archive(archived)
    except ValueError:
        return False
    if not 0 <= passage.start < passage.end <= len(document):
        return False
    span = document[passage.start : passage.end]
    return span == passage.text and sha256_hex(span.encode("utf-8")) == passage.text_sha256


__all__ = [
    "MAX_PASSAGE_CHARS",
    "MAX_TEXT_CHARS",
    "SEC_PUBLIC_LAG",
    "Fact",
    "Passage",
    "TextFact",
    "decode_archive",
    "facts_from_sec",
    "has_hidden_characters",
    "passage_from_span",
    "sha256_hex",
    "text_fact",
    "verify_passage",
]
