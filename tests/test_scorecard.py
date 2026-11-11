"""SCORECARD.md and scorecard.toml must describe the same 20 rows (honesty rule 1)."""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROW_PATTERN = re.compile(r"^\| (?P<id>[1-4][a-f]) \| (?P<claim>[^|]+?) \|", re.MULTILINE)
VALID_STATUSES = {"pass", "fail", "not yet measurable"}


def markdown_rows() -> dict[str, str]:
    text = (ROOT / "SCORECARD.md").read_text(encoding="utf-8")
    return {m["id"]: m["claim"].strip() for m in ROW_PATTERN.finditer(text)}


def toml_rows() -> dict[str, dict[str, str]]:
    data = tomllib.loads((ROOT / "scorecard.toml").read_text(encoding="utf-8"))
    return {row["id"]: row for row in data["row"]}


@pytest.mark.unit
def test_markdown_has_twenty_rows() -> None:
    assert len(markdown_rows()) == 20


@pytest.mark.unit
def test_toml_and_markdown_have_the_same_rows_and_claims() -> None:
    md = markdown_rows()
    toml = toml_rows()
    assert md.keys() == toml.keys()
    for row_id, claim in md.items():
        assert toml[row_id]["claim"] == claim, row_id


@pytest.mark.unit
def test_every_row_has_a_valid_status_and_a_stage() -> None:
    for row_id, row in toml_rows().items():
        assert row["status"] in VALID_STATUSES, row_id
        assert row["stage"].strip(), row_id
