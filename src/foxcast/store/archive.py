"""Content-addressed copies of every fetched source (brief F2, scorecard row 1c).

Claims are verified against the copy saved at fetch time, not a live re-fetch. Each copy
is stored under its SHA-256, so saving is idempotent and a changed file is detected.
"""

import hashlib
import os
import uuid
from pathlib import Path
from typing import Final

SUFFIX: Final = ".bin"
SHA256_HEX_LENGTH: Final = 64
HEX_DIGITS: Final = frozenset("0123456789abcdef")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class SourceArchive:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def _path_for(self, sha256: str) -> Path:
        if len(sha256) != SHA256_HEX_LENGTH or not set(sha256) <= HEX_DIGITS:
            raise ValueError(f"not a SHA-256 hex digest: {sha256!r}")
        return self._directory / sha256[:2] / f"{sha256}{SUFFIX}"

    def save(self, raw: bytes) -> Path:
        """Store ``raw`` under its hash. An existing copy is kept only if it still matches."""
        sha256 = _sha256(raw)
        path = self._path_for(sha256)
        if path.exists() and _sha256(path.read_bytes()) == sha256:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        with temporary.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return path

    def load(self, sha256: str) -> bytes:
        raw = self._path_for(sha256).read_bytes()
        if _sha256(raw) != sha256:
            raise ValueError(f"stored copy {sha256} no longer matches its hash")
        return raw
