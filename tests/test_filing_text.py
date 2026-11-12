"""Filing text: extraction, paging into passages, fetching and archiving (Stage 1A.3)."""

import time
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from foxcast.facts.record import MAX_PASSAGE_CHARS, has_hidden_characters, verify_passage
from foxcast.sources.base import Fetched, NoData, Unavailable, run_provider
from foxcast.sources.filing_text import (
    EXTRACTOR_VERSION,
    FULL_TEXT_SECTION,
    MAX_DEPTH,
    PAGE_CHARS,
    FilingDocumentQuery,
    SecDocumentProvider,
    archive_filing_text,
    extract_text,
    page_spans,
)
from foxcast.store.archive import SourceArchive

FIXTURE = (Path(__file__).parent / "fixtures" / "sec_8k_googl_20261028.htm").read_bytes()
FETCHED_AT = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)
QUERY = FilingDocumentQuery(
    cik=1652044, accession="0001652044-26-000080", document="goog-20261028.htm"
)


# --- extraction ---


@pytest.mark.unit
def test_a_real_8k_keeps_its_visible_text_and_drops_the_hidden_xbrl_header() -> None:
    text = extract_text(FIXTURE)
    assert "Item 2.02" in text
    assert "Results of Operations and Financial Condition" in text
    assert "Item 9.01" in text
    assert "goog-20261028.xsd" not in text
    assert "0001652044" not in text  # the CIK sits only in the hidden header
    assert not has_hidden_characters(text)
    assert "\xa0" not in text
    assert "&#" not in text


@pytest.mark.unit
def test_scripts_styles_head_and_hidden_elements_are_dropped() -> None:
    html = (
        b"<html><head><title>T</title><style>p{color:red}</style></head><body>"
        b"<script>alert(1)</script><p>Shown</p>"
        b'<div style="color: blue; DISPLAY : none"><p>Hidden <b>nested</b></p><br></div>'
        b"<p>Also shown</p></body></html>"
    )
    assert extract_text(html) == "Shown\n\nAlso shown"


@pytest.mark.unit
def test_void_and_self_closing_tags_do_not_unbalance_hidden_blocks() -> None:
    html = (
        b'<div style="display:none"><img src="x"><br/><link:schemaRef href="a"/>secret</div>'
        b"<p>after<br>break</p>"
    )
    assert extract_text(html) == "after\n\nbreak"


@pytest.mark.unit
def test_unclosed_tags_inside_a_hidden_block_end_with_it() -> None:
    html = b'<div style="display:none"><p>secret<span>more</div><p>visible</p>'
    assert extract_text(html) == "visible"


@pytest.mark.unit
def test_entities_spaces_and_invisible_characters_are_normalised() -> None:
    html = (
        "<p>A&#160;B&nbsp;C "
        + chr(0x200B)  # zero-width space
        + "D"
        + chr(0xAD)  # soft hyphen
        + "e"
        + chr(0xFEFF)  # byte-order mark
        + "</p><p>tab\there\fpage</p>"
    ).encode()
    assert extract_text(html) == "A B C De\n\ntab here page"


@pytest.mark.unit
def test_table_cells_are_kept_apart_and_rows_are_lines() -> None:
    html = (
        b"<table><tr><td>Revenue</td><td>$1.2</td></tr><tr><td>Cost</td><td>$0.4</td></tr></table>"
    )
    assert extract_text(html) == "Revenue $1.2\n\nCost $0.4"


@pytest.mark.unit
def test_windows_1252_documents_are_read() -> None:
    assert extract_text("<p>café — “ok”</p>".encode("cp1252")) == ("café — “ok”")


# --- paging ---


@pytest.mark.unit
def test_paragraphs_are_grouped_into_pages_without_crossing_the_limit() -> None:
    paragraphs = [f"Paragraph {i} " + "word " * 60 for i in range(20)]
    text = "\n\n".join(p.strip() for p in paragraphs)
    spans = page_spans(text)
    assert len(spans) > 1
    assert all(end - start <= PAGE_CHARS for start, end in spans)
    assert all(text[start:end] == text[start:end].strip() for start, end in spans)


@pytest.mark.unit
def test_a_paragraph_longer_than_a_page_is_split_at_a_sentence() -> None:
    sentence = "This sentence is about forty-five characters. "
    text = (sentence * 80).strip()
    spans = page_spans(text)
    assert len(spans) > 1
    assert all(text[start:end].endswith(".") for start, end in spans)


@pytest.mark.unit
def test_text_with_no_sentence_breaks_is_cut_hard() -> None:
    text = "x" * (PAGE_CHARS * 2 + 5)
    assert page_spans(text) == [
        (0, PAGE_CHARS),
        (PAGE_CHARS, PAGE_CHARS * 2),
        (PAGE_CHARS * 2, len(text)),
    ]


@pytest.mark.unit
def test_empty_text_has_no_pages() -> None:
    assert page_spans("") == []
    assert page_spans("\n\n  \n\n") == []


@given(st.lists(st.text(alphabet=st.sampled_from("ab .\n"), min_size=0, max_size=900), max_size=12))
def test_pages_are_ordered_disjoint_and_cover_every_visible_character(parts: list[str]) -> None:
    text = "\n\n".join(parts)
    spans = page_spans(text)
    covered = [False] * len(text)
    previous_end = 0
    for start, end in spans:
        assert previous_end <= start < end <= len(text)
        assert end - start <= MAX_PASSAGE_CHARS
        assert text[start:end].strip() == text[start:end]
        for i in range(start, end):
            covered[i] = True
        previous_end = end
    for i, char in enumerate(text):
        if not char.isspace():
            assert covered[i], (i, char)


# --- fetching and archiving ---


def fetch(payload: bytes = FIXTURE, status: int = 200, content_type: str = "text/html") -> object:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status, content=payload, headers={"content-type": content_type}
            )
        )
    )
    return run_provider(
        SecDocumentProvider("Jane Doe jane@example.com"), QUERY, client, now=lambda: FETCHED_AT
    )


@pytest.mark.unit
def test_the_document_url_is_built_from_the_accession_and_name() -> None:
    request = SecDocumentProvider("Jane Doe jane@example.com").build_request(QUERY)
    assert str(request.url) == (
        "https://www.sec.gov/Archives/edgar/data/1652044/000165204426000080/goog-20261028.htm"
    )
    assert request.headers["User-Agent"] == "Jane Doe jane@example.com"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("accession", "document"),
    [
        ("bad", "a.htm"),
        ("0001652044-26-000080", "../secret.htm"),
        ("0001652044-26-000080", "/etc/passwd"),
        ("0001652044-26-000080", "a b.htm"),
        ("0001652044-26-000080", ""),
    ],
)
def test_bad_accessions_and_document_names_are_refused(accession: str, document: str) -> None:
    with pytest.raises(ValueError):
        FilingDocumentQuery(cik=1, accession=accession, document=document)


@pytest.mark.integration
def test_a_fetched_document_is_archived_and_paged_into_verifiable_passages(
    tmp_path: Path,
) -> None:
    result = fetch()
    assert isinstance(result, Fetched), result
    archive = SourceArchive(tmp_path / "sources")
    archived = archive_filing_text(
        result, archive, query=QUERY, form="8-K", filed=date(2026, 10, 28)
    )
    assert archive.load(result.content_sha256) == FIXTURE  # the raw document is kept
    assert archived.passages
    assert archived.extractor_version == EXTRACTOR_VERSION
    assert archived.python_version
    for passage in archived.passages:
        assert passage.section == FULL_TEXT_SECTION
        assert passage.accession == QUERY.accession
        assert passage.public_on == date(2026, 10, 29)  # filed on the 28th, public the 29th
        assert passage.source_url == result.url
        assert verify_passage(passage, archive.load(passage.content_sha256))
    assert archived.text == extract_text(FIXTURE).encode("utf-8")
    assert any("Item 2.02" in p.text for p in archived.passages)


@pytest.mark.unit
def test_archiving_an_empty_fetch_is_a_clear_error(tmp_path: Path) -> None:
    empty = Fetched(
        table=pd.DataFrame({"text": []}),
        raw=b"",
        content_sha256="0" * 64,
        url="https://example.test",
        fetched_at=FETCHED_AT,
    )
    with pytest.raises(ValueError, match="no extracted text"):
        archive_filing_text(
            empty, SourceArchive(tmp_path), query=QUERY, form="8-K", filed=date(2026, 10, 28)
        )


# --- hiding techniques (each must not reach a passage) ---


@pytest.mark.unit
@pytest.mark.parametrize(
    "hidden",
    [
        '<div style="display:none">SECRET</div>',
        '<span style="visibility: hidden">SECRET</span>',
        '<span style="visibility:collapse">SECRET</span>',
        '<span style="opacity:0">SECRET</span>',
        '<span style="opacity: 0.0;">SECRET</span>',
        '<span style="font-size:0">SECRET</span>',
        '<span style="font-size: 0px; color: red">SECRET</span>',
        '<div style="position:absolute; left:-9999px">SECRET</div>',
        '<p style="text-indent:-10000px">SECRET</p>',
        '<span aria-hidden="true">SECRET</span>',
        "<span hidden>SECRET</span>",
        "<template><p>SECRET</p></template>",
        "<noscript>SECRET</noscript>",
    ],
)
def test_text_hidden_by_any_usual_technique_is_dropped(hidden: str) -> None:
    html = f"<p>before</p>{hidden}<p>after</p>".encode()
    assert extract_text(html) == "before\n\nafter"


@pytest.mark.unit
@pytest.mark.parametrize(
    "visible",
    [
        '<span style="opacity:0.5">kept</span>',
        '<span style="font-size:10pt">kept</span>',
        '<span style="left:-5px">kept</span>',
        '<span aria-hidden="false">kept</span>',
    ],
)
def test_ordinary_styles_do_not_hide_text(visible: str) -> None:
    assert extract_text(visible.encode()) == "kept"


@pytest.mark.unit
def test_classes_hidden_by_a_style_block_are_dropped() -> None:
    html = (
        b"<html><head><style>.x1 { color: red } .gone, .also { display: none }</style></head>"
        b'<body><p class="x1">shown</p><p class="big gone">SECRET</p>'
        b'<p class="also">SECRET</p></body></html>'
    )
    assert extract_text(html) == "shown"


@pytest.mark.unit
def test_an_unclosed_hidden_block_hides_the_rest_rather_than_leaking() -> None:
    assert extract_text(b'<p>before</p><div style="display:none"><p>x<p>y') == "before"


@pytest.mark.unit
def test_line_separators_and_variation_selectors_do_not_survive() -> None:
    html = ("<p>one" + chr(0x2028) + "two" + chr(0x2029) + "three"
            + chr(0xFE0F) + chr(0xE0100) + "</p>").encode()  # fmt: skip
    text = extract_text(html)
    assert text == "one two three"
    assert not has_hidden_characters(text)


# --- size, depth and speed ---


@pytest.mark.unit
def test_deep_nesting_is_refused_rather_than_slow() -> None:
    with pytest.raises(ValueError, match="deeper"):
        extract_text(b"<b>" * (MAX_DEPTH + 1) + b"x")


@pytest.mark.unit
@pytest.mark.parametrize(
    "html",
    [
        b"<p>x" * 50_000,  # unclosed paragraphs, common in old filings
        b"<div>" * 900 + b"</i>" * 50_000 + b"text",  # stray end tags under deep nesting
        b"<span>y</span>" * 50_000,
    ],
)
def test_extraction_stays_linear_on_awkward_html(html: bytes) -> None:
    started = time.perf_counter()
    extract_text(html)
    # Quadratic work on these inputs takes hours; linear work takes seconds, even under
    # coverage on a slow laptop.
    assert time.perf_counter() - started < 20.0


@pytest.mark.integration
def test_a_response_declared_too_large_is_refused_before_reading() -> None:
    small = SecDocumentProvider("Jane Doe jane@example.com")
    small.max_bytes = 1000  # type: ignore[misc]
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content=b"x" * 2000, headers={"content-type": "text/html"}
            )
        )
    )
    result = run_provider(small, QUERY, client, now=lambda: FETCHED_AT)
    assert isinstance(result, Unavailable)
    assert "larger than 1000" in result.reason


@pytest.mark.integration
def test_a_streamed_response_is_cut_off_at_the_cap_without_a_length_header() -> None:
    small = SecDocumentProvider("Jane Doe jane@example.com")
    small.max_bytes = 1000  # type: ignore[misc]
    chunks_sent = []

    def body() -> Iterator[bytes]:
        for i in range(100):
            chunks_sent.append(i)
            yield b"x" * 500

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=body(), headers={"content-type": "text/html"})
        )
    )
    result = run_provider(small, QUERY, client, now=lambda: FETCHED_AT)
    assert isinstance(result, Unavailable)
    assert "larger than 1000" in result.reason
    assert len(chunks_sent) < 10  # stopped early, not after reading everything


# --- encodings and content types ---


@pytest.mark.unit
def test_a_declared_charset_is_honoured() -> None:
    text = "caf" + chr(0xE9) + " " + chr(0x2019)  # e-acute and a right quotation mark
    html = ('<?xml version="1.0" encoding="windows-1252"?><p>' + text + "</p>").encode("cp1252")
    assert extract_text(html) == text


@pytest.mark.unit
def test_utf16_documents_with_a_byte_order_mark_are_read() -> None:
    assert extract_text("<p>wide text</p>".encode("utf-16")) == "wide text"


@pytest.mark.unit
def test_a_wrong_charset_declaration_falls_back_to_utf8() -> None:
    html = '<meta charset="no-such-codec"><p>café</p>'.encode()
    assert extract_text(html) == "café"


@pytest.mark.integration
@pytest.mark.parametrize("content_type", ["text/plain", "text/xml", "application/xhtml+xml"])
def test_plain_text_and_xml_documents_are_accepted(content_type: str) -> None:
    result = fetch(b"<p>An old filing</p>", content_type=content_type)
    assert isinstance(result, Fetched), result


@pytest.mark.integration
def test_a_document_that_is_not_html_is_unavailable_and_an_empty_one_is_no_data() -> None:
    wrong_type = fetch(content_type="application/pdf")
    assert isinstance(wrong_type, Unavailable)
    assert "content type" in wrong_type.reason
    empty = fetch(b"<html><body><div style='display:none'>x</div></body></html>")
    assert isinstance(empty, NoData)


@pytest.mark.unit
def test_an_oversized_document_is_refused_before_parsing() -> None:
    provider = SecDocumentProvider("Jane Doe jane@example.com")
    with pytest.raises(ValueError, match="larger than"):
        provider.shape(b"x" * (provider.max_bytes + 1))


@pytest.mark.unit
@pytest.mark.parametrize(
    "hidden",
    [
        '<div style="display:none"/>SECRET',  # "/>" is ignored in HTML: the div stays open
        "<span hidden/>SECRET",
        '<span style="display:/**/none">SECRET</span>',
        '<span style="transform: scale(0)">SECRET</span>',
        '<span style="transform:scaleX(0.0)">SECRET</span>',
        '<div style="height:0; overflow:hidden">SECRET</div>',
        '<div style="max-height: 0px; overflow-y: hidden">SECRET</div>',
        '<span style="position:absolute; clip: rect(0 0 0 0)">SECRET</span>',
    ],
)
def test_more_hiding_techniques_are_dropped(hidden: str) -> None:
    html = f"<div><p>before</p>{hidden}</div><p>after</p>".encode()
    assert extract_text(html) == "before\n\nafter"


@pytest.mark.unit
@pytest.mark.parametrize(
    "visible",
    [
        '<div style="height:12px"/>kept',
        '<span style="transform: scale(1.5)">kept</span>',
        '<div style="height:0">kept</div>',  # no overflow cut: the text still shows
        '<div style="overflow:hidden">kept</div>',
    ],
)
def test_ordinary_boxes_and_transforms_keep_their_text(visible: str) -> None:
    assert extract_text(visible.encode()) == "kept"


@pytest.mark.unit
def test_class_rules_inside_media_queries_and_comments_are_read() -> None:
    html = (
        b"<style>@media screen{.a{display:none}} .b{/* x */visibility:hidden}</style>"
        b'<p class="a">SECRET</p><p class="b">SECRET</p><p>shown</p>'
    )
    assert extract_text(html) == "shown"


@pytest.mark.unit
def test_legacy_documents_with_thousands_of_unclosed_font_tags_are_read() -> None:
    html = b"<font size=2>word " * 5000
    assert extract_text(html).startswith("word word")
