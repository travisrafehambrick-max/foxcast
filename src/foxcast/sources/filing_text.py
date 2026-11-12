"""Filing text: fetch a filing's document, extract its visible text, page it (Stage 1A.3).

The model reads filings only as passages: spans of an extracted, archived text, each with
an ID. This module does the steps before the store:

1. :class:`SecDocumentProvider` fetches one document of a filing from EDGAR's archive.
2. :func:`extract_text` turns the HTML into plain visible text. It drops scripts, styles,
   ``<head>`` and hidden elements: inline XBRL's ``display:none`` header, and the other
   usual ways of hiding text (``visibility:hidden``, zero opacity or font size, off-screen
   positions, ``aria-hidden``, the ``hidden`` attribute, and classes that a ``<style>``
   block hides). Characters a reader cannot see are removed or made plain, because
   passages refuse them (see ``facts.record``).
3. :func:`page_spans` cuts the text into passages at paragraph boundaries.
4. :func:`archive_filing_text` saves the raw document and the extracted text (as UTF-8)
   in the content-addressed archive and builds passages against the saved text.

Extraction runs in time linear in the document, and refuses documents nested deeper than
``MAX_DEPTH``, so a hostile filing cannot stall a run. Filing text is still untrusted: it
is shown to the model as third-party data, behind the locked-down tool server (task 1A.7).

Citations are checked against the archived extracted text, never by re-extracting, so a
change in the extractor or in Python's HTML parser cannot break an existing citation.
``EXTRACTOR_VERSION`` is recorded with each archived text.

Sections are not split yet: every passage is labelled "full text" (the plan's fallback).
Splitting into 10-K items arrives with edgartools 6.x once it clears the seven-day wait.
"""

import codecs
import platform
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Final

import httpx
import pandas as pd
import pandera.pandas as pa

from foxcast.facts.record import (
    MAX_PASSAGE_CHARS,
    SEC_PUBLIC_LAG,
    Passage,
    is_hidden_character,
    passage_from_span,
)
from foxcast.sources.base import Fetched
from foxcast.sources.sec import ACCESSION_PATTERN, SEC_THROTTLE
from foxcast.sources.throttle import Throttle
from foxcast.store.archive import SourceArchive

EXTRACTOR_VERSION: Final = "filing-text/2"
SEC_ARCHIVE_HOST: Final = "https://www.sec.gov"
FULL_TEXT_SECTION: Final = "full text"
PAGE_CHARS: Final = 1500
MAX_DOCUMENT_BYTES: Final = 50_000_000
# Work per tag is O(1) at any depth; the cap only bounds memory. Legacy filings leave
# thousands of <font> and <b> tags unclosed, so it is set well above them.
MAX_DEPTH: Final = 100_000
DOCUMENT_PATTERN: Final = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)?$", re.ASCII
)
DOCUMENT_CONTENT_TYPES: Final = frozenset(
    {"text/html", "application/xhtml+xml", "text/xml", "text/plain"}
)

SKIPPED_TAGS: Final = frozenset({"script", "style", "head", "title", "noscript", "template"})
VOID_TAGS: Final = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)
BLOCK_TAGS: Final = frozenset(
    {
        "address", "article", "blockquote", "br", "caption", "dd", "div", "dl", "dt",
        "figcaption", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
        "ol", "p", "pre", "section", "table", "tbody", "thead", "tfoot", "tr", "ul",
    }
)  # fmt: skip
CELL_TAGS: Final = frozenset({"td", "th"})
# Start tag -> open tags it closes when one of them is innermost (HTML's optional end tags).
IMPLICITLY_CLOSED_BY: Final[dict[str, frozenset[str]]] = {
    "p": frozenset({"p"}),
    "li": frozenset({"li"}),
    "tr": frozenset({"tr", "td", "th"}),
    "td": frozenset({"td", "th"}),
    "th": frozenset({"td", "th"}),
    "dt": frozenset({"dt", "dd"}),
    "dd": frozenset({"dt", "dd"}),
}
# Declarations that hide an element's text from a reader.
HIDING_DECLARATION: Final = re.compile(
    r"display\s*:\s*none"
    r"|visibility\s*:\s*(hidden|collapse)"
    r"|opacity\s*:\s*0*(\.0*)?\s*(;|$|!)"
    r"|font-size\s*:\s*0*(\.0*)?\s*(px|pt|em|rem|%)?\s*(;|$|!)"
    r"|(left|top|text-indent|margin-left)\s*:\s*-\s*\d{3,}"
    r"|transform\s*:[^;]*scale[xy]?\(\s*0*(\.0*)?\s*[,)]"
    r"|clip\s*:\s*rect\(\s*0",
    re.IGNORECASE,
)
# A box shrunk to nothing with its overflow cut off also hides its text.
ZERO_BOX: Final = re.compile(
    r"(^|[;\s])(max-)?(height|width)\s*:\s*0*(\.0*)?\s*(px|pt|em|%)?\s*(;|$|!)", re.I
)
OVERFLOW_HIDDEN: Final = re.compile(r"overflow(-[xy])?\s*:\s*(hidden|clip)", re.IGNORECASE)
CSS_COMMENT: Final = re.compile(r"/\*.*?\*/", re.DOTALL)
STYLE_RULE: Final = re.compile(r"([^{}]+)\{([^{}]*)\}")


def hides_text(declarations: str) -> bool:
    """True if CSS declarations hide an element's text from a reader."""
    plain = CSS_COMMENT.sub("", declarations)
    if HIDING_DECLARATION.search(plain):
        return True
    return bool(ZERO_BOX.search(plain) and OVERFLOW_HIDDEN.search(plain))


CLASS_SELECTOR: Final = re.compile(r"^\s*\.([A-Za-z_][\w-]*)\s*$")
DECLARED_CHARSET: Final = re.compile(rb"""(?:encoding|charset)\s*=\s*["']?([A-Za-z0-9_.:-]+)""")
SPACE_RUN: Final = re.compile(r"[ \t]+")
SENTENCE_END: Final = re.compile(r"[.!?]\s")
PARAGRAPH: Final = re.compile(r"\S(?:.*?\S)?(?=\s*\n\s*\n|\s*$)", re.DOTALL)


def _hiding_classes(css: str) -> frozenset[str]:
    """Classes that a ``<style>`` block hides with a plain ``.name { ... }`` rule."""
    hidden = set()
    for selectors, body in STYLE_RULE.findall(CSS_COMMENT.sub("", css)):
        if not hides_text(body):
            continue
        for selector in selectors.split(","):
            match = CLASS_SELECTOR.match(selector)
            if match:
                hidden.add(match.group(1))
    return frozenset(hidden)


class _VisibleText(HTMLParser):
    """Collect visible text in one pass, with O(1) work per tag."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._open: list[tuple[str, bool]] = []  # (tag, hides its content)
        self._open_names: Counter[str] = Counter()
        self._hidden_depth = 0
        self._style_parts: list[str] = []
        self._hidden_classes: frozenset[str] = frozenset()

    def _hides(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if tag in SKIPPED_TAGS or tag == "ix:header":
            return True
        for name, value in attrs:
            if name == "hidden":
                return True
            if name == "aria-hidden" and (value or "").strip().lower() == "true":
                return True
            if name == "style" and value and hides_text(value):
                return True
            if name == "class" and value and self._hidden_classes.intersection(value.split()):
                return True
        return False

    def _pop(self) -> None:
        name, hides = self._open.pop()
        self._open_names[name] -= 1
        self._hidden_depth -= hides

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._hidden_depth and (tag in BLOCK_TAGS or tag in CELL_TAGS):
            self.parts.append("\n" if tag in BLOCK_TAGS else " ")
        if tag in VOID_TAGS:
            return
        # HTML closes some tags implicitly: a new <p> ends an open <p>, and so on.
        while self._open and self._open[-1][0] in IMPLICITLY_CLOSED_BY.get(tag, ()):
            self._pop()
        if len(self._open) >= MAX_DEPTH:
            raise ValueError(f"document nests tags deeper than {MAX_DEPTH}")
        hides = self._hides(tag, attrs)
        self._open.append((tag, hides))
        self._open_names[tag] += 1
        self._hidden_depth += hides

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # In HTML, "/>" on an ordinary element is ignored: <div hidden/> stays open and
        # hides what follows. Fail closed: a self-closed element that hides is opened.
        # (Namespaced XML elements such as link:schemaRef really are empty.)
        if tag not in VOID_TAGS and ":" not in tag and self._hides(tag, attrs):
            self.handle_starttag(tag, attrs)
            return
        if not self._hidden_depth and tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self._hidden_classes = self._hidden_classes | _hiding_classes(
                "".join(self._style_parts)
            )
            self._style_parts = []
        if self._open_names[tag]:
            # Close back to the matching tag; tags left unclosed inside it end with it.
            # Each tag is pushed and popped once, so this is linear over the document.
            while self._open:
                name = self._open[-1][0]
                self._pop()
                if name == tag:
                    break
        if not self._hidden_depth and tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._open and self._open[-1][0] == "style":
            self._style_parts.append(data)
        if not self._hidden_depth:
            self.parts.append(data)


def _decode_html(raw: bytes) -> str:
    """Decode by byte-order mark, then a declared charset, then UTF-8, then cp1252."""
    for bom, encoding in ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"),
                          (codecs.BOM_UTF16_BE, "utf-16")):  # fmt: skip
        if raw.startswith(bom):
            return raw.decode(encoding)
    declared = DECLARED_CHARSET.search(raw[:2048])
    if declared:
        try:
            return raw.decode(declared.group(1).decode("ascii"))
        except (LookupError, UnicodeDecodeError):
            pass  # a wrong or unknown declaration: fall through to the defaults
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")  # older EDGAR documents


def _plain(char: str) -> str:
    if char == "\n":
        return char
    category = unicodedata.category(char)
    if category in {"Zs", "Zl", "Zp"} or char in "\t\f\v\r":
        return " "
    if is_hidden_character(char):
        return " " if category == "Cc" else ""
    return char


def extract_text(raw: bytes) -> str:
    """Visible text of an HTML document: one paragraph per block, blank lines between."""
    parser = _VisibleText()
    parser.feed(_decode_html(raw))
    parser.close()
    text = "".join(_plain(c) for c in "".join(parser.parts))
    lines = (SPACE_RUN.sub(" ", line).strip() for line in text.split("\n"))
    return "\n\n".join(line for line in lines if line)


def _split_long(text: str, start: int, end: int) -> list[tuple[int, int]]:
    spans = []
    while end - start > PAGE_CHARS:
        window = text[start : start + PAGE_CHARS + 1]
        breaks = [m.start() + 1 for m in SENTENCE_END.finditer(window)]
        cut = start + (breaks[-1] if breaks else PAGE_CHARS)
        span_end = cut
        while span_end > start and text[span_end - 1].isspace():
            span_end -= 1
        if span_end > start:
            spans.append((start, span_end))
        start = cut
        while start < end and text[start].isspace():
            start += 1
    if start < end:
        spans.append((start, end))
    return spans


def page_spans(text: str) -> list[tuple[int, int]]:
    """Cut text into passage spans of at most ``PAGE_CHARS``, at paragraph boundaries.

    Short paragraphs are grouped; a long one is split after a sentence end, or cut hard
    when it has none. Spans are ordered, do not overlap, never start or end with white
    space, and together cover every visible character.
    """
    if PAGE_CHARS > MAX_PASSAGE_CHARS:  # pragma: no cover - a configuration error
        raise ValueError("PAGE_CHARS must not exceed MAX_PASSAGE_CHARS")
    pages: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    for match in PARAGRAPH.finditer(text):
        start, end = match.start(), match.end()
        if end - start > PAGE_CHARS:
            if current:
                pages.append(current)
                current = None
            pages.extend(_split_long(text, start, end))
        elif current and end - current[0] <= PAGE_CHARS:
            current = (current[0], end)
        else:
            if current:
                pages.append(current)
            current = (start, end)
    if current:
        pages.append(current)
    return pages


@dataclass(frozen=True)
class FilingDocumentQuery:
    cik: int
    accession: str
    document: str

    def __post_init__(self) -> None:
        if self.cik < 1:
            raise ValueError(f"CIK must be positive, got {self.cik}")
        if not re.fullmatch(ACCESSION_PATTERN, self.accession, re.ASCII):
            raise ValueError(f"not an accession number: {self.accession!r}")
        if not DOCUMENT_PATTERN.fullmatch(self.document) or ".." in self.document:
            raise ValueError(f"not a plain document name: {self.document!r}")


DOCUMENT_CONTRACT: Final = pa.DataFrameSchema(
    {"text": pa.Column(str, pa.Check.str_length(min_value=1))}, strict=True
)


class SecDocumentProvider:
    """One document of a filing, from EDGAR's archive, as extracted visible text."""

    name = "sec_document"
    contract = DOCUMENT_CONTRACT
    secret_params: frozenset[str] = frozenset()
    default_throttle: Throttle | None = SEC_THROTTLE
    allowed_content_types: frozenset[str] | None = DOCUMENT_CONTENT_TYPES
    max_bytes: int = MAX_DOCUMENT_BYTES

    def __init__(self, contact: str) -> None:
        if not contact.strip():
            raise ValueError("SEC requires a contact name and email in the User-Agent header")
        self._contact = contact

    def build_request(self, query: FilingDocumentQuery) -> httpx.Request:
        folder = query.accession.replace("-", "")
        url = f"{SEC_ARCHIVE_HOST}/Archives/edgar/data/{query.cik}/{folder}/{query.document}"
        return httpx.Request("GET", url, headers={"User-Agent": self._contact})

    def shape(self, payload: bytes) -> pd.DataFrame:
        if len(payload) > self.max_bytes:
            raise ValueError(f"document is larger than {self.max_bytes} bytes")
        text = extract_text(payload)
        return pd.DataFrame({"text": [text]} if text else {"text": []})

    def mismatch(self, query: FilingDocumentQuery, table: pd.DataFrame) -> str | None:
        return None  # the URL names the filing; there is no subject in the payload to check


@dataclass(frozen=True)
class ArchivedText:
    text: bytes  # the extracted text, as archived (UTF-8)
    passages: tuple[Passage, ...]
    extractor_version: str
    python_version: str


def archive_filing_text(
    fetched: Fetched,
    archive: SourceArchive,
    *,
    query: FilingDocumentQuery,
    form: str,
    filed: date,
) -> ArchivedText:
    """Save the raw document and its extracted text; return the text and its passages.

    ``filed`` is the filing date from the index; passages are public from the next day.
    """
    if fetched.table.empty:
        raise ValueError(f"{query.accession}: no extracted text to archive")
    archive.save(fetched.raw)
    archived = str(fetched.table["text"].iloc[0]).encode("utf-8")
    archive.save(archived)
    passages = tuple(
        passage_from_span(
            archived,
            start,
            end,
            cik=query.cik,
            accession=query.accession,
            form=form,
            section=FULL_TEXT_SECTION,
            public_on=filed + SEC_PUBLIC_LAG,
            fetched_at=fetched.fetched_at,
            source_url=fetched.url,
        )
        for start, end in page_spans(archived.decode("utf-8"))
    )
    return ArchivedText(
        text=archived,
        passages=passages,
        extractor_version=EXTRACTOR_VERSION,
        python_version=platform.python_version(),
    )
