"""The core ledger: append-only, hash-chained, and repeat-safe (brief F17, changes T, AU, BH)."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from foxcast.ledger.chain import (
    GENESIS_HASH,
    Ledger,
    LedgerConflictError,
    _record_hash,
    canonical_json,
)
from foxcast.ledger.lock import LockTimeoutError, exclusive_lock
from foxcast.ledger.resolution import RESOLUTION_RULE

START = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)


def clock() -> Callable[[], datetime]:
    times = (START + timedelta(seconds=i) for i in range(10_000))
    return lambda: next(times)


def filled(path: Path, count: int) -> Ledger:
    ledger = Ledger(path, now=clock())
    for i in range(count):
        ledger.append(kind="forecast", request_id=f"req-{i}", payload={"i": i, "q": [0.1, 0.5]})
    return ledger


@pytest.mark.unit
def test_canonical_json_is_stable_and_rejects_nan() -> None:
    assert canonical_json({"b": 1, "a": [1, 2]}) == b'{"a":[1,2],"b":1}'
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


@pytest.mark.unit
def test_records_chain_from_genesis(tmp_path: Path) -> None:
    ledger = filled(tmp_path / "ledger.jsonl", 3)
    records = ledger.records()
    assert [r.sequence for r in records] == [0, 1, 2]
    assert records[0].prev_hash == GENESIS_HASH
    assert records[1].prev_hash == records[0].hash
    assert ledger.verify().ok


@pytest.mark.unit
def test_retried_freeze_returns_the_existing_record(tmp_path: Path) -> None:
    ledger = filled(tmp_path / "ledger.jsonl", 2)
    again = ledger.append(kind="forecast", request_id="req-1", payload={"i": 1, "q": [0.1, 0.5]})
    assert again.sequence == 1
    assert len(ledger.records()) == 2


@pytest.mark.unit
def test_retry_with_different_contents_is_refused(tmp_path: Path) -> None:
    ledger = filled(tmp_path / "ledger.jsonl", 2)
    assert ledger.path == tmp_path / "ledger.jsonl"
    with pytest.raises(LedgerConflictError):
        ledger.append(kind="forecast", request_id="req-1", payload={"i": 99})
    with pytest.raises(LedgerConflictError):
        ledger.append(kind="other_kind", request_id="req-1", payload={"i": 1, "q": [0.1, 0.5]})
    assert len(ledger.records()) == 2


@pytest.mark.unit
def test_records_survive_reopening(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    filled(path, 3)
    reopened = Ledger(path, now=clock())
    record = reopened.append(kind="forecast", request_id="req-new", payload={})
    assert record.sequence == 3
    assert reopened.verify().ok


@pytest.mark.unit
def test_backup_copies_the_ledger(tmp_path: Path) -> None:
    ledger = filled(tmp_path / "ledger.jsonl", 2)
    copy = ledger.backup(tmp_path / "backups")
    assert copy.read_bytes() == (tmp_path / "ledger.jsonl").read_bytes()


@pytest.mark.unit
def test_resolution_rule_never_voids_a_priced_outcome() -> None:
    assert "last traded price" in RESOLUTION_RULE["delisting"]
    assert "consideration" in RESOLUTION_RULE["takeover"]
    assert "only when no price exists" in RESOLUTION_RULE["void"]


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


PROPERTY = settings(
    max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None
)


@PROPERTY
@given(index=st.integers(min_value=0, max_value=4), new_value=st.integers(min_value=100))
def test_any_edit_breaks_verification(tmp_path: Path, index: int, new_value: int) -> None:
    path = tmp_path / f"edit-{index}-{new_value}.jsonl"
    filled(path, 5)
    lines = _lines(path)
    record = json.loads(lines[index])
    record["payload"]["i"] = new_value
    lines[index] = json.dumps(record)
    _write(path, lines)
    assert not Ledger(path, now=clock()).verify().ok


@PROPERTY
@given(index=st.integers(min_value=0, max_value=3))
def test_any_deletion_breaks_verification(tmp_path: Path, index: int) -> None:
    path = tmp_path / f"delete-{index}.jsonl"
    filled(path, 5)
    lines = _lines(path)
    del lines[index]
    _write(path, lines)
    assert not Ledger(path, now=clock()).verify().ok


@PROPERTY
@given(perm=st.permutations(list(range(5))))
def test_any_reordering_breaks_verification(tmp_path: Path, perm: list[int]) -> None:
    if perm == sorted(perm):
        return
    path = tmp_path / f"order-{'-'.join(map(str, perm))}.jsonl"
    filled(path, 5)
    lines = _lines(path)
    _write(path, [lines[i] for i in perm])
    assert not Ledger(path, now=clock()).verify().ok


@pytest.mark.unit
def test_truncated_last_line_is_reported_not_ignored(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    filled(path, 3)
    text = path.read_text(encoding="utf-8")
    path.write_text(text[: len(text) - 10], encoding="utf-8")
    result = Ledger(path, now=clock()).verify()
    assert not result.ok
    assert result.records_checked == 2
    assert "line 3 is torn" in result.reason
    assert "foxcast repair-ledger" in result.reason


@pytest.mark.unit
def test_verify_detects_malformed_records_and_broken_prev_hash(tmp_path: Path) -> None:
    bad_json = tmp_path / "bad_json.jsonl"
    filled(bad_json, 1)
    bad_json.write_text(bad_json.read_text(encoding="utf-8") + "{not-json}\n", encoding="utf-8")
    res_json = Ledger(bad_json, now=clock()).verify()
    assert not res_json.ok
    assert res_json.records_checked == 1
    assert "line 2 is not a valid record" in res_json.reason

    missing_fields = tmp_path / "missing_fields.jsonl"
    missing_fields.write_text('{"sequence": 0}\n', encoding="utf-8")
    res_fields = Ledger(missing_fields, now=clock()).verify()
    assert not res_fields.ok
    assert res_fields.records_checked == 0
    assert "line 1 is not a valid record" in res_fields.reason

    broken_link = tmp_path / "broken_link.jsonl"
    filled(broken_link, 2)
    lines = _lines(broken_link)
    forged = json.loads(lines[1])
    forged["prev_hash"] = "f" * 64
    forged["hash"] = _record_hash(forged)
    lines[1] = json.dumps(forged)
    _write(broken_link, lines)
    res_link = Ledger(broken_link, now=clock()).verify()
    assert not res_link.ok
    assert res_link.records_checked == 1
    assert "line 2 does not chain to the previous" in res_link.reason


@pytest.mark.unit
def test_repair_torn_tail_only_repairs_interrupted_last_line(tmp_path: Path) -> None:
    backups = tmp_path / "backups"

    # Empty or missing ledger has no torn tail.
    empty = Ledger(tmp_path / "empty.jsonl", now=clock())
    with pytest.raises(ValueError, match="no torn last line"):
        empty.repair_torn_tail(backups)

    # A tampered record with a trailing newline is NOT a torn tail and must not be repaired.
    tampered_path = tmp_path / "tampered.jsonl"
    filled(tampered_path, 2)
    lines = _lines(tampered_path)
    first = json.loads(lines[0])
    first["payload"]["i"] = 999
    lines[0] = json.dumps(first)
    _write(tampered_path, lines)
    with pytest.raises(ValueError, match="no torn last line"):
        Ledger(tampered_path, now=clock()).repair_torn_tail(backups)

    # A torn first line (no prior newline) is backed up and trimmed to an empty valid ledger.
    first_torn = tmp_path / "first_torn.jsonl"
    first_torn.write_text('{"sequence":0,"request_id":"r0"', encoding="utf-8")
    first_ledger = Ledger(first_torn, now=clock())
    backup = first_ledger.repair_torn_tail(backups)
    assert backup.read_text(encoding="utf-8") == '{"sequence":0,"request_id":"r0"'
    verified = first_ledger.verify()
    assert verified.ok
    assert verified.records_checked == 0

    # If cutting the torn tail still leaves an earlier tampered line, repair raises ValueError.
    both_broken = tmp_path / "both_broken.jsonl"
    both_broken.write_text(lines[0] + "\n" + '{"sequence":1,"torn', encoding="utf-8")
    with pytest.raises(ValueError, match="ledger still broken after cutting the tail"):
        Ledger(both_broken, now=clock()).repair_torn_tail(backups)


@pytest.mark.unit
def test_exclusive_lock_times_out_when_already_held(tmp_path: Path) -> None:
    lock_file = tmp_path / "ledger.jsonl.lock"
    with (
        exclusive_lock(lock_file, timeout=1.0),
        pytest.raises(LockTimeoutError, match="could not lock"),
        exclusive_lock(lock_file, timeout=0.01),
    ):
        pass
