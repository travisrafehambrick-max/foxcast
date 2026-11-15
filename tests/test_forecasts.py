"""Forecasts 1, 2 and 4 against closed-form results (scorecard row 2a, change BH)."""

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from arch.univariate import GARCH, StudentsT, ZeroMean
from scipy.stats import norm

from foxcast.benchmark.option_implied import forecast_option_implied
from foxcast.sim.grid import QUANTILE_LEVELS, QuantileForecast
from foxcast.sim.naive import forecast_naive
from foxcast.sim.plain_vol import fit_plain_vol, forecast_plain_vol

SEED = 20261111
MIN_QUOTE = 0.05


def gbm_log_returns(sigma: float, days: int, seed: int = 1) -> np.ndarray:
    return np.random.default_rng(seed).normal(0.0, sigma, size=days)


def garch_log_returns(days: int = 2500, seed: int = 7) -> np.ndarray:
    # The seed must reach the simulator: arch_model(None, ...) draws from numpy's global
    # state, so each run fitted a different series and the variance check failed at random.
    model = ZeroMean(None, volatility=GARCH(p=1, o=1, q=1), distribution=StudentsT(seed=seed))
    params = np.array([0.02, 0.05, 0.08, 0.88, 7.0])
    sim = model.simulate(params, nobs=days, burn=500, initial_value=None)
    return sim["data"].to_numpy() / 100.0  # percent to fraction


@pytest.mark.unit
def test_grid_has_101_levels_reaching_the_99_percent_interval() -> None:
    assert len(QUANTILE_LEVELS) == 101
    assert QUANTILE_LEVELS[0] == 0.005
    assert QUANTILE_LEVELS[-1] == 0.995
    assert list(QUANTILE_LEVELS) == sorted(QUANTILE_LEVELS)


@pytest.mark.unit
def test_grid_rejects_unsorted_or_wrong_length_values() -> None:
    with pytest.raises(ValueError):
        QuantileForecast("x", 5, tuple(range(100, -1, -1)), seed=1, paths=1, detail={})
    with pytest.raises(ValueError):
        QuantileForecast("x", 5, (0.0, 1.0), seed=1, paths=1, detail={})


@pytest.mark.unit
@pytest.mark.parametrize("horizon", [5, 21])
def test_naive_matches_lognormal_quantiles(horizon: int) -> None:
    sigma = 0.02
    returns = gbm_log_returns(sigma, 2000)
    fc = forecast_naive(returns, horizon=horizon, seed=SEED)
    sample_sigma = float(np.std(returns[-63:], ddof=1))
    for level, value in zip(QUANTILE_LEVELS, fc.values, strict=True):
        variance = sample_sigma**2 * horizon
        expected = math.exp(-0.5 * variance + norm.ppf(level) * math.sqrt(variance)) - 1
        assert value == pytest.approx(expected, abs=0.004), level


@pytest.mark.unit
def test_forecasts_are_reproducible_from_the_seed() -> None:
    returns = gbm_log_returns(0.015, 1000)
    assert forecast_naive(returns, horizon=21, seed=5) == forecast_naive(
        returns, horizon=21, seed=5
    )
    assert forecast_naive(returns, horizon=21, seed=5) != forecast_naive(
        returns, horizon=21, seed=6
    )


@pytest.mark.unit
def test_naive_needs_enough_history() -> None:
    result = forecast_naive(np.zeros(10), horizon=5, seed=1)
    assert isinstance(result, str)
    assert "history" in result


@pytest.mark.unit
@pytest.mark.parametrize("horizon", [5, 21])
def test_plain_vol_simulated_variance_matches_arch_analytic(horizon: int) -> None:
    returns = garch_log_returns()
    fitted = fit_plain_vol(returns)
    analytic = fitted.forecast(horizon=horizon, method="analytic", reindex=False)
    analytic_total_var = float(analytic.variance.to_numpy()[-1].sum()) / 100.0**2
    fc = forecast_plain_vol(returns, horizon=horizon, seed=SEED, fitted=fitted)
    simulated_total_var = fc.detail["simulated_log_return_variance"]
    assert simulated_total_var == pytest.approx(analytic_total_var, rel=0.10)


@pytest.mark.unit
def test_plain_vol_records_the_fit_check() -> None:
    fc = forecast_plain_vol(garch_log_returns(), horizon=21, seed=SEED)
    assert fc.forecast_id == "plain_vol"
    assert fc.detail["model"] == "GJR-GARCH(1,1,1) Student-t, zero mean, bootstrap paths"
    assert 0.5 < fc.detail["fit_check_variance_ratio"] < 2.0


# --- forecast 4: option-implied, raw ----------------------------------------------------


def black_scholes_chain(spot: float, sigma: float, valuation: date, expiry: date) -> pd.DataFrame:
    t = (expiry - valuation).days / 365.0
    rows = []
    for strike in np.arange(spot * 0.7, spot * 1.3 + 1e-9, spot * 0.02):
        d1 = (math.log(spot / strike) + 0.5 * sigma**2 * t) / (sigma * math.sqrt(t))
        d2 = d1 - sigma * math.sqrt(t)
        call = spot * norm.cdf(d1) - strike * norm.cdf(d2)
        put = strike * norm.cdf(-d2) - spot * norm.cdf(-d1)
        for kind, price in (("call", call), ("put", put)):
            if price < MIN_QUOTE:  # real chains do not quote options this cheap
                continue
            rows.append(
                {
                    "strike": round(float(strike), 2),
                    "expiry": pd.Timestamp(expiry),
                    "option_type": kind,
                    "last_price": price,
                    "bid": price * 0.99,
                    "ask": price * 1.01,
                    "volume": 100,
                    "last_trade_date": pd.Timestamp(valuation),
                }
            )
    return pd.DataFrame(rows)


@pytest.mark.unit
def test_option_implied_recovers_a_lognormal_chain() -> None:
    valuation = date(2026, 11, 11)
    expiry = valuation + timedelta(days=30)
    spot, sigma = 100.0, 0.30
    chain = black_scholes_chain(spot, sigma, valuation, expiry)
    fc = forecast_option_implied(chain, spot=spot, valuation=valuation, horizon_date=expiry)
    assert isinstance(fc, QuantileForecast)
    t = 30 / 365.0
    for level in (0.10, 0.25, 0.50, 0.75, 0.90):
        index = QUANTILE_LEVELS.index(level)
        expected = math.exp(-0.5 * sigma**2 * t + norm.ppf(level) * sigma * math.sqrt(t)) - 1
        assert fc.values[index] == pytest.approx(expected, abs=0.015), level


@pytest.mark.unit
def test_option_implied_without_a_near_expiry_is_unavailable() -> None:
    valuation = date(2026, 11, 11)
    chain = black_scholes_chain(100.0, 0.3, valuation, valuation + timedelta(days=90))
    result = forecast_option_implied(
        chain, spot=100.0, valuation=valuation, horizon_date=valuation + timedelta(days=30)
    )
    assert isinstance(result, str)
    assert "expiry" in result


@pytest.mark.unit
def test_plain_vol_carries_no_historical_drift() -> None:
    drifted = garch_log_returns() + 0.002  # a strong upward drift, about 50% a year
    fc = forecast_plain_vol(drifted, horizon=21, seed=SEED)
    median = fc.values[QUANTILE_LEVELS.index(0.50)]
    assert abs(median) < 0.01
