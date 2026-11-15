"""Quantile-form CRPS scoring (scorecard rows 2b-2f, change BA).

Every compared forecast is frozen on the shared quantile grid, so scores are
computed from quantiles: CRPS = 2 * integral over the level a of the pinball
loss rho_a(Q(a) - y). Between the given levels the integrand is trapezoid-
integrated; outside the grid the quantile function is treated as flat (the
grid's own convention — the 0.5%/99.5% tails are the resolution limit of a
frozen forecast).

The volatility-scaled skill is a per-observation ratio (1 - model/baseline),
which is what stops volatile stocks dominating the mean (change BA). The
bootstrap resamples whole dates, not forecasts: stocks move together, so the
date is the effective sample unit (scorecard 2b, honesty rule 8).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from math import isnan, nan
from typing import Final

import numpy as np

DEFAULT_REPLICATES: Final = 10_000
DEFAULT_CONFIDENCE: Final = 0.95
DEFAULT_TAIL: Final = 0.1


def _pinball(q: np.ndarray, y: float, a: np.ndarray) -> np.ndarray:
    """Pinball loss rho_a(y - q) = (y - q) * (a - 1{y - q < 0})."""
    u = y - q
    return np.where(u < 0.0, u * (a - 1.0), u * a)


def _flat_edge(u: float, lo: float, hi: float) -> float:
    """Integral of the pinball loss over ``[lo, hi]`` for a constant quantile ``u = y - q``."""
    if u >= 0.0:
        return u * (hi**2 - lo**2) / 2.0
    return -u * ((hi - lo) - (hi**2 - lo**2) / 2.0)


def _check_inputs(
    values: Sequence[float], levels: Sequence[float]
) -> tuple[np.ndarray, np.ndarray]:
    a = np.asarray(levels, dtype=float)
    q = np.asarray(values, dtype=float)
    if a.shape != q.shape or a.ndim != 1:
        raise ValueError("levels and values must be one-dimensional and the same length")
    if a.size < 2:
        raise ValueError("need at least two quantile points")
    if np.any((a <= 0.0) | (a >= 1.0)):
        raise ValueError("levels must lie inside (0, 1)")
    if np.any(np.diff(a) <= 0.0):
        raise ValueError("levels must be sorted ascending")
    if np.any(np.diff(q) < 0.0):
        raise ValueError("values must be nondecreasing (a quantile function)")
    return a, q


def _band_integral(a: np.ndarray, q: np.ndarray, y: float, lo: float, hi: float) -> float:
    """Integral of the pinball loss over ``[lo, hi]`` of the level axis.

    Flat tails: below the first grid level the quantile function is constant at
    ``q[0]`` and above the last at ``q[-1]``; those stretches integrate
    analytically. Inside the grid the integrand is trapezoid-integrated with
    ``q`` interpolated linearly at the band edges.
    """
    total = 0.0
    head_end = min(hi, a[0])
    if lo < head_end:
        total += _flat_edge(float(y - q[0]), lo, head_end)
    tail_start = max(lo, a[-1])
    if tail_start < hi:
        total += _flat_edge(float(y - q[-1]), tail_start, hi)
    ilo, ihi = max(lo, a[0]), min(hi, a[-1])
    if ilo < ihi:
        inside = (a > ilo) & (a < ihi)
        xs = np.concatenate([[ilo], a[inside], [ihi]])
        qs = np.interp(xs, a, q)
        total += float(np.trapezoid(_pinball(qs, y, xs), xs))
    return total


def crps_from_quantiles(values: Sequence[float], levels: Sequence[float], outcome: float) -> float:
    """CRPS of one quantile forecast against one outcome.

    ``values`` are the forecast quantiles at ``levels`` (sorted, inside (0, 1)).
    """
    a, q = _check_inputs(values, levels)
    return 2.0 * _band_integral(a, q, outcome, 0.0, 1.0)


def tail_crps_from_quantiles(
    values: Sequence[float],
    levels: Sequence[float],
    outcome: float,
    *,
    tail: float = DEFAULT_TAIL,
) -> float:
    """CRPS restricted to the outer ``tail`` bands of the level axis.

    The pinball integral runs only over ``levels <= tail`` and
    ``levels >= 1 - tail``, then is rescaled by the covered range ``2 * tail``
    so the figure shares the full-CRPS scale convention (a degenerate forecast
    scores ``|forecast - outcome|`` under either function). Reported beside the
    raw score per change BA.
    """
    if not 0.0 < tail < 0.5:
        raise ValueError("tail must lie inside (0, 0.5)")
    a, q = _check_inputs(values, levels)
    bands = _band_integral(a, q, outcome, 0.0, tail) + _band_integral(
        a, q, outcome, 1.0 - tail, 1.0
    )
    return 2.0 * bands / (2.0 * tail)


def scaled_crps_skill(model: float, baseline: float) -> float:
    """Per-observation volatility-scaled CRPS skill: ``1 - model / baseline``.

    Positive means better than the baseline, negative worse, zero equal. The
    ratio form is what stops volatile stocks dominating (change BA). Returns
    ``nan`` when the baseline score is zero — skill is undefined there and
    callers drop it from the mean.
    """
    if baseline == 0.0:
        return nan
    return 1.0 - model / baseline


def mean_skill(skills: Iterable[float]) -> float:
    """Mean of per-observation skills, ignoring ``nan`` (undefined baselines)."""
    clean = [s for s in skills if not isnan(s)]
    if not clean:
        return nan
    return float(np.mean(clean))


@dataclass(frozen=True)
class ScoredForecast:
    """One settled forecast scored by CRPS against one baseline."""

    day: str  # forecast day, ISO "YYYY-MM-DD"
    ticker: str
    model_crps: float
    baseline_crps: float


@dataclass(frozen=True)
class SkillSummary:
    """Aggregate scaled skill with its bootstrap confidence interval."""

    skill: float
    ci_low: float
    ci_high: float
    n_obs: int
    n_dates: int
    mean_model_crps: float
    mean_baseline_crps: float
    tail_skill: float | None = None  # filled by a later variant; None here


def _pooled_skill(scores: Sequence[ScoredForecast]) -> float:
    return mean_skill(scaled_crps_skill(s.model_crps, s.baseline_crps) for s in scores)


def skill_summary(
    scores: Sequence[ScoredForecast],
    *,
    seed: int,
    replicates: int = DEFAULT_REPLICATES,
    confidence: float = DEFAULT_CONFIDENCE,
) -> SkillSummary:
    """Mean scaled skill with a block bootstrap CI over whole dates.

    Each replicate resamples the distinct days with replacement and pools every
    scored forecast on the sampled days — stocks move together, so the date is
    the effective sample unit (scorecard 2b; confidence adjusted per rule 8).
    ``n_dates`` is reported so the harness can apply the "low power" label.
    """
    if not scores:
        raise ValueError("no scored forecasts")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must lie inside (0, 1)")
    days = sorted({s.day for s in scores})
    by_day = {d: [s for s in scores if s.day == d] for d in days}
    rng = np.random.default_rng(seed)
    replicate_skills = np.empty(replicates)
    for i in range(replicates):
        sampled = rng.choice(len(days), size=len(days), replace=True)
        replicate_skills[i] = _pooled_skill(
            [s for d in (days[j] for j in sampled) for s in by_day[d]]
        )
    alpha = 1.0 - confidence
    ci_low, ci_high = np.quantile(replicate_skills, [alpha / 2.0, 1.0 - alpha / 2.0])
    return SkillSummary(
        skill=_pooled_skill(scores),
        ci_low=float(ci_low),
        ci_high=float(ci_high),
        n_obs=len(scores),
        n_dates=len(days),
        mean_model_crps=float(np.mean([s.model_crps for s in scores])),
        mean_baseline_crps=float(np.mean([s.baseline_crps for s in scores])),
    )
