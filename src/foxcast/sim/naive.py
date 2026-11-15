"""Forecast 1, naive: constant volatility, zero drift (the floor; brief 2.2)."""

from typing import Final

import numpy as np

from foxcast.sim.grid import DEFAULT_PATHS, QuantileForecast, quantiles_of

VOL_WINDOW_DAYS: Final = 63  # about three months of trading days


def forecast_naive(
    log_returns: np.ndarray, *, horizon: int, seed: int, paths: int = DEFAULT_PATHS
) -> QuantileForecast | str:
    """Normal daily log returns with one volatility from the last ``VOL_WINDOW_DAYS`` days,
    or the reason the forecast is unavailable."""
    if len(log_returns) < VOL_WINDOW_DAYS:
        return f"need at least {VOL_WINDOW_DAYS} days of history, got {len(log_returns)}"
    sigma = float(np.std(log_returns[-VOL_WINDOW_DAYS:], ddof=1))
    rng = np.random.default_rng(seed)
    # Log mean of -sigma^2 T / 2 gives a zero expected simple return (brief 2.2).
    variance = sigma**2 * horizon
    horizon_log_returns = rng.normal(-0.5 * variance, np.sqrt(variance), size=paths)
    return QuantileForecast(
        forecast_id="naive",
        horizon_days=horizon,
        values=quantiles_of(np.expm1(horizon_log_returns)),
        seed=seed,
        paths=paths,
        detail={"daily_sigma": sigma, "window_days": VOL_WINDOW_DAYS},
    )
