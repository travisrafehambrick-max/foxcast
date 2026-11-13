"""Content-addressed copies of every fetched source (brief F2, scorecard row 1c).

Claims are verified against the copy saved at fetch time, not a live re-fetch. Each copy
is stored under its SHA-256, so saving is idempotent and a changed file is detected.

Where it lives (decided 2026-11-12): raw payloads stay on local disk, outside any git
repository, in ``archive_dir`` (default ``~/.foxcast/archive``, beside the data repo, not
in it). Filing bodies run to several MB and include third-party text, so they never go
into git. Entry points open the archive with :meth:`SourceArchive.outside_git`, which
refuses a folder that git could commit: one inside a work tree that does not ignore it.
(A home folder that is itself a repository counts.) What may be committed, if the data repo is
published, is the hash index (:meth:`SourceArchive.write_index`) and the hashes the facts
already carry.
"""

import hashlib
import os
import subprocess
import uuid
from pathlib import Path
from typing import Final

SUFFIX: Final = ".bin"
SHA256_HEX_LENGTH: Final = 64
HEX_DIGITS: Final = frozenset("0123456789abcdef")
GIT_MARKER: Final = ".git"  # a folder in a clone, a file in a linked worktree
GIT_TIMEOUT_SECONDS: Final = 10


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_digest(text: str) -> bool:
    return len(text) == SHA256_HEX_LENGTH and set(text) <= HEX_DIGITS


def git_work_tree_of(directory: Path) -> Path | None:
    """The root of the git work tree holding ``directory``, if any (it need not exist)."""
    for folder in (directory.resolve(), *directory.resolve().parents):
        if (folder / GIT_MARKER).exists():
            return folder
    return None


def ignored_by_git(directory: Path, repository: Path) -> bool:
    """True only if git confirms it would ignore a copy saved in ``directory``.

    Anything else (git missing, an error, a timeout, "not ignored") counts as not ignored.
    The folder itself must be ignored, not just some files in it, because git never looks
    inside an ignored folder. Git may not look above ``repository``, and inherited ``GIT_*``
    settings (``GIT_DIR`` in a hook, say) are dropped, so it answers for this repository.
    """
    probe = f"{directory.resolve().as_posix()}/"  # the trailing slash: as a folder
    command = ["git", "-C", str(repository), "check-ignore", "--quiet", probe]
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    environment["GIT_CEILING_DIRECTORIES"] = str(repository.resolve().parent)
    try:
        result = subprocess.run(  # noqa: S603 - fixed arguments, no shell
            command,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


class SourceArchive:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @classmethod
    def outside_git(cls, directory: Path) -> "SourceArchive":
        """The archive in ``directory``, refused if git could commit what it saves there.

        A folder in no git work tree is accepted, and so is one its work tree ignores.
        """
        repository = git_work_tree_of(directory)
        if repository is not None and not ignored_by_git(directory, repository):
            raise ValueError(
                f"the source archive {directory} is inside the git work tree {repository} "
                "and git does not ignore it; raw filings must stay out of git (set "
                "archive_dir to a folder outside any repository, or ignore it there)"
            )
        return cls(directory)

    def _path_for(self, sha256: str) -> Path:
        if not _is_digest(sha256):
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

    def _stored(self) -> list[Path]:
        """Every complete copy, skipping partial writes and anything not named by a hash."""
        if not self._directory.is_dir():
            return []
        return [
            path
            for path in self._directory.glob(f"??/*{SUFFIX}")
            if _is_digest(path.name.removesuffix(SUFFIX)) and path.parent.name == path.name[:2]
        ]

    def write_index(self, target: Path) -> int:
        """Write one ``<sha256> <bytes>`` line per stored copy, sorted; return the count.

        The index names what the archive holds without any of its content, so it is the
        part that may be committed to a data repo.
        """
        lines = sorted(
            f"{path.name.removesuffix(SUFFIX)} {path.stat().st_size}\n" for path in self._stored()
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="ascii", newline="\n") as handle:
            handle.writelines(lines)
        return len(lines)
