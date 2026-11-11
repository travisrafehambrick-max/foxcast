"""The conformance suite, run against the reference backend (no model).

Every model backend must later pass the same tasks through the same tools (scorecard
row 4b). Each task runs three times and must pass every time.
"""

from pathlib import Path

import pytest

from foxcast.conformance import ReferenceBackend, Task, load_tasks, run_task

HERE = Path(__file__).parent
TASKS = load_tasks(HERE / "tasks")
FIXTURES = HERE.parent / "fixtures"


@pytest.mark.conformance
@pytest.mark.parametrize("task", TASKS, ids=[task.id for task in TASKS])
def test_reference_backend_passes(task: Task) -> None:
    result = run_task(ReferenceBackend(FIXTURES), task)
    assert result.passed, result.errors
