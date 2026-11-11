"""The append-only, hash-chained forecast ledger (brief F17, scorecard row 3a).

Each record is one JSON line. Its hash covers its own contents and the previous record's
hash, so any edit, deletion or reordering breaks `verify()`. Appends are repeat-safe: a
retried freeze with the same request ID returns the existing record, and a retry with
different contents is refused (brief change T). Every append holds an exclusive lock for
its whole read-verify-write, so two processes cannot fork the chain.

Public digests and independent timestamps are added in Stage 3; until then, cutting
records off the end of the file is not detectable (rows 3a and change E close that gap).
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

from foxcast.ledger.lock import exclusive_lock

GENESIS_HASH: Final = "0" * 64
HASHED_FIELDS: Final = ("sequence", "request_id", "kind", "created_at", "payload", "prev_hash")
NEWLINE: Final = "\n"


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

    @property
    def lock_path(self) -> Path:
        return self._path.with_name(self._path.name + ".lock")

    def _text(self) -> str:
        return self._path.read_text(encoding="utf-8") if self._path.exists() else ""

    def _raw_lines(self) -> list[str]:
        # Split on the newline character only. str.splitlines() also splits on U+2028,
        # U+2029 and U+0085, which canonical JSON writes unescaped inside strings.
        return [line for line in self._text().split(NEWLINE) if line]

    def records(self) -> list[LedgerRecord]:
        return [LedgerRecord(**json.loads(line)) for line in self._raw_lines()]

    def verify(self) -> VerifyResult:
        text = self._text()
        lines = self._raw_lines()
        if text and not text.endswith(NEWLINE):
            return VerifyResult(
                False,
                max(len(lines) - 1, 0),
                f"line {len(lines)} is torn (no trailing newline; a write was interrupted). "
                "Run `foxcast repair-ledger` to cut it off after a backup.",
            )
        prev_hash = GENESIS_HASH
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

    def _existing(
        self, request_id: str, kind: str, payload: Mapping[str, Any]
    ) -> LedgerRecord | None:
        for record in self.records():
            if record.request_id != request_id:
                continue
            same = record.kind == kind and canonical_json(record.payload) == canonical_json(payload)
            if not same:
                raise LedgerConflictError(
                    f"request {request_id!r} already frozen with different contents"
                )
            return record
        return None

    def append(self, *, kind: str, request_id: str, payload: Mapping[str, Any]) -> LedgerRecord:
        with exclusive_lock(self.lock_path):
            check = self.verify()
            if not check.ok:
                raise ValueError(f"refusing to append to a broken ledger: {check.reason}")
            found = self._existing(request_id, kind, payload)
            if found is not None:
                return found
            existing = self.records()
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
            with self._path.open("a", encoding="utf-8", newline=NEWLINE) as handle:
                handle.write(canonical_json(asdict(record)).decode("utf-8") + NEWLINE)
                handle.flush()
                os.fsync(handle.fileno())
            return record

    def repair_torn_tail(self, backup_directory: Path) -> Path:
        """Cut off an interrupted last line, after backing up the whole file.

        Only a torn tail (no trailing newline) is repaired. Any other break is evidence of
        tampering or corruption and is left for a person to investigate.
        """
        with exclusive_lock(self.lock_path):
            text = self._text()
            if not text or text.endswith(NEWLINE):
                raise ValueError("the ledger has no torn last line to repair")
            backup = self.backup(backup_directory)
            kept = text[: text.rfind(NEWLINE) + 1]
            temporary = self._path.with_name(self._path.name + ".tmp")
            temporary.write_text(kept, encoding="utf-8", newline=NEWLINE)
            os.replace(temporary, self._path)
            check = self.verify()
            if not check.ok:
                raise ValueError(f"ledger still broken after cutting the tail: {check.reason}")
            return backup

    def backup(self, directory: Path) -> Path:
        """Copy the ledger into ``directory`` under a timestamped name (brief change BH)."""
        directory.mkdir(parents=True, exist_ok=True)
        stamp = self._now().astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = directory / f"{self._path.stem}-{stamp}{self._path.suffix}"
        shutil.copy2(self._path, target)
        return target
