"""The llm claims backend seam: ToolCaller + ClaimsBackend + reference fixture agent."""

import re
from pathlib import Path
from typing import Any

import pytest

from foxcast.llm.claims import ClaimsRequest, ReferenceClaimsBackend
from foxcast.llm.tools import InProcessToolCaller
from foxcast.mcp.server import ToolServer

from .mcp_session import MODEL, RUN_ID, copy_world, make_server, make_world
from .test_checker import AS_OF
from .test_sec_ingest import GOOGL

MODELS = Path(__file__).resolve().parents[1] / "docs" / "MODELS.md"
DIGIT = re.compile(r"\d")


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("llm-world")
    make_world(folder)
    return folder


@pytest.fixture
def server(template: Path, tmp_path: Path) -> ToolServer:
    return make_server(copy_world(template, tmp_path / "world"), tmp_path / "work")


@pytest.fixture
def request_googl() -> ClaimsRequest:
    return ClaimsRequest(
        ticker="GOOGL",
        cik=GOOGL,
        as_of=AS_OF.isoformat(),
        model_name=MODEL["model_name"],
        model_cutoff=MODEL["model_cutoff"],
    )


@pytest.mark.unit
def test_in_process_caller_dispatches_through_tool_server_call(
    server: ToolServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []
    real = server.call

    def wrapped(tool: str, arguments: Any, timing: Any = None) -> dict[str, Any]:
        seen.append((tool, dict(arguments)))
        return real(tool, arguments, timing)

    monkeypatch.setattr(server, "call", wrapped)
    caller = InProcessToolCaller(server)
    result = caller.call(
        "research_get_context",
        {
            "ticker": "GOOGL",
            "cik": GOOGL,
            "as_of": AS_OF.isoformat(),
            **MODEL,
        },
    )
    assert result["ok"] is True
    assert seen == [
        (
            "research_get_context",
            {"ticker": "GOOGL", "cik": GOOGL, "as_of": AS_OF.isoformat(), **MODEL},
        )
    ]


@pytest.mark.integration
def test_reference_backend_runs_to_a_rendered_dossier_with_audit_trail(
    server: ToolServer, request_googl: ClaimsRequest
) -> None:
    backend = ReferenceClaimsBackend()
    result = backend.run(InProcessToolCaller(server), request_googl)

    assert backend.name == "reference"
    assert result.run_id == RUN_ID
    assert result.markdown_file == f"{RUN_ID}.md"
    assert result.json_file == f"{RUN_ID}.json"
    assert result.claims_verified >= 1
    assert result.claims_withheld >= 0
    assert set(result.cost) >= {"tool_calls", "result_bytes", "wall_seconds"}
    assert result.cost["tool_calls"] >= 1
    assert len(result.calls) >= 2
    assert result.calls[0].tool == "research_get_context"
    assert result.calls[-1].tool == "dossier_render"
    assert result.calls[-1].result["ok"] is True
    assert "meter" in result.calls[-1].result
    assert result.calls[-1].result["meter"]["tool_calls"] >= 1
    mid = next(c for c in result.calls if c.tool == "sec_get_facts")
    assert {"foxcast_tool", "tool", "ok", "meter", "data", "error"} <= set(mid.result)
    assert mid.arguments["run_id"] == RUN_ID


@pytest.mark.integration
def test_reference_backend_claim_text_uses_fact_ids_not_hardcoded_figures(
    server: ToolServer, request_googl: ClaimsRequest
) -> None:
    backend = ReferenceClaimsBackend()
    result = backend.run(InProcessToolCaller(server), request_googl)
    submit = next(c for c in result.calls if c.tool == "dossier_submit_claims")
    for claim in submit.arguments["claims"]:
        own = re.sub(r"\{[fp]_[0-9a-f]+\}", "", claim["text"])
        assert not DIGIT.search(own), own
        assert claim["cites"], "every claim must cite at least one fact or passage ID"


@pytest.mark.unit
def test_models_md_marks_stage2_stubs_as_stubbed() -> None:
    text = MODELS.read_text(encoding="utf-8")
    assert "**stubbed**" in text
    for row in (
        "| 3 | Event-aware | stubbed | 2 |",
        "| 5 | Option-implied, corrected | stubbed | 2 |",
        "| 6 | Blend (the headline) | stubbed | 2 |",
        "| B | Simple implied-volatility benchmark | stubbed | 2 |",
    ):
        assert row in text
