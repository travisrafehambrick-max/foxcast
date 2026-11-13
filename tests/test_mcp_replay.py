"""Replaying a call log gives the same results and the same dossier (Stage 1A.7).

``tests/fixtures/mcp_replay/`` holds a committed run: its ``run.json``, its call log and
its run record, from the scripted session on the synthetic world (no third-party text:
the log holds the agent's arguments and result hashes only). CI replays it, so a change
that alters any tool result or the dossier fails here until the fixture is rewritten on
purpose (see ``tests/mcp_session.py``).
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from foxcast.mcp.replay import replay_run

from .mcp_session import RUN_ID, copy_world, make_server, make_world, scripted_session

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_replay" / RUN_ID
REWRITE = os.environ.get("FOXCAST_WRITE_REPLAY_FIXTURE") == "1"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("replay-world")
    make_world(folder)
    return folder


def record_session(template: Path, folder: Path) -> Path:
    server = make_server(copy_world(template, folder / "world"), folder / "work")
    scripted_session(server)
    return folder / "work" / "runs" / RUN_ID


@pytest.mark.integration
def test_a_session_replays_to_the_same_dossier(template: Path, tmp_path: Path) -> None:
    recorded = record_session(template, tmp_path / "first")
    store, archive = copy_world(template, tmp_path / "second-world")
    report = replay_run(recorded, store=store, archive=archive, work_dir=tmp_path / "replay")
    assert report.ok, report.mismatches
    assert report.calls == 9
    original = json.loads((recorded / "record.json").read_text(encoding="utf-8"))["dossier"]
    assert report.dossier is not None
    assert report.dossier["json_sha256"] == original["json_sha256"]
    assert report.dossier["markdown_sha256"] == original["markdown_sha256"]
    first = (tmp_path / "first" / "work" / "dossiers" / original["json_file"]).read_bytes()
    again = (tmp_path / "replay" / "dossiers" / original["json_file"]).read_bytes()
    assert first == again


@pytest.mark.integration
def test_a_changed_result_is_caught(template: Path, tmp_path: Path) -> None:
    recorded = record_session(template, tmp_path / "first")
    log = recorded / "calls.jsonl"
    lines = log.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[2])
    tampered["result_sha256"] = "0" * 64
    lines[2] = json.dumps(tampered, sort_keys=True)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    store, archive = copy_world(template, tmp_path / "second-world")
    report = replay_run(recorded, store=store, archive=archive, work_dir=tmp_path / "replay")
    assert not report.ok
    assert [(m.seq, m.tool) for m in report.mismatches] == [(2, "sec_get_facts")]


@pytest.mark.integration
def test_a_replay_needs_an_empty_folder(template: Path, tmp_path: Path) -> None:
    recorded = record_session(template, tmp_path / "first")
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "x").write_text("x", encoding="utf-8")
    store, archive = copy_world(template, tmp_path / "second-world")
    with pytest.raises(ValueError, match="must be empty"):
        replay_run(recorded, store=store, archive=archive, work_dir=busy)


@pytest.mark.integration
def test_the_committed_call_log_replays_to_the_committed_dossier(
    template: Path, tmp_path: Path
) -> None:
    if REWRITE:
        recorded = record_session(template, tmp_path / "rewrite")
        FIXTURE.mkdir(parents=True, exist_ok=True)
        for name in ("run.json", "calls.jsonl", "record.json"):
            shutil.copyfile(recorded / name, FIXTURE / name)
    store, archive = copy_world(template, tmp_path / "world")
    report = replay_run(FIXTURE, store=store, archive=archive, work_dir=tmp_path / "replay")
    assert report.ok, report.mismatches
    committed = json.loads((FIXTURE / "record.json").read_text(encoding="utf-8"))["dossier"]
    assert report.dossier is not None
    assert report.dossier["json_sha256"] == committed["json_sha256"]
    assert report.dossier["markdown_sha256"] == committed["markdown_sha256"]
