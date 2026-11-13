"""The locked-down tool server (Stage 1A.7; change T; rows 1a, 1b, 1d and 4c)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from foxcast.mcp.dossier_tools import MAX_CLAIMS_PER_RUN
from foxcast.mcp.envelope import PROTOCOL, UNTRUSTED_NOTICE
from foxcast.mcp.inputs import INPUTS
from foxcast.mcp.server import Pins, ToolServer, workflow_sha256
from foxcast.store.archive import SourceArchive
from foxcast.store.pit import FactStore

from .mcp_session import (
    MODEL,
    OPEN,
    RUN_ID,
    copy_world,
    make_server,
    make_world,
    ph,
    scripted_session,
    ticking_clock,
)
from .test_checker import TEN_K
from .test_sec_ingest import GOOGL

World = tuple[FactStore, SourceArchive]
HEADER = {
    "foxcast_tool",
    "tool",
    "ok",
    "run_id",
    "as_of",
    "untrusted",
    "notice",
    "page",
    "meter",
    "data",
    "error",
}


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("mcp-world")
    make_world(folder)
    return folder


@pytest.fixture
def world(template: Path, tmp_path: Path) -> World:
    """A private copy of the synthetic world: derived facts written by one test stay there."""
    return copy_world(template, tmp_path / "world")


@pytest.fixture
def server(world: World, tmp_path: Path) -> ToolServer:
    return make_server(world, tmp_path / "work")


def opened(server: ToolServer) -> dict[str, Any]:
    result = server.call("research_get_context", OPEN)
    assert result["ok"], result["error"]
    return result


def revenue_ids(server: ToolServer) -> tuple[str, str]:
    facts = server.call("sec_get_facts", {"run_id": RUN_ID, "field_prefix": "us-gaap:Revenues"})
    annual = [
        f
        for f in facts["data"]["items"]
        if f["period_start"] and f["period_start"][5:] == "01-01" and f["period_end"][5:] == "12-31"
    ]
    return annual[-2]["id"], annual[-1]["id"]


def calls_of(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "work" / "runs" / RUN_ID / "calls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- the common header ---


@pytest.mark.integration
def test_every_result_has_the_common_header(server: ToolServer) -> None:
    for result in scripted_session(server):
        assert set(result) == HEADER
        assert result["foxcast_tool"] == PROTOCOL
        assert result["ok"], result["error"]
        assert result["run_id"] == RUN_ID
        assert result["as_of"] == OPEN["as_of"]


@pytest.mark.integration
def test_third_party_text_is_marked_untrusted(server: ToolServer) -> None:
    opened(server)
    filings = server.call("sec_list_filings", {"run_id": RUN_ID})["data"]["items"]
    passages = server.call(
        "sec_read_passages", {"run_id": RUN_ID, "accession": filings[0]["accession"]}
    )
    assert passages["untrusted"] == ["items[].untrusted"]
    assert passages["notice"] == UNTRUSTED_NOTICE
    for item in passages["data"]["items"]:
        assert set(item["untrusted"]) == {"text"}
        assert "text" not in item  # the text lives only under the untrusted key
    texts = server.call("sec_get_text_facts", {"run_id": RUN_ID})
    assert texts["untrusted"] == ["items[].untrusted"]
    numbers = server.call("sec_get_facts", {"run_id": RUN_ID})
    assert numbers["untrusted"] == [] and numbers["notice"] is None


# --- research_get_context ---


@pytest.mark.integration
def test_opening_a_run_verifies_the_ticker_against_the_filing_index(server: ToolServer) -> None:
    result = opened(server)
    data = result["data"]
    assert data["status"] == "opened"
    assert data["run"]["untrusted"] == {"company": "Alphabet Inc."}  # the filer's own words
    assert result["untrusted"] == ["run.untrusted"]
    assert data["run"]["ticker_verified"] is True
    assert data["record"]["numbers"]["us-gaap:"] > 0
    assert {f["form"] for f in data["record"]["filings"]} >= {"10-K", "10-Q"}
    assert "fiscal Q1 2026" in data["rules"]["fiscal_periods"]


@pytest.mark.integration
def test_a_ticker_the_filing_index_does_not_list_is_refused(server: ToolServer) -> None:
    result = server.call("research_get_context", {**OPEN, "ticker": "AAPL"})
    assert not result["ok"]
    assert result["error"]["code"] == "conflict"


@pytest.mark.integration
def test_a_company_not_ingested_is_refused(server: ToolServer) -> None:
    result = server.call("research_get_context", {**OPEN, "cik": 320193, "ticker": "AAPL"})
    assert result["error"]["code"] == "not_found"
    assert "foxcast ingest" in result["error"]["message"]


@pytest.mark.integration
def test_an_as_of_date_in_the_future_is_refused(server: ToolServer) -> None:
    result = server.call("research_get_context", {**OPEN, "as_of": "2027-01-04"})
    assert result["error"]["code"] == "invalid_input"


@pytest.mark.integration
def test_reopening_resumes_and_another_model_is_refused(server: ToolServer) -> None:
    opened(server)
    again = opened(server)
    assert again["data"]["status"] == "resumed"
    assert again["meter"]["tool_calls"] == 1
    other = server.call("research_get_context", {**OPEN, "model_name": "Other model"})
    assert other["error"]["code"] == "conflict"
    cutoff = server.call("research_get_context", {**OPEN, "model_cutoff": "2025-01"})
    assert "model_cutoff" in cutoff["error"]["message"]
    holding = server.call("research_get_context", {**OPEN, "holding": "holds"})
    assert "holding" in holding["error"]["message"]


@pytest.mark.integration
@pytest.mark.parametrize(
    "arguments",
    [
        {**OPEN, "cik": str(GOOGL)},  # a string is not a number
        {**OPEN, "extra": 1},  # unknown fields are refused
        {**OPEN, "ticker": "googl"},
        {**OPEN, "as_of": "2026-02-30"},
        {k: v for k, v in OPEN.items() if k != "model_name"},
        {**OPEN, "holding": "maybe"},
        {**OPEN, "model_name": "Claude" + chr(0x200B)},
        {**OPEN, "model_name": "   "},
        {**OPEN, "ticker": "CON.A"},  # a Windows device name cannot name a run folder
    ],
)
def test_every_input_is_validated(server: ToolServer, arguments: dict[str, Any]) -> None:
    result = server.call("research_get_context", arguments)
    assert not result["ok"]
    assert result["error"]["code"] == "invalid_input"


@pytest.mark.integration
def test_unknown_tools_and_runs_are_refused_and_logged(server: ToolServer, tmp_path: Path) -> None:
    assert server.call("shell_exec", {"cmd": "dir"})["error"]["code"] == "unknown_tool"
    assert server.call("sec_list_filings", [])["error"]["code"] == "invalid_input"
    missing = server.call("sec_list_filings", {"run_id": "AAPL-2026-11-11"})
    assert missing["error"]["code"] == "not_found"
    unbound = (tmp_path / "work" / "runs" / "unbound-calls.jsonl").read_text(encoding="utf-8")
    assert unbound.count("\n") == 3


# --- the SEC reads ---


@pytest.mark.integration
def test_facts_are_shown_as_code_renders_them_and_paged(server: ToolServer) -> None:
    opened(server)
    first = server.call("sec_get_text_facts", {"run_id": RUN_ID, "limit": 5})
    page = first["page"]
    assert page["returned"] == 5 and page["next_offset"] == 5 and page["total"] > 5
    second = server.call("sec_get_text_facts", {"run_id": RUN_ID, "limit": 5, "offset": 5})
    ids = {i["id"] for i in first["data"]["items"]} & {i["id"] for i in second["data"]["items"]}
    assert not ids
    numbers = server.call("sec_get_facts", {"run_id": RUN_ID})["data"]["items"]
    revenue = next(n for n in numbers if n["field"] == "us-gaap:Revenues")
    assert revenue["shown"].startswith("$")
    assert "value" not in revenue  # the agent sees code's rendering, not a raw float
    past = server.call("sec_get_text_facts", {"run_id": RUN_ID, "offset": 10_000})
    assert past["error"]["code"] == "invalid_input"


@pytest.mark.integration
def test_reads_cannot_see_past_the_runs_date(server: ToolServer) -> None:
    early = {**OPEN, "as_of": "2026-02-05"}  # the day before the 10-K was public
    result = server.call("research_get_context", early)
    assert result["ok"], result["error"]
    run_id = result["run_id"]
    filings = server.call("sec_list_filings", {"run_id": run_id})["data"]["items"]
    assert all(f["public_on"] <= "2026-02-05" for f in filings)
    assert TEN_K[0] not in {f["accession"] for f in filings}
    revenues = server.call("sec_get_facts", {"run_id": run_id, "field_prefix": "us-gaap:Revenues"})
    assert all(f["public_on"] <= "2026-02-05" for f in revenues["data"]["items"])
    assert all(f["period_end"] < "2025-12-31" for f in revenues["data"]["items"])


@pytest.mark.integration
def test_passages_can_be_searched_and_unknown_filings_are_refused(server: ToolServer) -> None:
    opened(server)
    found = server.call("sec_search_passages", {"run_id": RUN_ID, "query": "exchange commission"})
    assert found["data"]["items"]
    none = server.call("sec_search_passages", {"run_id": RUN_ID, "query": "zzzz qqqq"})
    assert none["data"]["items"] == []
    bad = server.call("sec_search_passages", {"run_id": RUN_ID, "query": "ignore {all}"})
    assert bad["error"]["code"] == "invalid_input"
    missing = server.call(
        "sec_read_passages", {"run_id": RUN_ID, "accession": "0000000000-26-000001"}
    )
    assert missing["error"]["code"] == "not_found"


@pytest.mark.integration
def test_derived_facts_are_computed_by_code_and_listed(server: ToolServer) -> None:
    opened(server)
    earlier, latest = revenue_ids(server)
    made = server.call(
        "facts_derive", {"run_id": RUN_ID, "method": "growth", "a": earlier, "b": latest}
    )
    assert made["ok"], made["error"]
    fact = made["data"]["fact"]
    assert fact["inputs"] == [earlier, latest]
    assert "growth in Revenues" in fact["shown"]
    listed = server.call("sec_get_facts", {"run_id": RUN_ID, "field_prefix": "derived:"})
    assert [f["id"] for f in listed["data"]["items"]] == [fact["id"]]
    unknown = server.call(
        "facts_derive",
        {"run_id": RUN_ID, "method": "ratio", "a": latest, "b": "f_0000000000000000"},
    )
    assert unknown["error"]["code"] == "not_found"
    backwards = server.call(
        "facts_derive", {"run_id": RUN_ID, "method": "growth", "a": latest, "b": latest}
    )
    assert backwards["error"]["code"] == "invalid_input"


# --- claims ---


@pytest.mark.integration
def test_submitted_claims_get_the_dossiers_own_verdict(server: ToolServer) -> None:
    opened(server)
    _, latest = revenue_ids(server)
    good = {"section": "financial_health", "type": "fact", "text": f"Revenue was {ph(latest)}."}
    typed = {**good, "text": f"Revenue was nearly 403 billion {ph(latest)}."}
    claims = [{**good, "cites": [latest]}, {**typed, "cites": [latest]}]
    result = server.call("dossier_submit_claims", {"run_id": RUN_ID, "claims": claims})
    assert result["ok"], result["error"]
    shown, withheld = result["data"]["results"]
    assert shown["verdict"] == "shown" and shown["new"]
    assert "$402.8 billion" in shown["untrusted"]["text"]
    assert withheld["verdict"] == "withheld" and withheld["reasons"]
    assert "untrusted" not in withheld
    again = server.call("dossier_submit_claims", {"run_id": RUN_ID, "claims": claims[:1]})
    assert again["data"]["results"][0]["new"] is False
    assert again["data"]["submitted_total"] == 2


@pytest.mark.integration
def test_a_claim_outside_the_claim_sections_is_withheld_not_refused(server: ToolServer) -> None:
    opened(server)
    _, latest = revenue_ids(server)
    claim = {"section": "header", "type": "fact", "text": f"See {ph(latest)}.", "cites": [latest]}
    result = server.call("dossier_submit_claims", {"run_id": RUN_ID, "claims": [claim]})
    assert result["data"]["results"][0]["verdict"] == "withheld"


@pytest.mark.integration
def test_badly_typed_claims_are_refused_as_input(server: ToolServer) -> None:
    opened(server)
    for claim in (
        {"section": "business", "type": "rumour", "text": "x", "cites": []},
        {"section": "business", "type": "estimate", "text": "x", "cites": [], "probability": True},
        {"section": "business", "type": "fact", "text": "x", "cites": "f_0000000000000000"},
    ):
        result = server.call("dossier_submit_claims", {"run_id": RUN_ID, "claims": [claim]})
        assert result["error"]["code"] == "invalid_input", claim
    empty = server.call("dossier_submit_claims", {"run_id": RUN_ID, "claims": []})
    assert empty["error"]["code"] == "invalid_input"


@pytest.mark.integration
def test_a_run_takes_a_bounded_number_of_claims(
    server: ToolServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    import foxcast.mcp.dossier_tools as tools

    monkeypatch.setattr(tools, "MAX_CLAIMS_PER_RUN", 1)
    opened(server)
    _, latest = revenue_ids(server)
    claims = [
        {
            "section": "business",
            "type": "opinion",
            "text": f"{word} {ph(latest)}.",
            "cites": [latest],
        }
        for word in ("Strong", "Weak")
    ]
    result = server.call("dossier_submit_claims", {"run_id": RUN_ID, "claims": claims})
    assert result["error"]["code"] == "limit"
    assert MAX_CLAIMS_PER_RUN == 300


# --- render, cost meter, call log ---


@pytest.mark.integration
def test_render_writes_the_dossier_with_the_cost_meter(server: ToolServer, tmp_path: Path) -> None:
    results = scripted_session(server)
    render = results[-1]
    data = render["data"]
    assert data["claims_counted"] == 5
    assert data["claims_verified"] == 3 and data["claims_withheld"] == 2
    assert data["sections_filled"] + data["sections_unavailable"] == 19
    calls_before = len(results) - 1
    assert data["cost"]["tool_calls"] == calls_before == render["meter"]["tool_calls"]
    assert data["cost"]["result_bytes"] == render["meter"]["result_bytes"]
    assert data["cost"]["wall_seconds"] == pytest.approx(0.25 * calls_before)
    folder = tmp_path / "work" / "dossiers"
    markdown = (folder / data["markdown_file"]).read_text(encoding="utf-8")
    assert f"{calls_before} tool calls" in markdown
    dossier_json = json.loads((folder / data["json_file"]).read_text(encoding="utf-8"))
    assert dossier_json["certification"]["prompt_sha256"] == workflow_sha256()
    assert dossier_json["certification"]["model"] == MODEL["model_name"]
    record = json.loads((tmp_path / "work" / "runs" / RUN_ID / "record.json").read_text())
    assert record["dossier"]["cost"] == data["cost"]


@pytest.mark.integration
def test_a_rendered_run_is_closed(server: ToolServer) -> None:
    results = scripted_session(server)
    claim = {"section": "business", "type": "opinion", "text": "Sound.", "cites": []}
    late = server.call("dossier_submit_claims", {"run_id": RUN_ID, "claims": [claim]})
    assert late["error"]["code"] == "run_closed"
    again = server.call("dossier_render", {"run_id": RUN_ID})
    assert again["data"]["already_rendered"] is True
    assert again["data"]["json_sha256"] == results[-1]["data"]["json_sha256"]
    assert opened(server)["data"]["status"] == "rendered"


@pytest.mark.integration
def test_every_call_is_logged_with_its_result_hash_and_no_third_party_text(
    server: ToolServer, tmp_path: Path
) -> None:
    results = scripted_session(server)
    calls = calls_of(tmp_path)
    assert [c["tool"] for c in calls] == [r["tool"] for r in results]
    assert [c["seq"] for c in calls] == list(range(len(results)))
    assert all(len(c["result_sha256"]) == 64 and c["result_bytes"] > 0 for c in calls)
    log = (tmp_path / "work" / "runs" / RUN_ID / "calls.jsonl").read_text(encoding="utf-8")
    passages = next(r for r in results if r["tool"] == "sec_read_passages")
    for item in passages["data"]["items"]:
        assert item["untrusted"]["text"][:60] not in log


@pytest.mark.integration
def test_a_restarted_server_resumes_the_run(world: World, tmp_path: Path) -> None:
    first = make_server(world, tmp_path / "work")
    opened(first)
    _, latest = revenue_ids(first)
    claim = {"section": "financial_health", "type": "fact", "text": f"Revenue was {ph(latest)}."}
    first.call(
        "dossier_submit_claims", {"run_id": RUN_ID, "claims": [{**claim, "cites": [latest]}]}
    )
    later = datetime(2026, 11, 13, 18, 0, tzinfo=UTC)
    second = make_server(world, tmp_path / "work", clock=ticking_clock(later))
    resumed = opened(second)
    assert resumed["data"]["status"] == "resumed"
    assert resumed["data"]["claims"] == {"submitted": 1, "by_section": {"financial_health": 1}}
    assert resumed["meter"]["tool_calls"] == 3  # open, read, submit
    render = second.call("dossier_render", {"run_id": RUN_ID})
    assert render["data"]["claims_verified"] == 1


@pytest.mark.unit
def test_every_tool_has_an_input_model() -> None:
    assert set(INPUTS) == {
        "research_get_context",
        "sec_get_facts",
        "sec_get_text_facts",
        "sec_list_filings",
        "sec_read_passages",
        "sec_search_passages",
        "facts_derive",
        "dossier_submit_claims",
        "dossier_render",
    }


# --- review fixes: pins, metering refused calls, damaged logs, result budget ---


@pytest.mark.integration
def test_a_pinned_room_opens_only_its_date_and_companies(world: World, tmp_path: Path) -> None:
    pins = Pins(as_of=OPEN["as_of"], companies={"GOOGL": GOOGL})
    server = make_server(world, tmp_path / "work", pins=pins)
    later = server.call("research_get_context", {**OPEN, "as_of": "2026-11-10"})
    assert later["error"]["code"] == "invalid_input"
    assert "pinned to the as-of date" in later["error"]["message"]
    other = server.call("research_get_context", {**OPEN, "ticker": "GOOG"})
    assert "pinned to GOOGL" in other["error"]["message"]
    assert opened(server)["data"]["run"]["ticker_verified"] is True


@pytest.mark.integration
def test_an_unverified_ticker_needs_a_pin(world: World, tmp_path: Path) -> None:
    store, _ = world
    empty = SourceArchive(tmp_path / "no-index")  # the filing index cannot be read back
    unpinned = make_server((store, empty), tmp_path / "a")
    refused = unpinned.call("research_get_context", OPEN)
    assert refused["error"]["code"] == "conflict"
    assert "pin the company" in refused["error"]["message"]
    pinned = make_server((store, empty), tmp_path / "b", pins=Pins(companies={"GOOGL": GOOGL}))
    result = pinned.call("research_get_context", OPEN)
    assert result["ok"] and result["data"]["run"]["ticker_verified"] is True
    assert result["data"]["run"]["untrusted"] == {"company": None}


@pytest.mark.integration
def test_refused_calls_are_logged_and_metered_in_their_run(
    server: ToolServer, tmp_path: Path
) -> None:
    opened(server)
    refused = server.call("sec_get_facts", {"run_id": RUN_ID, "limit": 0})
    assert refused["error"]["code"] == "invalid_input"
    assert refused["run_id"] == RUN_ID and refused["meter"]["tool_calls"] == 1
    server.call("no_such_tool", {"run_id": RUN_ID})
    server.call("research_get_context", {**OPEN, "model_name": "Other model"})
    after = server.call("sec_list_filings", {"run_id": RUN_ID})
    assert after["meter"]["tool_calls"] == 4
    assert [c["ok"] for c in calls_of(tmp_path)] == [True, False, False, False, True]
    assert not (tmp_path / "work" / "runs" / "unbound-calls.jsonl").exists()


@pytest.mark.integration
def test_a_damaged_call_log_is_reported_not_fatal(server: ToolServer, tmp_path: Path) -> None:
    opened(server)
    log = tmp_path / "work" / "runs" / RUN_ID / "calls.jsonl"
    log.write_text(log.read_text(encoding="utf-8") + "\x00\x00 not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"calls\.jsonl line 2 is damaged"):
        server.call("sec_list_filings", {"run_id": RUN_ID})


@pytest.mark.integration
def test_a_big_batch_leaves_shown_text_out_past_the_budget(
    server: ToolServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    import foxcast.mcp.dossier_tools as tools

    monkeypatch.setattr(tools, "SHOWN_TEXT_BUDGET", 80)
    opened(server)
    _, latest = revenue_ids(server)
    claims = [
        {"section": "financial_health", "type": "fact", "text": f"{w} {ph(latest)}.",
         "cites": [latest]}
        for w in ("Revenue was", "Sales were")
    ]  # fmt: skip
    results = server.call("dossier_submit_claims", {"run_id": RUN_ID, "claims": claims})
    first, second = results["data"]["results"]
    assert first["verdict"] == second["verdict"] == "shown"
    assert "untrusted" in first
    assert "untrusted" not in second and second["text_omitted"]


@pytest.mark.integration
def test_a_probability_that_is_not_finite_is_refused(server: ToolServer) -> None:
    opened(server)
    _, latest = revenue_ids(server)
    claim = {"section": "bear_case", "type": "estimate", "text": f"Slower {ph(latest)}.",
             "cites": [latest], "probability": float("nan")}  # fmt: skip
    result = server.call("dossier_submit_claims", {"run_id": RUN_ID, "claims": [claim]})
    assert result["error"]["code"] == "invalid_input"
    log = (Path(server._config.runs.folder) / RUN_ID / "calls.jsonl").read_text(encoding="utf-8")
    assert "NaN" not in log
