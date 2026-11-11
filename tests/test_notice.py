"""The research-not-advice notice is fixed text, reused everywhere (brief 2.1 section 19)."""

from pathlib import Path

import pytest

from foxcast.notice import NOTICE_HEADLINE, NOTICE_TEXT

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.unit
def test_readme_carries_the_notice() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert NOTICE_HEADLINE in readme
    assert NOTICE_TEXT in readme


@pytest.mark.unit
def test_notice_says_it_is_not_advice_and_places_no_orders() -> None:
    assert "Nothing it outputs is financial, investment or trading advice" in NOTICE_TEXT
    assert "places no orders" in NOTICE_TEXT
