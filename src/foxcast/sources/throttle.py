"""A minimum-interval rate limiter, so every source stays within its fair-access limit."""

import time
from collections.abc import Callable


class Throttle:
    """Spaces calls at least ``1 / max_per_second`` seconds apart."""

    def __init__(
        self,
        max_per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_per_second <= 0:
            raise ValueError("max_per_second must be positive")
        self._interval = 1.0 / max_per_second
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            elapsed = now - self._last
            if elapsed < self._interval:
                self._sleep(self._interval - elapsed)
        self._last = self._clock()
