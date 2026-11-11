"""The append-only, hash-chained forecast ledger (brief F17, scorecard row 3a).

Each record is one JSON line. Its hash covers its own contents and the previous record's
hash, so any edit, deletion or reordering breaks `verify()`. Appends are repeat-safe: a
retried freeze with the same request ID returns the existing record, and a retry with
different contents is refused (brief change T).

Public digests and independent timestamps are added in Stage 3; this is the core chain.
"""

import hashlib
import json
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

GENESIS_HASH: Final = "0" * 64
HASHED_FIELDS: Final = ("sequence", "request_id", "kind", "created_at", "payload", "prev_hash")


class LedgerConflictError(ValueError):
    """A request ID was reused with different contents."""


def canonical_json(value: Any) -> bytes:
    """Deterministic JSON: sorted keys, no spaces, and no NaN or infinity."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _record_hash(fields: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json({k: fields[k] for k in HASHED_FIELDS})).hexdigest()


@dataclass(frozen=True)
class LedgerRecord:
    sequence: int
    request_id: str
    kind: str
    created_at: str
    payload: Mapping[str, Any]
    prev_hash: str
    hash: str


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    records_checked: int
    reason: str = ""


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Ledger:
    def __init__(self, path: Path, *, now: Callable[[], datetime] = _utc_now) -> None:
        self._path = path
        self._now = now

    @property
    def path(self) -> Path:
        return self._path

    def _raw_lines(self) -> list[str]:
        if not self._path.exists():
            return []
        return [line for line in self._path.read_text(encoding="utf-8").splitlines() if line]

    def records(self) -> list[LedgerRecord]:
        return [LedgerRecord(**json.loads(line)) for line in self._raw_lines()]

    def verify(self) -> VerifyResult:
        prev_hash = GENESIS_HASH
        lines = self._raw_lines()
        for index, line in enumerate(lines):
            try:
                fields = json.loads(line)
                record = LedgerRecord(**fields)
            except (json.JSONDecodeError, TypeError) as exc:
                return VerifyResult(False, index, f"line {index + 1} is not a valid record: {exc}")
            if record.sequence != index:
                return VerifyResult(
                    False, index, f"line {index + 1} has sequence {record.sequence}"
                )
            if record.prev_hash != prev_hash:
                return VerifyResult(
                    False, index, f"line {index + 1} does not chain to the previous"
                )
            if _record_hash(fields) != record.hash:
                return VerifyResult(False, index, f"line {index + 1} was changed after writing")
            prev_hash = record.hash
        return VerifyResult(True, len(lines))

    def append(self, *, kind: str, request_id: str, payload: Mapping[str, Any]) -> LedgerRecord:
        existing = self.records()
        for record in existing:
            if record.request_id == request_id:
                if record.kind != kind or canonical_json(record.payload) != canonical_json(payload):
                    raise LedgerConflictError(
                        f"request {request_id!r} already frozen with different contents"
                    )
                return record
        check = self.verify()
        if not check.ok:
            raise ValueError(f"refusing to append to a broken ledger: {check.reason}")

        fields: dict[str, Any] = {
            "sequence": len(existing),
            "request_id": request_id,
            "kind": kind,
            "created_at": self._now().astimezone(UTC).isoformat(),
            "payload": json.loads(canonical_json(payload)),
            "prev_hash": existing[-1].hash if existing else GENESIS_HASH,
        }
        record = LedgerRecord(**fields, hash=_record_hash(fields))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(asdict(record)).decode("utf-8") + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def backup(self, directory: Path) -> Path:
        """Copy the ledger into ``directory`` under a timestamped name (brief change BH)."""
        directory.mkdir(parents=True, exist_ok=True)
        stamp = self._now().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = directory / f"{self._path.stem}-{stamp}{self._path.suffix}"
        shutil.copy2(self._path, target)
        return target
