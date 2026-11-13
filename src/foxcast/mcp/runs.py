"""A dossier run on disk: resumable, with its call log and cost meter (Stage 1A.7; row 4c).

One folder per run, named by its ID (``<TICKER>-<as of>``)::

    run.json      what the run is: ticker, CIK, as-of date, the self-reported model,
                  the prompt hash. Written once when the run opens.
    claims.jsonl  every distinct claim the agent submitted, in order. Nothing is ever
                  removed, so row 1c's denominator counts every attempt.
    calls.jsonl   the call log: one line per tool call with the tool, its arguments, the
                  hash and size of its result and its timing. Results are not stored, so
                  the log holds no third-party text (plan 1A.8).
    record.json   the run record, written when the dossier is rendered.

Everything is appended a line at a time and flushed to disk, so a restart loses at most
the call in flight; a torn last line is cut off on the next write. Opening a run with the
same ID resumes it.
"""

import json
import os
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

from foxcast.mcp.envelope import ErrorCode, ToolError

NEWLINE: Final = "\n"
UNBOUND_LOG: Final = "unbound-calls.jsonl"  # calls that named no open run
# A run ID names a folder, so it is matched in full before any path is built from it.
RUN_ID: Final = re.compile(r"[A-Z0-9][A-Z0-9.-]{0,9}-[0-9]{4}-[0-9]{2}-[0-9]{2}")


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    ticker: str
    cik: int
    company: str | None
    as_of: str
    model_name: str
    model_cutoff: str
    holding: str
    prompt_sha256: str
    ticker_verified: bool
    opened_at: str
    code_commit: str | None


@dataclass(frozen=True)
class CallRecord:
    seq: int
    tool: str
    arguments: Any
    ok: bool
    result_sha256: str
    result_bytes: int
    started_at: str
    seconds: float


@dataclass(frozen=True)
class RunCostSoFar:
    tool_calls: int
    result_bytes: int
    wall_seconds: float


def _lines(path: Path) -> Iterator[dict[str, Any]]:
    """The complete lines of a JSON-lines file; a torn last line is skipped."""
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    complete = text if text.endswith(NEWLINE) else text[: text.rfind(NEWLINE) + 1]
    for number, line in enumerate(complete.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except ValueError as error:
            raise ValueError(
                f"{path.name} line {number} is damaged; a person must repair it"
            ) from error


def _append(path: Path, value: Any) -> None:
    """Append one JSON line and flush it to disk, cutting off a torn tail first."""
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if text and not text.endswith(NEWLINE):
            temporary = path.with_name(path.name + ".tmp")
            kept = text[: text.rfind(NEWLINE) + 1]
            temporary.write_text(kept, encoding="utf-8", newline=NEWLINE)
            os.replace(temporary, path)
    line = json.dumps(value, sort_keys=True, ensure_ascii=True) + NEWLINE
    with path.open("a", encoding="utf-8", newline=NEWLINE) as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def write_json_atomic(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + NEWLINE,
        encoding="utf-8",
        newline=NEWLINE,
    )
    os.replace(temporary, path)


class Run:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    @property
    def info(self) -> RunInfo:
        data = json.loads((self.folder / "run.json").read_text(encoding="utf-8"))
        return RunInfo(**data)

    def claims(self) -> list[dict[str, Any]]:
        return list(_lines(self.folder / "claims.jsonl"))

    def add_claims(self, claims: list[dict[str, Any]]) -> list[bool]:
        """Append claims not already submitted; True for each one that is new."""
        known = {c["claim_id"] for c in self.claims()}
        added = []
        for claim in claims:
            new = claim["claim_id"] not in known
            if new:
                _append(self.folder / "claims.jsonl", claim)
                known.add(claim["claim_id"])
            added.append(new)
        return added

    def calls(self) -> list[CallRecord]:
        return [CallRecord(**line) for line in _lines(self.folder / "calls.jsonl")]

    def log(self, record: CallRecord) -> None:
        _append(self.folder / "calls.jsonl", asdict(record))

    def cost(self) -> RunCostSoFar:
        calls = self.calls()
        return RunCostSoFar(
            tool_calls=len(calls),
            result_bytes=sum(c.result_bytes for c in calls),
            wall_seconds=round(sum(c.seconds for c in calls), 3),
        )

    @property
    def record_path(self) -> Path:
        return self.folder / "record.json"

    def record(self) -> dict[str, Any] | None:
        if not self.record_path.exists():
            return None
        value: dict[str, Any] = json.loads(self.record_path.read_text(encoding="utf-8"))
        return value

    @property
    def rendered(self) -> bool:
        return self.record_path.exists()


class Runs:
    """The runs folder: one subfolder per run, plus a log of calls that named none."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def find(self, run_id: str | None) -> Run | None:
        """The run with this ID if it exists; None otherwise. Safe for unchecked input."""
        if run_id is None or not RUN_ID.fullmatch(run_id):
            return None
        run = Run(self.folder / run_id)
        return run if (run.folder / "run.json").exists() else None

    def get(self, run_id: str) -> Run:
        run = Run(self.folder / run_id)
        if not (run.folder / "run.json").exists():
            raise ToolError(
                ErrorCode.NOT_FOUND,
                f"no run {run_id!r}; open it with research_get_context first",
            )
        return run

    def open(self, info: RunInfo) -> tuple[Run, bool]:
        """The run for ``info``: created if new, resumed if the same run exists."""
        run = Run(self.folder / info.run_id)
        if (run.folder / "run.json").exists():
            existing = run.info
            fixed = ("cik", "model_name", "model_cutoff", "holding")
            changed = [f for f in fixed if getattr(existing, f) != getattr(info, f)]
            if changed:
                raise ToolError(
                    ErrorCode.CONFLICT,
                    f"run {info.run_id} exists with a different {', '.join(changed)}; "
                    "resume it with the values it was opened with (one run per ticker and "
                    "date in a runs folder)",
                )
            return run, False
        run.folder.mkdir(parents=True, exist_ok=True)
        write_json_atomic(run.folder / "run.json", asdict(info))
        return run, True

    def log_unbound(self, record: CallRecord) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        _append(self.folder / UNBOUND_LOG, asdict(record))
