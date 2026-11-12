"""Exact binomial bounds for error rates (row 1e, and the planted-claim rate beside row 1c).

The one-sided Clopper-Pearson upper bound is the largest error rate that is still
consistent, at the given confidence, with seeing ``errors`` wrong out of ``n`` checked.
It is exact, so it stays honest at small counts, where a normal approximation would not.
"""

from numbers import Integral
from typing import Final

from scipy.stats import beta

DEFAULT_CONFIDENCE: Final = 0.95


def _is_count(value: object) -> bool:
    return isinstance(value, Integral) and not isinstance(value, bool)


def _check(n: int, confidence: float) -> None:
    if not _is_count(n):
        raise TypeError(f"n must be a whole number, got {n!r}")
    if n < 1:
        raise ValueError(f"need at least one checked item, got n={n}")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be strictly between 0 and 1, got {confidence}")


def clopper_pearson_upper(errors: int, n: int, *, confidence: float = DEFAULT_CONFIDENCE) -> float:
    """One-sided upper bound on the error rate after ``errors`` wrong out of ``n``."""
    _check(n, confidence)
    if not _is_count(errors):
        raise TypeError(f"errors must be a whole number, got {errors!r}")
    if not 0 <= errors <= n:
        raise ValueError(f"errors must be between 0 and n={n}, got {errors}")
    if errors == n:
        return 1.0
    return float(beta.ppf(confidence, errors + 1, n - errors))


def max_errors_below(n: int, bound: float, confidence: float = DEFAULT_CONFIDENCE) -> int:
    """The most errors out of ``n`` whose upper bound stays below ``bound``.

    Returns -1 when even zero errors cannot get the bound below ``bound``: the sample is
    too small for the pass line.
    """
    _check(n, confidence)
    if not 0.0 < bound <= 1.0:
        raise ValueError(f"bound must be in (0, 1], got {bound}")
    allowed = -1
    for errors in range(n + 1):
        if clopper_pearson_upper(errors, n, confidence=confidence) >= bound:
            break
        allowed = errors
    return allowed
