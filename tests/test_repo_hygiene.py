"""Source files must not contain characters a reader cannot see."""

import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_CONTROLS = {chr(9), chr(13)}  # tab and carriage return


def hidden_characters(text: str) -> list[tuple[int, str]]:
    found = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for char in line:
            category = unicodedata.category(char)
            if category in {"Cf", "Zl", "Zp"} or (
                category == "Cc" and char not in ALLOWED_CONTROLS
            ):
                found.append((line_number, f"U+{ord(char):04X}"))
    return found


@pytest.mark.unit
def test_no_python_file_contains_raw_invisible_characters() -> None:
    # Write such characters with chr() instead: a raw one cannot be seen in review.
    offenders = {
        str(path.relative_to(ROOT)): hidden_characters(path.read_text(encoding="utf-8"))
        for folder in ("src", "tests")
        for path in (ROOT / folder).rglob("*.py")
    }
    assert {path: found for path, found in offenders.items() if found} == {}
