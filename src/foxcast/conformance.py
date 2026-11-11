"""The conformance suite runner (brief F21, scorecard row 4b).

A task is a TOML file with an input and an expected answer. A backend solves tasks; it
passes a task only if all three runs match. The reference backend answers by calling
foxcast's own functions directly, with no model; model backends added later must reach the
same answers through the same tools.
"""

import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final, Protocol

import httpx

from foxcast.sources.base import Fetched, NoData, run_provider
from foxcast.sources.sec import ConceptQuery, SecConceptProvider, first_reported, latest_reported
from foxcast.sources.tickers import TickerTable

RUNS_PER_TASK: Final = 3
FLOAT_TOLERANCE: Final = 1e-9
NOT_FOUND_FIXTURE: Final = "__404__"
REFERENCE_CONTACT: Final = "foxcast conformance conformance@example.invalid"


@dataclass(frozen=True)
class Task:
    id: str
    description: str
    kind: str
    fixture: str | None
    input: Mapping[str, Any]
    expect: Mapping[str, Any]


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    runs: tuple[bool, ...]
    errors: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return all(self.runs)


class Backend(Protocol):
    name: str

    def solve(self, task: Task) -> dict[str, Any]: ...


def load_tasks(directory: Path) -> list[Task]:
    tasks = []
    for path in sorted(directory.glob("*.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        tasks.append(
            Task(
                id=data["id"],
                description=data["description"],
                kind=data["kind"],
                fixture=data.get("fixture"),
                input=data["input"],
                expect=data["expect"],
            )
        )
    return tasks


def _values_match(expected: object, actual: object) -> bool:
    if isinstance(expected, float) or isinstance(actual, float):
        if not isinstance(actual, int | float) or not isinstance(expected, int | float):
            return False
        return math.isclose(expected, actual, rel_tol=FLOAT_TOLERANCE)
    return expected == actual


def answers_match(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    """Every expected key must be present and equal; extra keys are ignored."""
    return all(
        key in actual and _values_match(value, actual[key]) for key, value in expected.items()
    )


def run_task(backend: Backend, task: Task, runs: int = RUNS_PER_TASK) -> TaskResult:
    outcomes: list[bool] = []
    errors: list[str] = []
    for attempt in range(runs):
        try:
            answer = backend.solve(task)
        except Exception as exc:
            outcomes.append(False)
            errors.append(f"run {attempt + 1}: {type(exc).__name__}: {exc}")
            continue
        matched = answers_match(task.expect, answer)
        outcomes.append(matched)
        if not matched:
            errors.append(f"run {attempt + 1}: expected {dict(task.expect)}, got {answer}")
    return TaskResult(task.id, tuple(outcomes), tuple(errors))


class ReferenceBackend:
    """Answers tasks with foxcast's own functions and recorded fixtures; no model, no network."""

    name = "reference"

    def __init__(self, fixtures: Path) -> None:
        self._fixtures = fixtures

    def solve(self, task: Task) -> dict[str, Any]:
        if task.kind == "sec_concept":
            return self._sec_concept(task)
        if task.kind == "ticker_lookup":
            return self._ticker_lookup(task)
        raise ValueError(f"unknown task kind: {task.kind}")

    def _client(self, fixture: str | None) -> httpx.Client:
        if fixture is None or fixture == NOT_FOUND_FIXTURE:
            return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
        body = (self._fixtures / fixture).read_bytes()
        return httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
        )

    def _sec_concept(self, task: Task) -> dict[str, Any]:
        args = task.input
        query = ConceptQuery(cik=int(args["cik"]), tag=str(args["tag"]))
        with self._client(task.fixture) as client:
            result = run_provider(SecConceptProvider(REFERENCE_CONTACT), query, client)
        if isinstance(result, NoData):
            return {"outcome": "no_data"}
        if not isinstance(result, Fetched):
            return {"outcome": "unavailable", "reason": result.reason}
        as_of = date.fromisoformat(args["as_of"]) if "as_of" in args else None
        pick = first_reported if args["mode"] == "first_reported" else latest_reported
        table = pick(result.table, as_of)
        row = table[table["period_end"] == date.fromisoformat(args["period_end"])].iloc[0]
        return {"outcome": "fetched", "value": float(row["value"]), "accession": row["accession"]}

    def _ticker_lookup(self, task: Task) -> dict[str, Any]:
        table = TickerTable()
        for snapshot in task.input["snapshots"]:
            table = table.observe(snapshot["tickers"], as_of=date.fromisoformat(snapshot["as_of"]))
        key = table.lookup(task.input["ticker"], date.fromisoformat(task.input["on"]))
        return {"cik": key.cik if key else None}
