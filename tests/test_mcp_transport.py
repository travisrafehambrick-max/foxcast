"""The stdio transport, the locked-down room, the launcher and the result header."""

import io
import json
from pathlib import Path
from typing import Any

import pytest

from foxcast.mcp import __main__ as launcher
from foxcast.mcp.envelope import (
    MAX_RESULT_BYTES,
    PAGE_BUDGET_BYTES,
    ErrorCode,
    Meter,
    Outcome,
    ToolError,
    envelope,
    paged,
)
from foxcast.mcp.room import DENIED_TOOLS, settings, write_room
from foxcast.mcp.runs import Run, _append
from foxcast.mcp.server import ToolServer, workflow_text
from foxcast.mcp.stdio import DESCRIPTIONS, handle, serve, tool_list
from foxcast.store.archive import SourceArchive
from foxcast.store.pit import FactStore

from .mcp_session import OPEN, make_server, make_world

World = tuple[FactStore, SourceArchive]
PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return make_world(tmp_path_factory.mktemp("transport-world"))


@pytest.fixture
def server(world: World, tmp_path: Path) -> ToolServer:
    return make_server(world, tmp_path / "work")


def rpc(server: ToolServer, *messages: Any) -> list[dict[str, Any]]:
    out = io.StringIO()
    lines = [m if isinstance(m, str) else json.dumps(m) for m in messages]
    serve(server, [line + "\n" for line in lines], out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


# --- stdio ---


@pytest.mark.integration
def test_the_server_speaks_mcp_over_stdio(server: ToolServer) -> None:
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}}}  # fmt: skip
    note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "research_get_context", "arguments": OPEN}}  # fmt: skip
    replies = rpc(server, init, note, listing, call)
    assert [r["id"] for r in replies] == [1, 2, 3]  # the notification gets no reply
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    assert replies[0]["result"]["capabilities"] == {"tools": {"listChanged": False}}
    names = [t["name"] for t in replies[1]["result"]["tools"]]
    assert names == list(DESCRIPTIONS)
    result = replies[2]["result"]
    assert result["isError"] is False
    body = json.loads(result["content"][0]["text"])
    assert body["ok"] and body["data"]["status"] == "opened"


@pytest.mark.integration
def test_a_refused_call_is_an_mcp_tool_error(server: ToolServer) -> None:
    params = {"name": "research_get_context", "arguments": {"ticker": "GOOGL"}}
    call = {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": params}
    (reply,) = rpc(server, call)
    assert reply["result"]["isError"] is True
    assert json.loads(reply["result"]["content"][0]["text"])["error"]["code"] == "invalid_input"


@pytest.mark.unit
def test_bad_messages_get_json_rpc_errors(server: ToolServer) -> None:
    replies = rpc(
        server,
        "{not json",
        json.dumps([1, 2]),
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "resources/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 6, "method": "ping"}),
        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": {}}),
        "x" * 1_000_001,
    )
    codes = [r.get("error", {}).get("code") for r in replies]
    assert codes == [-32700, -32600, -32601, -32602, None, None, -32600]
    assert replies[4]["result"] == {}
    assert replies[5]["result"]["protocolVersion"] == "2025-11-25"
    assert handle(server, {"jsonrpc": "2.0", "id": 8, "method": 3}) is not None


@pytest.mark.unit
def test_a_failing_call_answers_an_error_and_the_server_keeps_going(
    server: ToolServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: Any, **__: Any) -> dict[str, Any]:
        raise OSError("disk full")

    monkeypatch.setattr(server, "call", broken)
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "sec_list_filings", "arguments": {}}}  # fmt: skip
    replies = rpc(server, call, {"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert replies[0]["error"]["code"] == -32603
    assert "OSError" in replies[0]["error"]["message"]
    assert replies[1]["result"] == {}


@pytest.mark.unit
def test_tools_are_listed_with_schemas_that_forbid_extra_fields() -> None:
    for tool in tool_list():
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert schema.get("additionalProperties") is False, tool["name"]
        assert tool["annotations"]["openWorldHint"] is False
    read_only = {t["name"] for t in tool_list() if t["annotations"]["readOnlyHint"]}
    assert "dossier_render" not in read_only and "sec_read_passages" in read_only


# --- the room ---


@pytest.mark.unit
def test_the_room_allows_only_the_foxcast_tools(tmp_path: Path) -> None:
    room = tmp_path / "room"
    project = tmp_path / "project"  # the test folder itself sits inside the real project
    written = write_room(
        room,
        project=project,
        as_of="2026-11-12",
        companies={"GOOGL": 1652044},
        config=tmp_path / "foxcast.toml",
    )
    assert {p.name for p in written} == {
        ".mcp.json",
        "settings.json",
        "CLAUDE.md",
        "start.ps1",
        "start.sh",
    }
    stored = json.loads((room / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert stored == settings()
    assert stored["permissions"]["allow"] == ["mcp__foxcast"]
    assert stored["permissions"]["defaultMode"] == "dontAsk"
    for tool in ("Bash", "PowerShell", "Read", "Write", "Edit", "WebFetch", "WebSearch", "Agent"):
        assert tool in stored["permissions"]["deny"]
    assert set(DENIED_TOOLS) == set(stored["permissions"]["deny"])
    assert stored["disableClaudeAiConnectors"] is True and stored["disableAllHooks"] is True
    servers = json.loads((room / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert list(servers) == ["foxcast"]
    args = servers["foxcast"]["args"]
    assert args[-7:] == [
        "serve",
        "--config",
        str(tmp_path / "foxcast.toml"),
        "--as-of",
        "2026-11-12",
        "--company",
        "GOOGL:1652044",
    ]
    assert stored["permissions"]["disableBypassPermissionsMode"] == "disable"
    assert (room / "CLAUDE.md").read_text(encoding="utf-8") == workflow_text()
    start = (room / "start.ps1").read_text(encoding="utf-8")
    assert "--strict-mcp-config" in start and '--tools ""' in start
    assert "#Requires -Version 7.3" in start and "PSNativeCommandArgumentPassing" in start


@pytest.mark.unit
def test_the_room_must_be_outside_the_project() -> None:
    with pytest.raises(ValueError, match="outside the foxcast project"):
        write_room(PROJECT / "room", project=PROJECT, as_of="2026-11-12")


@pytest.mark.unit
def test_the_room_must_not_sit_below_instruction_files(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("do something else\n", encoding="utf-8")
    with pytest.raises(ValueError, match="below instruction files"):
        write_room(tmp_path / "room", project=tmp_path / "project", as_of="2026-11-12")
    with pytest.raises(ValueError):
        write_room(tmp_path / "other", project=tmp_path / "project", as_of="2026-13-01")


@pytest.mark.unit
def test_the_workflow_file_carries_the_fiscal_and_untrusted_rules() -> None:
    text = workflow_text()
    for phrase in ("fiscal Q1 2026", "Q1 FY2026", "untrusted", "research_get_context",
                   "dossier_render", "facts_derive", "Never guess a CIK"):  # fmt: skip
        assert phrase in text


# --- the launcher ---


@pytest.mark.unit
def test_the_launcher_writes_a_room(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert launcher.main(["room", str(PROJECT / "inside"), "--as-of", "2026-11-12"]) == 1
    monkeypatch.setattr(launcher, "PROJECT", tmp_path / "project")
    pins = ["--as-of", "2026-11-12", "--company", "GOOGL:1652044"]
    assert launcher.main(["room", str(tmp_path / "room"), *pins]) == 0
    mcp = json.loads((tmp_path / "room" / ".mcp.json").read_text(encoding="utf-8"))
    assert "GOOGL:1652044" in mcp["mcpServers"]["foxcast"]["args"]
    for bad in (["--company", "GOOGL"], ["--company", "googl:1"], ["--as-of", "soon"]):
        with pytest.raises(SystemExit):
            launcher.main(["room", str(tmp_path / "bad"), "--as-of", "2026-11-12", *bad])
    assert (tmp_path / "room" / ".claude" / "settings.json").exists()


@pytest.mark.unit
def test_the_launcher_refuses_to_serve_without_a_store(tmp_path: Path) -> None:
    config = tmp_path / "foxcast.toml"
    config.write_text(f"data_dir = '{(tmp_path / 'data').as_posix()}'\n", encoding="utf-8")
    assert launcher.main(["serve", "--config", str(config)]) == 1


@pytest.mark.integration
def test_the_launcher_serves_with_a_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    FactStore(data / "facts.duckdb")
    config = tmp_path / "foxcast.toml"
    archive = tmp_path / "archive"
    config.write_text(
        f"data_dir = '{data.as_posix()}'\narchive_dir = '{archive.as_posix()}'\n",
        encoding="utf-8",
    )
    served: list[ToolServer] = []
    monkeypatch.setattr(launcher, "serve_stdio", served.append)
    monkeypatch.setattr(launcher.SourceArchive, "outside_git", SourceArchive)
    pins = ["--as-of", "2026-11-11", "--company", "GOOGL:1652044"]
    assert launcher.main(["serve", "--config", str(config), *pins]) == 0
    assert len(served) == 1
    refused = served[0].call("research_get_context", {**OPEN, "as_of": "2026-11-10"})
    assert "pinned to the as-of date 2026-11-11" in refused["error"]["message"]


# --- the result header and paging ---


@pytest.mark.unit
def test_an_oversized_result_becomes_a_limit_error() -> None:
    big = Outcome({"items": ["x" * (MAX_RESULT_BYTES + 1)]})
    result = envelope("sec_get_facts", run_id="R", as_of="2026-11-11", meter=Meter(), outcome=big)
    assert result["ok"] is False and result["error"]["code"] == str(ErrorCode.LIMIT)


@pytest.mark.unit
def test_a_page_fits_the_byte_budget_but_always_moves_forward() -> None:
    items = ["x" * (PAGE_BUDGET_BYTES // 2)] * 5
    chosen, page = paged(items, 0, 10)
    assert len(chosen) == 1 and page.next_offset == 1
    huge = ["x" * (PAGE_BUDGET_BYTES * 2)]
    chosen, page = paged(huge, 0, 10)
    assert len(chosen) == 1 and page.next_offset is None
    with pytest.raises(ToolError):
        paged(items, 6, 10)


@pytest.mark.unit
def test_a_torn_last_line_is_dropped_and_cut_on_the_next_write(tmp_path: Path) -> None:
    run = Run(tmp_path)
    path = tmp_path / "claims.jsonl"
    path.write_text('{"claim_id": "c_1"}\n{"claim_', encoding="utf-8")
    assert [c["claim_id"] for c in run.claims()] == ["c_1"]
    _append(path, {"claim_id": "c_2"})
    assert [c["claim_id"] for c in run.claims()] == ["c_1", "c_2"]
    assert run.record() is None and not run.rendered
