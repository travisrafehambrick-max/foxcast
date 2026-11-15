"""Code provenance: which checkout produced a record (git commit + dirty flag).

Frozen forecasts, gate entries and dossier metadata all carry this, so a rerun whose
code changed is visible instead of silently different.
"""

import subprocess
from pathlib import Path
from typing import Final

from foxcast import __version__

GIT_TIMEOUT_SECONDS: Final = 10


def _git(project: Path, *args: str) -> str:
    out = subprocess.run(  # noqa: S603
        ["git", "-C", str(project), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    return out.stdout.strip()


def code_version(project: Path) -> str:
    """The git commit, marked "+dirty" when the working tree has uncommitted changes."""
    try:
        commit = _git(project, "rev-parse", "HEAD")
        dirty = bool(_git(project, "status", "--porcelain", "--untracked-files=no"))
    except (OSError, subprocess.SubprocessError):
        return f"package {__version__}, git unknown"
    return f"{commit}+dirty" if dirty else commit
