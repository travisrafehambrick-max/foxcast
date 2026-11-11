"""The conformance runner: each task runs three times and must pass every time."""

from pathlib import Path
from typing import Any

import pytest

from foxcast.conformance import Task, answers_match, load_tasks, run_task

TASKS_DIR = Path(__file__).parent / "conformance" / "tasks"


class ScriptedBackend:
    name = "scripted"

    def __init__(self, answers: list[dict[str, Any]]) -> None:
        self._answers = iter(answers)

    def solve(self, task: Task) -> dict[str, Any]:
        return next(self._answers)


TASK = Task(id="t", description="d", kind="k", fixture=None, input={}, expect={"value": 1.0})


@pytest.mark.unit
def test_all_task_files_load() -> None:
    tasks = load_tasks(TASKS_DIR)
    assert len(tasks) >= 5
    assert len({task.id for task in tasks}) == len(tasks)


@pytest.mark.unit
def test_task_passes_only_when_every_run_matches() -> None:
    good = run_task(ScriptedBackend([{"value": 1.0}] * 3), TASK)
    assert good.passed
    assert good.runs == (True, True, True)

    flaky = run_task(ScriptedBackend([{"value": 1.0}, {"value": 2.0}, {"value": 1.0}]), TASK)
    assert not flaky.passed
    assert flaky.runs == (True, False, True)


@pytest.mark.unit
def test_backend_errors_count_as_failed_runs() -> None:
    class Broken:
        name = "broken"

        def solve(self, task: Task) -> dict[str, Any]:
            raise RuntimeError("boom")

    result = run_task(Broken(), TASK)
    assert result.runs == (False, False, False)
    assert "boom" in result.errors[0]


@pytest.mark.unit
def test_answers_match_rules() -> None:
    assert answers_match({"value": 1.0}, {"value": 1.0 + 1e-12, "extra": "ignored"})
    assert not answers_match({"value": 1.0}, {"value": 1.01})
    assert not answers_match({"value": 1.0}, {})
    assert answers_match({"outcome": "no_data"}, {"outcome": "no_data"})
    assert not answers_match({"cik": 222}, {"cik": 111})
