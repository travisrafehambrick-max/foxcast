"""Quantile-form CRPS scoring (scorecard rows 2b-2f, change BA)."""

import math

import pytest
from scipy.stats import norm

from foxcast.scoring.crps import crps_from_quantiles, tail_crps_from_quantiles
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
