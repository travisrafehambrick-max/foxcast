"""Quantile-form CRPS scoring (scorecard rows 2b-2f, change BA)."""

import math

import pytest
from scipy.stats import norm

from foxcast.scoring.crps import (
    ScoredForecast,
    crps_from_quantiles,
    mean_skill,
    scaled_crps_skill,
    skill_summary,
    tail_crps_from_quantiles,
)
from foxcast.sim.grid import QUANTILE_LEVELS


@pytest.mark.unit
def test_crps_of_a_degenerate_forecast_is_the_absolute_error() -> None:
    for c, y in ((0.05, 0.0), (-0.03, 0.0), (0.0, 0.04)):
        values = [c] * len(QUANTILE_LEVELS)
        assert crps_from_quantiles(values, QUANTILE_LEVELS, y) == pytest.approx(abs(c - y))


@pytest.mark.unit
def test_crps_matches_the_normal_closed_form() -> None:
    sigma = 0.02
    values = [float(norm.ppf(a)) * sigma for a in QUANTILE_LEVELS]
    # CRPS(N(0, sigma^2), 0) = sigma * (2 * phi(0) - 1/sqrt(pi)).
    expected = sigma * (2.0 / math.sqrt(2.0 * math.pi) - 1.0 / math.sqrt(math.pi))
    # Tolerance is loose: the grid's flat tails ignore the outer half-percent each side.
    assert crps_from_quantiles(values, QUANTILE_LEVELS, 0.0) == pytest.approx(expected, rel=0.02)


@pytest.mark.unit
def test_crps_is_finite_for_an_outcome_beyond_the_grid() -> None:
    sigma = 0.02
    values = [float(norm.ppf(a)) * sigma for a in QUANTILE_LEVELS]
    central = crps_from_quantiles(values, QUANTILE_LEVELS, 0.0)
    extreme = crps_from_quantiles(values, QUANTILE_LEVELS, 0.5)  # 25 sigma out
    assert math.isfinite(extreme)
    assert extreme > central


@pytest.mark.unit
def test_crps_rejects_bad_inputs() -> None:
    good = [0.0, 1.0]
    with pytest.raises(ValueError):
        crps_from_quantiles(good, [0.25, 0.5, 0.75], 0.0)  # length mismatch
    with pytest.raises(ValueError):
        crps_from_quantiles([0.0], [0.5], 0.0)  # fewer than two points
    with pytest.raises(ValueError):
        crps_from_quantiles(good, [0.75, 0.25], 0.0)  # unsorted levels
    with pytest.raises(ValueError):
        crps_from_quantiles([0.0, 1.0], [0.0, 0.5], 0.0)  # level outside (0, 1)
    with pytest.raises(ValueError):
        crps_from_quantiles([1.0, 0.0], [0.25, 0.75], 0.0)  # unsorted values


@pytest.mark.unit
def test_tail_crps_of_a_degenerate_forecast_is_the_absolute_error() -> None:
    # The tail score shares the full CRPS scale convention: 2 * band integral
    # over the covered range. A constant forecast therefore scores |c - y|.
    for c, y in ((0.05, 0.0), (-0.03, 0.0)):
        values = [c] * len(QUANTILE_LEVELS)
        assert tail_crps_from_quantiles(values, QUANTILE_LEVELS, y) == pytest.approx(abs(c - y))


@pytest.mark.unit
def test_tail_crps_only_counts_the_outer_bands() -> None:
    sigma = 0.02
    base = [float(norm.ppf(a)) * sigma for a in QUANTILE_LEVELS]
    # Stretch only the outer 10% quantiles; the centre is untouched.
    wide = [
        q * 1.5 if a <= 0.1 or a >= 0.9 else q for q, a in zip(base, QUANTILE_LEVELS, strict=True)
    ]
    wide = sorted(wide)  # keep the monotone grid
    assert tail_crps_from_quantiles(wide, QUANTILE_LEVELS, 0.0) > tail_crps_from_quantiles(
        base, QUANTILE_LEVELS, 0.0
    )
    # Narrowing the band to 5% scores less of the distribution's centre.
    assert tail_crps_from_quantiles(
        base, QUANTILE_LEVELS, 0.0, tail=0.05
    ) != tail_crps_from_quantiles(base, QUANTILE_LEVELS, 0.0, tail=0.1)


@pytest.mark.unit
def test_scaled_skill_is_one_minus_the_crps_ratio() -> None:
    assert scaled_crps_skill(0.5, 1.0) == pytest.approx(0.5)
    assert scaled_crps_skill(1.0, 1.0) == pytest.approx(0.0)
    assert scaled_crps_skill(1.5, 1.0) == pytest.approx(-0.5)


@pytest.mark.unit
def test_scaled_skill_is_nan_when_the_baseline_is_zero() -> None:
    assert math.isnan(scaled_crps_skill(0.5, 0.0))
    assert math.isnan(mean_skill([]))
    assert mean_skill([0.5, math.nan, -0.5]) == pytest.approx(0.0)


def _scores() -> list[ScoredForecast]:
    out = []
    for day_i, day in enumerate(("2024-01-02", "2024-02-01", "2024-03-01")):
        for tick_i, ticker in enumerate(("AAA", "BBB", "CCC")):
            base = 0.01 + 0.005 * tick_i + 0.002 * day_i
            out.append(
                ScoredForecast(day=day, ticker=ticker, model_crps=base / 2.0, baseline_crps=base)
            )
    return out


@pytest.mark.unit
def test_skill_summary_reports_a_clear_beat() -> None:
    s = skill_summary(_scores(), seed=7, replicates=500)
    assert s.skill == pytest.approx(0.5)
    assert s.ci_low > 0.0 and s.ci_high <= 1.0
    assert s.n_obs == 9 and s.n_dates == 3
    assert s.mean_baseline_crps == pytest.approx(2.0 * s.mean_model_crps)


@pytest.mark.unit
def test_skill_summary_is_reproducible_for_a_given_seed() -> None:
    a = skill_summary(_scores(), seed=99, replicates=200)
    b = skill_summary(_scores(), seed=99, replicates=200)
    assert a == b


@pytest.mark.unit
def test_skill_summary_resamples_whole_dates() -> None:
    # Days disagree about the skill (+80%, 0%, -40%), so resampling dates
    # produces a real interval around the pooled mean.
    scores = [
        ScoredForecast(day=d, ticker=t, model_crps=m, baseline_crps=1.0)
        for d, m in (("2024-01-02", 0.2), ("2024-02-01", 1.0), ("2024-03-01", 1.4))
        for t in ("AAA", "BBB")
    ]
    s = skill_summary(scores, seed=11, replicates=500)
    assert s.skill == pytest.approx(1.0 - (0.2 + 1.0 + 1.4) / 3.0)
    assert s.ci_low < s.skill < s.ci_high


@pytest.mark.unit
def test_skill_summary_with_one_date_cannot_see_variation() -> None:
    # One resample unit means every replicate pools the same observations:
    # the interval degenerates to the point estimate. n_dates reports the
    # effective sample so the harness can label the low-power case.
    scores = [
        ScoredForecast(day="2024-01-02", ticker=t, model_crps=m, baseline_crps=1.0)
        for t, m in (("AAA", 0.4), ("BBB", 0.6), ("CCC", 0.8))
    ]
    s = skill_summary(scores, seed=5, replicates=100)
    assert s.n_dates == 1
    assert s.ci_low == pytest.approx(s.skill)
    assert s.ci_high == pytest.approx(s.skill)


@pytest.mark.unit
def test_skill_summary_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        skill_summary([], seed=1)
