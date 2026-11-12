"""SCORECARD.md and scorecard.toml must describe the same 20 rows (honesty rule 1)."""

import re
import tomllib
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from foxcast.scoring.bounds import clopper_pearson_upper, max_errors_below
from foxcast.universe import UniverseSpec, select_universe
from foxcast.watchlist import draw_watchlist, validate_ticker

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


# --- Stage 1A.1: the measures for rows 1c and 1e, written before any dossier ---

MEASURES_HEADING = "### How rows 1c and 1e are measured"
PLANTED_CATEGORIES = {
    "altered number",
    "altered quote",
    "wrong period",
    "wrong unit",
    "wrong company",
    "wrong metric",
}
RULES_1C = (
    "denominator",
    "verified",
    "number_match",
    "passage_match",
    "text_match",
    "words_match",
    "planted_rule",
    "planted_report",
)
RULES_1E = ("audit_tickers_rule", "sample", "method", "new_to_me")


def toml_data() -> dict[str, Any]:
    return tomllib.loads((ROOT / "scorecard.toml").read_text(encoding="utf-8"))


def measures_section() -> str:
    text = (ROOT / "SCORECARD.md").read_text(encoding="utf-8")
    start = text.index(MEASURES_HEADING)
    return text[start : text.index("## Change log", start)]


def change_log() -> str:
    text = (ROOT / "SCORECARD.md").read_text(encoding="utf-8")
    return text.split("## Change log", 1)[1]


@pytest.mark.unit
@pytest.mark.parametrize("key", RULES_1C)
def test_each_row_1c_rule_appears_word_for_word_in_the_markdown(key: str) -> None:
    rule = toml_data()["definition"]["1c"][key]
    assert len(rule) > 40, key
    assert rule in measures_section(), key


@pytest.mark.unit
@pytest.mark.parametrize("key", RULES_1E)
def test_each_row_1e_rule_appears_word_for_word_in_the_markdown(key: str) -> None:
    rule = toml_data()["definition"]["1e"][key]
    assert len(rule) > 40, key
    assert rule in measures_section(), key


@pytest.mark.unit
def test_the_rubric_is_numbered_in_the_markdown_in_the_same_order() -> None:
    rubric = toml_data()["definition"]["1e"]["rubric"]
    section = measures_section()
    assert len(rubric) == 5
    for number, item in enumerate(rubric, start=1):
        assert f"  {number}. {item}\n" in section, number


@pytest.mark.unit
def test_the_planted_claims_match_the_markdown() -> None:
    d = toml_data()["definition"]["1c"]
    section = measures_section()
    assert set(d["planted_categories"]) == PLANTED_CATEGORIES
    for category in d["planted_categories"]:
        assert category in section, category
    assert f"{d['planted_per_category']} per category, drawn with seed `{d['planted_seed']}`" in (
        section
    )


@pytest.mark.unit
def test_row_1e_definition_agrees_with_its_own_arithmetic() -> None:
    d = toml_data()["definition"]["1e"]
    assert d["claims"] == d["claims_per_ticker"] * len(d["audit_tickers"])
    assert d["max_wrong"] == max_errors_below(d["claims"], d["bound_below"], d["confidence"])
    assert d["max_wrong"] == 1  # what the owner was told: about 100 claims, at most one wrong


@pytest.mark.unit
def test_the_bounds_quoted_in_the_markdown_are_computed_not_typed() -> None:
    d = toml_data()["definition"]["1e"]
    section = measures_section()
    for errors in range(3):
        bound = clopper_pearson_upper(errors, d["claims"], confidence=d["confidence"])
        assert f"{bound * 100:.2f}%" in section, errors
    smallest = next(n for n in range(1, 200) if max_errors_below(n, d["bound_below"]) >= 0)
    assert f"Fewer than {smallest} marked claims cannot pass" in section
    assert f"a bound below {d['bound_below'] * 100:.0f}%" in section


@pytest.mark.unit
def test_row_1e_sample_matches_the_markdown() -> None:
    d = toml_data()["definition"]["1e"]
    section = measures_section()
    assert d["audit_tickers"] == ["AAPL", "NVDA", "MSFT", "GOOGL", "AMZN"]
    assert f"**Audit tickers:** {', '.join(d['audit_tickers'])}." in section
    expected = (
        f"{d['claims_per_ticker']} claims per ticker, {d['claims']} in all, "
        f"with seed `{d['sampler_seed']}`"
    )
    assert expected in section


@pytest.mark.unit
def test_row_1e_measure_names_the_audit_tickers_in_both_files() -> None:
    measure = toml_rows()["1e"]["measure"]
    assert "you know well" not in measure
    assert "AAPL, NVDA, MSFT, GOOGL, AMZN" in measure
    text = (ROOT / "SCORECARD.md").read_text(encoding="utf-8")
    row_line = next(line for line in text.splitlines() if line.startswith("| 1e |"))
    assert measure in row_line


@pytest.mark.unit
def test_the_gate_set_is_twenty_distinct_valid_tickers_that_match_the_markdown() -> None:
    gate = toml_data()["gate_set"]["1a"]
    tickers = gate["tickers"]
    assert len(tickers) == gate["size"] == 20
    assert tickers == sorted(set(tickers))
    for ticker in tickers:
        assert validate_ticker(ticker) == ticker
    section = measures_section()
    assert f"judged on these 20 tickers: {', '.join(tickers)}." in section
    assert f"drawn with seed `{gate['seed']}`" in section
    assert f"`{gate['universe_source']}`" in section
    assert f"SHA-256 `{gate['universe_sha256']}`" in section
    assert gate["as_of_rule"] in section


@pytest.mark.unit
def test_the_seeds_are_all_different() -> None:
    data = toml_data()
    seeds = [
        data["gate_set"]["1a"]["seed"],
        data["definition"]["1c"]["planted_seed"],
        data["definition"]["1e"]["sampler_seed"],
    ]
    assert len(set(seeds)) == len(seeds)


@pytest.mark.unit
def test_the_gate_set_redraws_from_the_universe_file_when_it_is_present() -> None:
    gate = toml_data()["gate_set"]["1a"]
    csv_path = Path.home() / ".foxcast" / "data" / "universe" / gate["universe_source"]
    if not csv_path.exists():
        pytest.skip("the universe file lives in the private data repo")
    spec = UniverseSpec(
        sectors=tuple(gate["sectors"]), sub_industries=tuple(gate["sub_industries"])
    )
    drawn = draw_watchlist(
        select_universe(csv_path.read_text(encoding="utf-8"), spec),
        size=gate["size"],
        seed=gate["seed"],
        drawn_on=date.fromisoformat(gate["drawn_on"]),
        universe_source=csv_path.name,
    )
    assert drawn.universe_sha256 == gate["universe_sha256"]
    assert list(drawn.tickers) == gate["tickers"]


@pytest.mark.unit
def test_the_change_log_records_the_definitions_and_their_effect() -> None:
    entry = next(line for line in change_log().splitlines() if "rows 1c and 1e" in line)
    assert entry.startswith("- **2026-11-11**")
    assert "No pass line changed" in entry
    assert "at most one wrong claim" in entry
