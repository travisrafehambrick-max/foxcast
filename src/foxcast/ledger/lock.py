"""An exclusive, cross-process lock on a sidecar file (no extra dependency).

Held for the whole read-verify-append of a ledger write, so two processes (say, a manual
`foxcast forecast` and a `foxcast run` started at the same time) cannot both append sequence N.
"""

import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

POLL_SECONDS: Final = 0.05
DEFAULT_TIMEOUT_SECONDS: Final = 60.0


class LockTimeoutError(TimeoutError):
    """Another process held the lock for too long."""


def _try_lock(fd: int) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def exclusive_lock(path: Path, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(fd):
            if time.monotonic() > deadline:
                raise LockTimeoutError(f"could not lock {path} within {timeout:.0f}s")
            time.sleep(POLL_SECONDS)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)
