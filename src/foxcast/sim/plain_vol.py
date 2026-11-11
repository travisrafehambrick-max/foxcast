"""Forecast 2, the plain volatility model (brief 2.2, step 3, without event handling).

One asymmetric, heavy-tailed volatility model on every day: falls raise volatility more
than rises (GJR term) and residuals are Student-t. Paths are simulated by resampling the
model's standardised residuals (filtered historical simulation), with a recorded seed.

The fit check required on every fit compares the model's implied variance with realised
variance over the same history.
"""

from typing import Final

import numpy as np
from arch import arch_model
from arch.univariate.base import ARCHModelResult

from foxcast.sim.grid import DEFAULT_PATHS, QuantileForecast, quantiles_of

MIN_HISTORY_DAYS: Final = 500
PERCENT: Final = 100.0  # arch is better conditioned on returns in percent
MODEL_LABEL: Final = "GJR-GARCH(1,1,1) Student-t, zero mean, bootstrap paths"


def fit_plain_vol(log_returns: np.ndarray) -> ARCHModelResult:
    if len(log_returns) < MIN_HISTORY_DAYS:
        raise ValueError(
            f"need at least {MIN_HISTORY_DAYS} days of history, got {len(log_returns)}"
        )
    model = arch_model(
        np.asarray(log_returns, dtype=float) * PERCENT,
        mean="Zero",
        vol="GARCH",
        p=1,
        o=1,
        q=1,
        dist="t",
    )
    return model.fit(disp="off")


def forecast_plain_vol(
    log_returns: np.ndarray,
    *,
    horizon: int,
    seed: int,
    paths: int = DEFAULT_PATHS,
    fitted: ARCHModelResult | None = None,
) -> QuantileForecast:
    result = fitted if fitted is not None else fit_plain_vol(log_returns)
    forecast = result.forecast(
        horizon=horizon,
        method="bootstrap",
        simulations=paths,
        random_state=np.random.RandomState(seed),
        reindex=False,
    )
    paths_values = forecast.simulations.values
    if paths_values is None:
        raise RuntimeError("arch returned no simulated paths")
    simulated = paths_values[-1] / PERCENT  # (paths, horizon) daily log returns
    horizon_log_returns = simulated.sum(axis=1)

    realised = float(np.var(np.asarray(log_returns, dtype=float), ddof=1))
    implied = float(np.mean(result.conditional_volatility**2)) / PERCENT**2
    return QuantileForecast(
        forecast_id="plain_vol",
        horizon_days=horizon,
        values=quantiles_of(np.expm1(horizon_log_returns)),
        seed=seed,
        paths=paths,
        detail={
            "model": MODEL_LABEL,
            "params": {k: float(v) for k, v in result.params.items()},
            "simulated_log_return_variance": float(np.var(horizon_log_returns, ddof=1)),
            "fit_check_variance_ratio": implied / realised,
        },
    )
