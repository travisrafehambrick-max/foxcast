"""The fact record (brief F2, change AF).

A fact is one fetched value with its provenance and three times:

- the period it covers (``period_start`` to ``period_end``);
- ``public_on``: the first day the public could have known it;
- ``fetched_at``: when foxcast fetched it.

An XBRL fact may also carry its company's fiscal label (``fiscal_year``, ``fiscal_period``;
see :mod:`foxcast.facts.fiscal`). The label is read from the same source copy, so it is
rebuilt and checked with the value; it is not part of the ``fact_id``.

Point-in-time queries filter on ``public_on``. The ``fact_id`` is a hash of the source copy
and the fact's identifying fields, so the same fetch always yields the same IDs, and a
model can cite a fact only by an ID that code issued.
"""

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from foxcast.facts.fiscal import fiscal_labels
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
    fiscal_year: int | None = None
    fiscal_period: str | None = None  # Q1 to Q4, H1, H2 or FY


def facts_from_sec(fetched: Fetched) -> list[Fact]:
    """Turn a fetched SEC company-concept table into facts."""
    rows = fetched.table.to_dict("records")
    labels = fiscal_labels(rows)
    facts = []
    for row in rows:
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
        fiscal_year, fiscal_period = labels.get((period_start, period_end), (None, None))
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
                fiscal_year=fiscal_year,
                fiscal_period=fiscal_period,
            )
        )
    return facts


MAX_TEXT_CHARS: Final = 500
MAX_PASSAGE_CHARS: Final = 4000
ALLOWED_CONTROLS: Final = frozenset("\n\r\t")


def is_hidden_character(char: str) -> bool:
    """A character a reader cannot see but a model may still read.

    Control characters (bar tab and line breaks), format characters (category Cf: bidi
    overrides, zero-width marks, tag characters), Unicode line and paragraph separators,
    and variation selectors, all known ways to hide or smuggle text.
    """
    category = unicodedata.category(char)
    if category == "Cc":
        return char not in ALLOWED_CONTROLS
    if category in {"Cf", "Zl", "Zp"}:
        return True
    code = ord(char)
    return 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF


def has_hidden_characters(text: str) -> bool:
    """True if any character is hidden (see :func:`is_hidden_character`).

    Text values and passages refuse such text; the filing-text extractor removes these
    characters before the text is archived.
    """
    return any(is_hidden_character(c) for c in text)


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
    "is_hidden_character",
    "passage_from_span",
    "sha256_hex",
    "text_fact",
    "verify_passage",
]
