"""Derived facts: values computed by code from other facts (brief change BC).

A claim that says "doubled", "a third" or "margin rose" needs a derived fact behind it, so
the model never does arithmetic. A derived fact records the IDs of its inputs, the method
and the basis of the comparison, and :func:`trace` can always walk it back to the fetched
facts it came from. :func:`recompute` rebuilds it from its inputs and refuses it unless
every field, not just the value, comes out the same.

Only three methods exist, and each refuses comparisons that would mislead: across
companies or units, between non-adjacent or overlapping periods, a quarter against a
year, or growth from a zero or negative base.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final, Protocol

from foxcast.facts.ids import canonical_id
from foxcast.facts.record import Fact, TextFact

PURE_UNIT: Final = "pure"
# Fiscal quarters and years differ by up to a week (52/53-week years); more is a mismatch.
PERIOD_LENGTH_TOLERANCE_DAYS: Final = 8
DAYS_IN_YEAR: Final = 365
DAYS_IN_QUARTER: Final = 91
MAX_TRACE_DEPTH: Final = 8


class Method(StrEnum):
    RATIO = "ratio"  # a / b at the same date (a margin, a share, a return on assets)
    DIFFERENCE = "difference"  # later - earlier, same metric
    GROWTH = "growth"  # later / earlier - 1, same metric


class Basis(StrEnum):
    SAME_PERIOD = "same period"
    YEAR_ON_YEAR = "year-on-year"
    SEQUENTIAL = "sequential"


@dataclass(frozen=True)
class DerivedFact:
    fact_id: str
    cik: int
    field: str
    method: Method
    basis: Basis
    inputs: tuple[str, ...]
    unit: str
    period_start: date | None
    period_end: date
    value: float
    public_on: date


NumericFact = Fact | DerivedFact


class UntraceableError(Exception):
    """A derived fact whose inputs are missing, not numeric, or no longer rebuild it."""


class FactLookup(Protocol):
    def get(self, fact_id: str, /) -> Fact | DerivedFact | TextFact | None: ...


def _length_days(fact: NumericFact) -> int | None:
    return None if fact.period_start is None else (fact.period_end - fact.period_start).days


def _near(days: int, target: int) -> bool:
    return abs(days - target) <= PERIOD_LENGTH_TOLERANCE_DAYS


def _ratio_period(a: NumericFact, b: NumericFact) -> date | None:
    """The period start of a ratio, refusing inputs that are not measured at the same date.

    Two flows must cover the same period; a flow over a stock (net income over assets) is
    allowed when the stock is measured at the flow's period end.
    """
    if a.period_end != b.period_end:
        raise ValueError("ratio inputs cover a different period")
    starts = {a.period_start, b.period_start} - {None}
    if len(starts) > 1:
        raise ValueError("ratio inputs cover a different period")
    return starts.pop() if starts else None


def _check_ratio(a: NumericFact, b: NumericFact) -> None:
    if a.unit != b.unit:
        raise ValueError(f"ratio inputs have a different unit: {a.unit} and {b.unit}")
    if b.value == 0:
        raise ValueError("ratio with a zero denominator")


def _change_basis(method: Method, earlier: NumericFact, later: NumericFact) -> Basis:
    if earlier.field != later.field:
        raise ValueError(f"{method} needs the same field: {earlier.field} and {later.field}")
    if earlier.unit != later.unit:
        raise ValueError(f"{method} inputs have a different unit")
    first, second = _length_days(earlier), _length_days(later)
    if (first is None) != (second is None):
        raise ValueError(f"{method} compares an instant with a period")
    if first is not None and second is not None and not _near(first, second):
        raise ValueError(f"{method} inputs have period lengths {first} and {second}")
    if not earlier.period_end < later.period_end:
        raise ValueError(f"{method} needs the earlier input first")
    if later.period_start is not None and later.period_start <= earlier.period_end:
        raise ValueError(f"{method} inputs have overlapping periods")
    if method is Method.GROWTH and earlier.value <= 0:
        raise ValueError("growth from a non-positive base is not meaningful")
    gap = (later.period_end - earlier.period_end).days
    if _near(gap, DAYS_IN_YEAR):
        return Basis.YEAR_ON_YEAR
    step = DAYS_IN_QUARTER if second is None else second
    if _near(gap, step):
        return Basis.SEQUENTIAL
    raise ValueError(f"{method} inputs are not adjacent periods ({gap} days apart)")


_COMPUTE: Final[dict[Method, Callable[[float, float], float]]] = {
    Method.RATIO: lambda a, b: a / b,
    Method.DIFFERENCE: lambda a, b: b - a,
    Method.GROWTH: lambda a, b: b / a - 1.0,
}


def derive(method: Method, a: NumericFact, b: NumericFact) -> DerivedFact:
    """Compute a derived fact. For a ratio, ``a / b``; otherwise ``a`` is the earlier fact."""
    if a.cik != b.cik:
        raise ValueError(f"inputs are from a different company: {a.cik} and {b.cik}")
    if not (math.isfinite(a.value) and math.isfinite(b.value)):
        raise ValueError("inputs must be finite numbers")
    if method is Method.RATIO:
        period_start = _ratio_period(a, b)
        _check_ratio(a, b)
        basis = Basis.SAME_PERIOD
        field, unit = f"derived:ratio({a.field}/{b.field})", PURE_UNIT
    else:
        basis = _change_basis(method, a, b)
        period_start = a.period_start
        field = f"derived:{method}({a.field})"
        unit = PURE_UNIT if method is Method.GROWTH else a.unit
    value = _COMPUTE[method](a.value, b.value)
    inputs = (a.fact_id, b.fact_id)
    return DerivedFact(
        fact_id=canonical_id("derived", str(method), str(basis), *inputs, repr(value)),
        cik=a.cik,
        field=field,
        method=method,
        basis=basis,
        inputs=inputs,
        unit=unit,
        period_start=period_start,
        period_end=b.period_end,
        value=value,
        public_on=max(a.public_on, b.public_on),
    )


def _inputs(fact: DerivedFact, lookup: FactLookup) -> list[NumericFact]:
    found: list[NumericFact] = []
    for fact_id in fact.inputs:
        item = lookup.get(fact_id)
        if item is None:
            raise UntraceableError(f"{fact.fact_id}: input {fact_id} is not in the record")
        if isinstance(item, TextFact):
            raise UntraceableError(f"{fact.fact_id}: input {fact_id} is not a number")
        found.append(item)
    return found


def trace(fact: NumericFact, lookup: FactLookup, *, _depth: int = 0) -> tuple[Fact, ...]:
    """The fetched facts a value rests on, in first-seen order, each listed once."""
    if isinstance(fact, Fact):
        return (fact,)
    if _depth >= MAX_TRACE_DEPTH:
        raise UntraceableError(f"{fact.fact_id}: derivations nest deeper than {MAX_TRACE_DEPTH}")
    sources: dict[str, Fact] = {}
    for item in _inputs(fact, lookup):
        for source in trace(item, lookup, _depth=_depth + 1):
            sources.setdefault(source.fact_id, source)
    return tuple(sources.values())


def recompute(fact: DerivedFact, lookup: FactLookup, *, _depth: int = 0) -> DerivedFact:
    """Rebuild a derived fact from its stored inputs; raise unless every field matches."""
    if _depth >= MAX_TRACE_DEPTH:
        raise UntraceableError(f"{fact.fact_id}: derivations nest deeper than {MAX_TRACE_DEPTH}")
    if len(fact.inputs) != 2:
        raise UntraceableError(f"{fact.fact_id}: a derived fact has exactly two inputs")
    a, b = _inputs(fact, lookup)
    for item in (a, b):
        if isinstance(item, DerivedFact):
            recompute(item, lookup, _depth=_depth + 1)
    try:
        expected = derive(fact.method, a, b)
    except ValueError as error:
        raise UntraceableError(f"{fact.fact_id}: inputs no longer allow it: {error}") from error
    if expected != fact:
        raise UntraceableError(f"{fact.fact_id}: the stored record does not match its inputs")
    return expected


__all__ = [
    "Basis",
    "DerivedFact",
    "FactLookup",
    "Method",
    "NumericFact",
    "UntraceableError",
    "derive",
    "recompute",
    "trace",
]
