"""Content-addressed copies of every fetched source (brief F2, scorecard row 1c).

Claims are verified against the copy saved at fetch time, not a live re-fetch. Each copy
is stored under its SHA-256, so saving is idempotent and a changed file is detected.
"""

import hashlib
import os
from pathlib import Path
from typing import Final

SUFFIX: Final = ".bin"


class SourceArchive:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def _path_for(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValueError(f"not a SHA-256 hex digest: {sha256!r}")
        return self._directory / sha256[:2] / f"{sha256}{SUFFIX}"

    def save(self, raw: bytes) -> Path:
        sha256 = hashlib.sha256(raw).hexdigest()
        path = self._path_for(sha256)
        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(raw)
        os.replace(temporary, path)
        return path

    def load(self, sha256: str) -> bytes:
        raw = self._path_for(sha256).read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha256:
            raise ValueError(f"stored copy {sha256} no longer matches its hash")
        return raw
