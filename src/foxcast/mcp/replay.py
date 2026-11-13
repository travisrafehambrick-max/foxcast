"""Replay a run's call log and check that the same results and dossier come out (Stage 1A.7).

The call log keeps each call's tool, arguments, start time, duration and the SHA-256 of its
result. :func:`replay_run` sends the same calls, in order, to a fresh server over the same
record, passing each logged start time and duration back in, and compares every result's
hash. The render call's result holds the hashes of the dossier's Markdown and JSON, so a
replay that matches call for call has produced the same dossier. No agent session is
needed: CI replays a committed log against fixtures.

Replay against the record as it stood when the run began: a store that already holds the
run's derived facts, or later ingests, can answer differently, and the replay says so.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from foxcast.mcp.dossier_tools import Record
from foxcast.mcp.envelope import canonical, sha256_text
from foxcast.mcp.runs import Run, Runs
from foxcast.mcp.server import ServerConfig, Timing, ToolServer
from foxcast.store.archive import SourceArchive
from foxcast.store.pit import FactStore


@dataclass(frozen=True)
class Mismatch:
    seq: int
    tool: str
    expected_sha256: str
    replayed_sha256: str


@dataclass(frozen=True)
class ReplayReport:
    calls: int
    mismatches: tuple[Mismatch, ...]
    dossier: dict[str, Any] | None  # the replayed render result's data, if a render ran

    @property
    def ok(self) -> bool:
        return self.calls > 0 and not self.mismatches


def replay_run(
    recorded: Path, *, store: FactStore, archive: SourceArchive, work_dir: Path
) -> ReplayReport:
    """Replay the run in ``recorded`` (a run folder) into the empty folder ``work_dir``."""
    if work_dir.exists() and any(work_dir.iterdir()):
        raise ValueError(f"the replay folder {work_dir} must be empty")
    info = json.loads((recorded / "run.json").read_text(encoding="utf-8"))
    calls = Run(recorded).calls()
    server = ToolServer(
        ServerConfig(
            record=Record(store=store, archive=archive, dossier_dir=work_dir / "dossiers"),
            runs=Runs(work_dir / "runs"),
            code_commit=info["code_commit"],
            prompt_sha256=info["prompt_sha256"],
        )
    )
    mismatches = []
    dossier = None
    for call in calls:
        timing = Timing(datetime.fromisoformat(call.started_at), call.seconds)
        result = server.call(call.tool, call.arguments, timing)
        replayed = sha256_text(canonical(result))
        if replayed != call.result_sha256:
            mismatches.append(Mismatch(call.seq, call.tool, call.result_sha256, replayed))
        if call.tool == "dossier_render" and result["ok"]:
            dossier = result["data"]
    return ReplayReport(len(calls), tuple(mismatches), dossier)
