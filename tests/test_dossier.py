"""The dossier builder (Stage 1A.6; scorecard rows 1a, 1b and 1d)."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from foxcast.claims.render import ClaimType, SubmittedClaim
from foxcast.dossier.builder import (
    ALL_WITHHELD,
    NO_CLAIMS,
    NO_MODEL,
    NOT_A_SECTION,
    DossierRequest,
    ModelDeclaration,
    build_dossier,
    shown_reason,
)
from foxcast.dossier.content import (
    EIGHT_K_ITEMS,
    Annual,
    Trade,
    holdings_table,
    latest_revenue,
)
from foxcast.dossier.output import (
    UnverifiedClaimError,
    escape,
    render_json,
    render_markdown,
    write_dossier,
)
from foxcast.dossier.schema import (
    SECTIONS,
    Cell,
    Dossier,
    Section,
    SilentGapError,
    Status,
    Table,
    silent_gaps,
)
from foxcast.dossier.sources import cited_ids
from foxcast.facts.record import Fact, TextFact
from foxcast.notice import NOTICE_TEXT
from foxcast.store.archive import SourceArchive
from foxcast.store.pit import FactStore

from .test_checker import AS_OF, COMPANIES, annual_revenue, ph
from .test_sec_ingest import GOOGL

BUILT_AT = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
MODEL = ModelDeclaration("Claude Opus 5.5", "2026-06")
PROMPT = "a" * 64
World = tuple[FactStore, SourceArchive]


def request(**changes: object) -> DossierRequest:
    base = DossierRequest(
        ticker="GOOGL", cik=GOOGL, as_of=AS_OF, built_at=BUILT_AT, company="Alphabet Inc."
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def no_llm(world: World, **changes: object) -> Dossier:
    store, archive = world
    return build_dossier(request(**changes), store, archive)


def with_claims(world: World, claims: list[SubmittedClaim]) -> Dossier:
    store, archive = world
    return build_dossier(
        request(model=MODEL, prompt_sha256=PROMPT),
        store,
        archive,
        claims=claims,
        companies=COMPANIES,
    )


def revenue_claim(world: World, section: str = "financial_health") -> SubmittedClaim:
    _, latest = annual_revenue(world[0])
    return SubmittedClaim(
        section, ClaimType.FACT, f"Revenue was {ph(latest.fact_id)}.", (latest.fact_id,)
    )


# --- row 1d: all 19 sections, each filled or with a stated reason ---


@pytest.mark.integration
def test_a_no_llm_dossier_has_all_19_sections_filled_or_with_a_reason(world: World) -> None:
    dossier = no_llm(world)
    assert [(s.number, s.key) for s in dossier.sections] == [(s.number, s.key) for s in SECTIONS]
    assert silent_gaps(dossier.sections) == []
    for section in dossier.sections:
        if section.status is Status.FILLED:
            assert section.has_content
        else:
            assert section.reason and section.reason.strip()


@pytest.mark.integration
def test_the_stage_1a_sections_say_why_they_are_unavailable(world: World) -> None:
    dossier = no_llm(world)
    for key in (
        "what_changed",
        "industry_peers",
        "price_implies",
        "wall_street",
        "calendar",
        "event_outlook",
        "forecast",
        "track_record",
    ):
        assert dossier.section(key).status is Status.UNAVAILABLE, key
    for key in ("bull_bear", "three_questions"):
        assert dossier.section(key).reason == NO_MODEL
    # The world holds only Revenues and NetIncomeLoss, so section 7 has nothing to show
    # and names every concept it looked for.
    capital = dossier.section("capital_structure")
    assert capital.status is Status.UNAVAILABLE
    assert "LongTermDebt" in (capital.reason or "")


@pytest.mark.integration
def test_filled_sections_show_code_rendered_values_with_their_ids(world: World) -> None:
    dossier = no_llm(world)
    store, _ = world
    _, latest = annual_revenue(store)
    health = dossier.section("financial_health")
    rows = [row for table in health.tables for row in table.rows]
    assert any(latest.fact_id in cell.cites for row in rows for cell in row)
    assert any(g.item == "Forensic scores" for g in health.gaps)
    assert dossier.section("market_pricing").status is Status.FILLED  # Form 4 trades
    events = dossier.section("news_events")
    assert events.status is Status.FILLED  # 8-K items
    items = [row[1].text for table in events.tables for row in table.rows]
    assert all(text.split()[0] in EIGHT_K_ITEMS for text in items)
    assert dossier.section("notice").lines[0].value.text == NOTICE_TEXT


@pytest.mark.integration
def test_every_shown_id_is_listed_in_the_sources_section(world: World) -> None:
    dossier = no_llm(world)
    body = [s for s in dossier.sections if s.key not in ("sources", "notice")]
    sources = dossier.section("sources").tables[0]
    listed = {row[0].text for row in sources.rows}
    assert cited_ids(body) <= listed
    assert dossier.certification.items_shown == len(listed)
    for row in sources.rows:
        assert row[2].text.startswith("https://")  # the source URL
        assert len(row[4].text) == 64  # the content hash


@pytest.mark.integration
def test_the_header_states_model_holding_cost_and_freshness(world: World) -> None:
    header = {ln.label: ln.value.text for ln in no_llm(world).section("header").lines}
    assert header["Model"] == "none: built without a model"
    assert header["Operator's holding"].startswith("not declared")
    assert header["Run cost"].startswith("unavailable:")
    assert header["Fresh: SEC XBRL figures"].endswith(
        datetime(2026, 11, 11, 15, 0, tzinfo=UTC).isoformat()
    )
    built = {ln.label: ln.value.text for ln in with_claims(world, []).section("header").lines}
    assert built["Model"] == "Claude Opus 5.5 (self-reported)"


@pytest.mark.integration
def test_a_dossier_cannot_show_its_own_future(world: World) -> None:
    early = no_llm(world, as_of=date(2026, 10, 29))  # the 10-Q and Form 4 came out later
    shown = " ".join(ln.value.text for ln in early.section("business").lines)
    assert "0001652044-26-000018" in shown  # the 10-K, filed in February
    assert "0001652044-26-000084" not in shown  # the 10-Q, public from 2026-10-30
    store, _ = world
    ids = cited_ids(early.sections)
    shown_facts = store.at(AS_OF).get_many(ids)
    assert shown_facts and all(f.public_on <= date(2026, 10, 29) for f in shown_facts.values())


# --- claims: shown only when verified; withheld ones listed without their words ---


@pytest.mark.integration
def test_a_verified_claim_is_shown_in_its_section(world: World) -> None:
    dossier = with_claims(world, [revenue_claim(world)])
    health = dossier.section("financial_health")
    assert len(health.claims) == 1
    assert dossier.certification.claims_verified == 1
    assert health.claims[0].claim_id in dossier.verified_claim_ids
    assert "$402.8 billion" in health.claims[0].text
    assert dossier.section("three_questions").reason == NO_CLAIMS


@pytest.mark.integration
def test_withheld_claims_are_listed_without_the_models_words(world: World) -> None:
    _, latest = annual_revenue(world[0])
    typed = SubmittedClaim(
        "three_questions", ClaimType.FACT, "Revenue was 987 billion.", (latest.fact_id,)
    )
    misplaced = replace(revenue_claim(world), section="header")
    dossier = with_claims(world, [typed, misplaced])
    assert dossier.certification.claims_withheld == 2
    reasons = {w.claim_id: w.reasons for w in dossier.withheld}
    assert any(NOT_A_SECTION in r for r in reasons.values())
    markdown, data = render_markdown(dossier), render_json(dossier)
    assert "987" not in markdown and "987" not in data
    assert dossier.section("three_questions").reason == ALL_WITHHELD
    health = with_claims(world, [replace(typed, section="financial_health")])
    assert any(g.reason == ALL_WITHHELD for g in health.section("financial_health").gaps)


@pytest.mark.integration
def test_an_estimate_shows_its_likelihood_word_and_model_estimate(world: World) -> None:
    _, latest = annual_revenue(world[0])
    bull = SubmittedClaim(
        "bull_case",
        ClaimType.ESTIMATE,
        f"Revenue of {ph(latest.fact_id)} is likely to keep growing.",
        (latest.fact_id,),
        probability=0.7,
    )
    dossier = with_claims(world, [bull])
    section = dossier.section("bull_bear")
    assert section.status is Status.FILLED
    assert [g.item for g in section.gaps] == ["Bear case"]
    markdown = render_markdown(dossier)
    assert r"likely \(model estimate 70%\)" in markdown
    assert "**Bull case**" in markdown


def test_claims_need_a_declared_model(tmp_path: Path) -> None:
    store, archive = FactStore(tmp_path / "f.duckdb"), SourceArchive(tmp_path / "a")
    with pytest.raises(ValueError, match="model"):
        build_dossier(request(), store, archive, claims=[])


# --- row 1a: never show a fact claim without a verified citation ---


@pytest.mark.integration
def test_rendering_fails_on_a_fact_claim_that_was_not_verified(world: World) -> None:
    dossier = with_claims(world, [revenue_claim(world)])
    tampered = replace(dossier, verified_claim_ids=frozenset())
    with pytest.raises(UnverifiedClaimError):
        render_markdown(tampered)
    with pytest.raises(UnverifiedClaimError):
        render_json(tampered)


@pytest.mark.integration
def test_withheld_claims_do_not_fail_the_run(world: World) -> None:
    typed = replace(revenue_claim(world), text="Revenue was 987 billion.")
    dossier = with_claims(world, [typed])
    assert render_markdown(dossier)  # no exception


# --- the schema refuses silent gaps ---


def test_a_section_marked_filled_must_have_content() -> None:
    with pytest.raises(SilentGapError):
        Section(6, "financial_health", "Financial health", Status.FILLED)


def test_an_unavailable_section_must_state_a_reason() -> None:
    with pytest.raises(SilentGapError):
        Section(2, "what_changed", "What changed", Status.UNAVAILABLE)
    with pytest.raises(SilentGapError):
        Section(2, "what_changed", "What changed", Status.UNAVAILABLE, reason="  ")


def test_a_table_needs_rows() -> None:
    with pytest.raises(SilentGapError):
        Table("Empty", ("A",), ())


@pytest.mark.integration
def test_a_dossier_with_a_section_missing_or_out_of_order_is_refused(world: World) -> None:
    dossier = no_llm(world)
    with pytest.raises(SilentGapError):
        replace(dossier, sections=dossier.sections[:-1])
    swapped = (dossier.sections[1], dossier.sections[0], *dossier.sections[2:])
    with pytest.raises(SilentGapError):
        replace(dossier, sections=swapped)


# --- output ---


@pytest.mark.parametrize(
    "text",
    [
        "# Heading",
        "**bold** _it_",
        "[link](https://example.com)",
        "![img](x)",
        "<script>",
        "a | b",
        "$x$",
        "line\n\n# two",
        "`code`",
        "&amp;",
        "~~strike~~",
        "see https://evil.example and www.evil.example",
        "mail a@b.co",
        "1. nested list",
    ],
)
def test_escaped_text_cannot_add_markdown(text: str) -> None:
    shown = escape(text)
    assert "\n" not in shown
    for i, char in enumerate(shown):
        if char in "#*_[]()!<>|$`&~:.@":
            assert shown[i - 1] == "\\", (text, shown)


@pytest.mark.integration
def test_markdown_and_json_carry_all_sections_and_the_certification(world: World) -> None:
    dossier = with_claims(world, [revenue_claim(world)])
    markdown = render_markdown(dossier)
    for spec in SECTIONS:
        assert f"## {spec.number}. " in markdown
    assert "Certified by foxcast" in markdown
    assert "Claude Opus 5\\.5 \\(self\\-reported\\)" in markdown or "self" in markdown
    data = json.loads(render_json(dossier))
    assert data["schema"] == "foxcast.dossier/1"
    assert [s["number"] for s in data["sections"]] == list(range(1, 20))
    assert all(s["status"] == "filled" or s["reason"] for s in data["sections"])
    assert data["certification"]["model_self_reported"] is True
    assert data["certification"]["prompt_sha256"] == PROMPT


@pytest.mark.integration
def test_write_dossier_writes_both_files(world: World, tmp_path: Path) -> None:
    md_path, json_path = write_dossier(no_llm(world), tmp_path / "out")
    assert md_path.name == "GOOGL-2026-11-11.md"
    assert b"\r\n" not in md_path.read_bytes()
    assert json.loads(json_path.read_text(encoding="utf-8"))["ticker"] == "GOOGL"


@pytest.mark.integration
def test_a_ticker_that_cannot_name_a_file_is_refused(world: World, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ticker"):
        write_dossier(replace(no_llm(world), ticker="../x"), tmp_path)


def test_request_fields_are_checked() -> None:
    with pytest.raises(ValueError, match="prompt hash"):
        request(model=MODEL, prompt_sha256="not-a-hash")
    with pytest.raises(ValueError, match="needs a model"):
        request(prompt_sha256=PROMPT)
    with pytest.raises(ValueError, match="model name"):
        ModelDeclaration("Opus" + chr(0x200B), "2026-06")


def test_cell_defaults_to_no_cites() -> None:
    assert Cell("label").cites == ()


def test_escaping_drops_hidden_characters_and_leading_markers() -> None:
    assert escape("- item") == r"\- item"
    assert escape("+x") == r"\+x"
    assert escape("a" + chr(0x202E) + "b") == "ab"


# --- review fixes ---


def test_an_empty_record_still_gives_19_sections(tmp_path: Path) -> None:
    store, archive = FactStore(tmp_path / "f.duckdb"), SourceArchive(tmp_path / "a")
    dossier = build_dossier(request(), store, archive)
    assert silent_gaps(dossier.sections) == []
    assert dossier.section("financial_health").status is Status.UNAVAILABLE
    assert dossier.section("sources").status is Status.UNAVAILABLE
    assert render_markdown(dossier).count("\n## ") == 19


def test_withheld_reasons_do_not_quote_the_claim() -> None:
    assert shown_reason("the period 'Q3 2025' is not carried by a cited annual fact") == (
        "the period (words from the claim) is not carried by a cited annual fact"
    )
    assert shown_reason("the period 2025-03-31 is not carried by a cited item") == (
        "a period named in the claim is not carried by a cited item"
    )
    assert shown_reason("the metric 'net income' is not carried by a cited item") == (
        "the metric (words from the claim) is not carried by a cited item"
    )


@pytest.mark.integration
def test_a_withheld_period_claim_is_listed_without_its_period(world: World) -> None:
    _, latest = annual_revenue(world[0])
    wrong = SubmittedClaim(
        "three_questions",
        ClaimType.FACT,
        f"Revenue for FY2019 was {ph(latest.fact_id)}.",
        (latest.fact_id,),
    )
    dossier = with_claims(world, [wrong])
    assert dossier.withheld
    assert "2019" not in render_markdown(dossier) and "2019" not in render_json(dossier)


@pytest.mark.integration
def test_filled_sections_say_when_no_research_step_ran(world: World) -> None:
    gaps = no_llm(world).section("financial_health").gaps
    assert any(g.item == "Research claims" and g.reason == NO_MODEL for g in gaps)


def _fact(field: str, end: date, value: float = 1.0, unit: str = "USD") -> Fact:
    return Fact(
        fact_id=f"f_{abs(hash((field, end))) % 16**16:016x}",
        cik=GOOGL,
        field=field,
        unit=unit,
        period_start=date(end.year, 1, 1),
        period_end=end,
        value=value,
        public_on=end,
        fetched_at=BUILT_AT,
        source_url="https://www.sec.gov/x",
        content_sha256="0" * 64,
        accession="0000000000-00-000001",
    )


def test_latest_revenue_is_the_newest_under_either_concept() -> None:
    old = _fact("us-gaap:Revenues", date(2017, 12, 31))
    new = _fact("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", date(2025, 12, 31))
    annual = Annual({"Revenues": [old], new.field.split(":")[1]: [new]}, frozenset())
    assert latest_revenue(annual) == new


def test_holdings_take_the_last_row_of_a_filing() -> None:
    def trade(index: int, owned: float) -> Trade:
        day = date(2026, 10, 25)
        texts = {
            name: TextFact(
                f"f_{index:015x}{n}",
                GOOGL,
                f"sec:form4:t{index}:{name}",
                value,
                day,
                day,
                BUILT_AT,
                "https://www.sec.gov/x",
                "0" * 64,
                "0000000000-00-000002",
            )
            for n, (name, value) in enumerate((("owner", "A Person"), ("security", "Class C")))
        }
        held = replace(
            _fact(f"sec:form4:t{index}:owned_after", day, owned, "shares"),
            fact_id=f"f_{index:015x}9",
        )
        return Trade("0000000000-00-000002", index, texts, {"owned_after": held})

    trades = sorted(
        [trade(2, 200.0), trade(10, 1000.0), trade(1, 100.0)],
        key=lambda t: (t.day or date.min, t.accession, t.index),
        reverse=True,
    )
    table = holdings_table(trades)
    assert table is not None and table.rows[0][3].text == "1,000 shares"
