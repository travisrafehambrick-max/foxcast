"""The Stage 1A gate dossier runner: the frozen dossier set rows 1c and 1e are read from."""

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from foxcast import __version__
from foxcast.claims.checker import WORDS_CHECK_VERSION
from foxcast.gate import (
    RUN_RECORD,
    SCHEMA,
    GateRequest,
    GateRunError,
    TickerSet,
    load_ticker_set,
    run_gate,
    verify_gate_run,
)
from foxcast.store.archive import SourceArchive
from foxcast.store.pit import FactStore

from .test_checker import AS_OF
from .test_sec_ingest import GOOGL

ROOT = Path(__file__).resolve().parents[1]
BUILT_AT = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
COMMIT = "0" * 40
World = tuple[FactStore, SourceArchive]

SMALL_SCORECARD = """
[definition.1c]
planted_seed = 3

[definition.1e]
audit_tickers = ["GOOGL"]
sampler_seed = 2

[gate_set.1a]
rows = ["1a", "1c"]
size = {size}
seed = 1
tickers = {tickers}
"""


def small_set(tmp_path: Path, tickers: list[str], which: str = "gate") -> TickerSet:
    path = tmp_path / "scorecard.toml"
    path.write_text(
        SMALL_SCORECARD.format(size=len(tickers), tickers=json.dumps(tickers)), encoding="utf-8"
    )
    return load_ticker_set(path, which)  # type: ignore[arg-type]


def gate_request(ticker_set: TickerSet, **changes: object) -> GateRequest:
    values: dict[str, object] = {
        "ticker_set": ticker_set,
        "as_of": AS_OF,
        "built_at": BUILT_AT,
        "companies": {"GOOGL": GOOGL},
        "code_commit": COMMIT,
    }
    values.update(changes)
    return GateRequest(**values)  # type: ignore[arg-type]


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- the ticker sets come from scorecard.toml, not from code ---


@pytest.mark.unit
def test_the_gate_set_is_the_twenty_tickers_and_seed_in_scorecard_toml() -> None:
    gate = load_ticker_set(ROOT / "scorecard.toml", "gate")
    assert gate.name == "gate-1a"
    assert len(gate.tickers) == 20
    assert gate.tickers[:3] == ("AIG", "ALL", "AON")
    assert gate.rows == ("1a", "1b", "1c", "1d", "4c")
    assert dict(gate.seeds) == {
        "gate_set_draw": 2026111101,
        "sampler_1e": 2026111102,
        "planted_1c": 2026111103,
    }
    raw = (ROOT / "scorecard.toml").read_bytes()
    assert gate.scorecard_sha256 == hashlib.sha256(raw).hexdigest()


@pytest.mark.unit
def test_the_audit_set_is_the_five_row_1e_tickers() -> None:
    audit = load_ticker_set(ROOT / "scorecard.toml", "audit")
    assert audit.name == "audit-1e"
    assert audit.tickers == ("AAPL", "NVDA", "MSFT", "GOOGL", "AMZN")
    assert audit.rows == ("1e",)


@pytest.mark.unit
def test_a_gate_set_whose_size_does_not_match_its_tickers_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "scorecard.toml"
    path.write_text(SMALL_SCORECARD.format(size=2, tickers='["GOOGL"]'), encoding="utf-8")
    with pytest.raises(GateRunError, match="size"):
        load_ticker_set(path, "gate")


@pytest.mark.unit
def test_a_repeated_ticker_is_refused(tmp_path: Path) -> None:
    with pytest.raises(GateRunError, match="twice"):
        small_set(tmp_path, ["GOOGL", "GOOGL"])


# --- a run ---


@pytest.mark.unit
def test_a_run_writes_each_dossier_and_a_run_record_with_their_hashes(
    world: World, tmp_path: Path
) -> None:
    store, archive = world
    ticker_set = small_set(tmp_path, ["GOOGL"])
    result = run_gate(gate_request(ticker_set), store, archive, tmp_path / "gate")

    assert result.folder == tmp_path / "gate" / f"gate-1a-{AS_OF.isoformat()}"
    record = json.loads((result.folder / RUN_RECORD).read_text(encoding="utf-8"))
    assert record["schema"] == SCHEMA
    assert record["set"] == "gate-1a"
    assert record["as_of"] == AS_OF.isoformat()
    assert record["built_at"] == BUILT_AT.isoformat()
    assert record["seeds"] == {"gate_set_draw": 1, "sampler_1e": 2, "planted_1c": 3}
    assert record["code"] == {
        "version": __version__,
        "commit": COMMIT,
        "clean": True,
        "words_check_version": WORDS_CHECK_VERSION,
    }
    assert record["scorecard_sha256"] == ticker_set.scorecard_sha256
    assert record["model"] is None
    [entry] = record["dossiers"]
    assert entry["ticker"] == "GOOGL"
    assert entry["cik"] == GOOGL
    for kind in ("markdown", "json"):
        path = result.folder / entry[f"{kind}_file"]
        assert entry[f"{kind}_sha256"] == sha256_of(path)
    assert entry["sections_filled"] + entry["sections_unavailable"] == 19
    assert entry["claims_counted"] == 0
    assert record["dossiers_sha256"] == result.dossiers_sha256


@pytest.mark.unit
def test_the_same_inputs_give_the_same_hashes(world: World, tmp_path: Path) -> None:
    store, archive = world
    ticker_set = small_set(tmp_path, ["GOOGL"])
    first = run_gate(gate_request(ticker_set), store, archive, tmp_path / "one")
    second = run_gate(gate_request(ticker_set), store, archive, tmp_path / "two")
    assert first.dossiers_sha256 == second.dossiers_sha256


@pytest.mark.unit
def test_a_frozen_run_is_never_rebuilt(world: World, tmp_path: Path) -> None:
    store, archive = world
    ticker_set = small_set(tmp_path, ["GOOGL"])
    result = run_gate(gate_request(ticker_set), store, archive, tmp_path / "gate")
    before = (result.folder / RUN_RECORD).read_bytes()
    later = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)
    with pytest.raises(GateRunError, match="frozen"):
        run_gate(gate_request(ticker_set, built_at=later), store, archive, tmp_path / "gate")
    assert (result.folder / RUN_RECORD).read_bytes() == before


@pytest.mark.unit
def test_the_audit_set_runs_into_its_own_folder(world: World, tmp_path: Path) -> None:
    store, archive = world
    audit = small_set(tmp_path, ["AIG"], which="audit")  # audit tickers are ["GOOGL"]
    result = run_gate(gate_request(audit), store, archive, tmp_path / "gate")
    assert result.folder.name == f"audit-1e-{AS_OF.isoformat()}"


# --- refused before any dossier is built ---


@pytest.mark.unit
def test_a_ticker_without_a_cik_is_refused_before_any_dossier(world: World, tmp_path: Path) -> None:
    store, archive = world
    ticker_set = small_set(tmp_path, ["GOOGL", "AAPL"])
    with pytest.raises(GateRunError, match="AAPL"):
        run_gate(gate_request(ticker_set), store, archive, tmp_path / "gate")
    assert not (tmp_path / "gate").exists()


@pytest.mark.unit
def test_a_ticker_with_nothing_in_the_record_is_refused_before_any_dossier(
    world: World, tmp_path: Path
) -> None:
    store, archive = world
    ticker_set = small_set(tmp_path, ["GOOGL", "AAPL"])
    request = gate_request(ticker_set, companies={"GOOGL": GOOGL, "AAPL": 320193})
    with pytest.raises(GateRunError, match=r"nothing in the record.*AAPL"):
        run_gate(request, store, archive, tmp_path / "gate")
    assert not (tmp_path / "gate").exists()


@pytest.mark.unit
def test_two_tickers_on_one_cik_are_refused(world: World, tmp_path: Path) -> None:
    store, archive = world
    ticker_set = small_set(tmp_path, ["GOOGL", "GOOG"])
    request = gate_request(ticker_set, companies={"GOOGL": GOOGL, "GOOG": GOOGL})
    with pytest.raises(GateRunError, match="same CIK"):
        run_gate(request, store, archive, tmp_path / "gate")


@pytest.mark.unit
def test_an_as_of_date_after_the_build_is_refused(world: World, tmp_path: Path) -> None:
    store, archive = world
    request = gate_request(small_set(tmp_path, ["GOOGL"]), as_of=date(2026, 11, 15))
    with pytest.raises(GateRunError, match="after"):
        run_gate(request, store, archive, tmp_path / "gate")


@pytest.mark.unit
def test_a_build_time_without_a_time_zone_is_refused(tmp_path: Path) -> None:
    with pytest.raises(GateRunError, match="time zone"):
        gate_request(small_set(tmp_path, ["GOOGL"]), built_at=datetime(2026, 11, 14, 9))


@pytest.mark.unit
@pytest.mark.parametrize("commit", [None, f"{COMMIT}+dirty", "package 0.1.0, git unknown"])
def test_code_that_is_not_a_clean_commit_is_refused(
    world: World, tmp_path: Path, commit: str | None
) -> None:
    store, archive = world
    request = gate_request(small_set(tmp_path, ["GOOGL"]), code_commit=commit)
    with pytest.raises(GateRunError, match="clean commit"):
        run_gate(request, store, archive, tmp_path / "gate")
    assert not (tmp_path / "gate").exists()


@pytest.mark.unit
def test_unclean_code_can_be_allowed_and_is_recorded(world: World, tmp_path: Path) -> None:
    store, archive = world
    request = gate_request(
        small_set(tmp_path, ["GOOGL"]), code_commit=f"{COMMIT}+dirty", allow_unclean_code=True
    )
    result = run_gate(request, store, archive, tmp_path / "gate")
    record = json.loads((result.folder / RUN_RECORD).read_text(encoding="utf-8"))
    assert record["code"]["clean"] is False
    assert record["code"]["commit"] == f"{COMMIT}+dirty"


# --- checking a frozen run ---


@pytest.mark.unit
def test_a_frozen_run_verifies_until_a_dossier_is_changed(world: World, tmp_path: Path) -> None:
    store, archive = world
    result = run_gate(
        gate_request(small_set(tmp_path, ["GOOGL"])), store, archive, tmp_path / "gate"
    )
    assert verify_gate_run(result.folder) == []

    markdown = next(result.folder.glob("GOOGL-*.md"))
    markdown.write_text(markdown.read_text(encoding="utf-8") + "edited\n", encoding="utf-8")
    problems = verify_gate_run(result.folder)
    assert problems == [f"{markdown.name} does not match its recorded hash"]


@pytest.mark.unit
def test_a_missing_dossier_or_run_record_fails_verification(world: World, tmp_path: Path) -> None:
    store, archive = world
    result = run_gate(
        gate_request(small_set(tmp_path, ["GOOGL"])), store, archive, tmp_path / "gate"
    )
    data = next(result.folder.glob("GOOGL-*.json"))
    data.unlink()
    assert verify_gate_run(result.folder) == [f"{data.name} is missing"]
    assert verify_gate_run(tmp_path / "nowhere") == [f"no {RUN_RECORD} in {tmp_path / 'nowhere'}"]
