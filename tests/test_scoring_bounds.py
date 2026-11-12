"""The error bound used by row 1e and the planted-claim rate beside row 1c."""

import numpy as np
import pytest

from foxcast.scoring.bounds import clopper_pearson_upper, max_errors_below


@pytest.mark.unit
@pytest.mark.parametrize(
    ("errors", "n", "expected"),
    [
        (0, 100, 0.0295),  # 1 - 0.05 ** (1 / 100)
        (1, 100, 0.0466),
        (2, 100, 0.0616),
        (0, 20, 0.1391),
    ],
)
def test_one_sided_upper_bound_matches_known_values(errors: int, n: int, expected: float) -> None:
    assert clopper_pearson_upper(errors, n) == pytest.approx(expected, abs=5e-5)


@pytest.mark.unit
def test_all_wrong_gives_a_bound_of_one() -> None:
    assert clopper_pearson_upper(10, 10) == 1.0


@pytest.mark.unit
def test_the_bound_rises_with_the_error_count() -> None:
    bounds = [clopper_pearson_upper(k, 100) for k in range(6)]
    assert bounds == sorted(bounds)
    assert len(set(bounds)) == len(bounds)


@pytest.mark.unit
def test_a_higher_confidence_gives_a_wider_bound() -> None:
    assert clopper_pearson_upper(1, 100, confidence=0.99) > clopper_pearson_upper(1, 100)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("errors", "n", "confidence"),
    [(-1, 10, 0.95), (11, 10, 0.95), (0, 0, 0.95), (0, 10, 0.0), (0, 10, 1.0)],
)
def test_bad_inputs_are_refused(errors: int, n: int, confidence: float) -> None:
    with pytest.raises(ValueError):
        clopper_pearson_upper(errors, n, confidence=confidence)


@pytest.mark.unit
def test_about_one_hundred_claims_allow_at_most_one_wrong_under_five_percent() -> None:
    # The scorecard tells the auditor this; the arithmetic must agree.
    assert max_errors_below(100, 0.05) == 1


@pytest.mark.unit
def test_a_small_sample_can_allow_no_errors_at_all() -> None:
    assert max_errors_below(20, 0.05) == -1  # even 0 of 20 cannot get under 5%


@pytest.mark.unit
def test_fifty_nine_claims_is_the_smallest_sample_that_can_pass() -> None:
    # 1 - 0.05 ** (1 / 59) is about 4.95%; at 58 it is about 5.03%.
    assert max_errors_below(59, 0.05) == 0
    assert max_errors_below(58, 0.05) == -1


@pytest.mark.unit
@pytest.mark.parametrize(("errors", "n"), [(1.5, 10), (True, 10), (0, 10.0)])
def test_counts_must_be_whole_numbers(errors: object, n: object) -> None:
    with pytest.raises(TypeError):
        clopper_pearson_upper(errors, n)  # type: ignore[arg-type]


@pytest.mark.unit
def test_max_errors_below_refuses_a_bad_bound() -> None:
    with pytest.raises(ValueError):
        max_errors_below(100, 0.0)


@pytest.mark.unit
def test_numpy_integer_counts_are_accepted() -> None:
    assert clopper_pearson_upper(np.int64(1), np.int64(100)) == clopper_pearson_upper(1, 100)
