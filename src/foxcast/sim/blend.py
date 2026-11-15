"""Forecast 6, the blend — the headline (MODELS.md row 6).

Fallback rules implemented; the 21-day combination method awaits proving
experiment 5 (change AD). Per change AW, the headline is forecast 3 at the
5-day horizon, and at any horizon when the option chain fails its liquidity
contract — the passthrough relabels the result ``blend`` and records the
ingredient and reason in ``detail`` so the ledger never mixes identities.
"""

from dataclasses import replace
from typing import Final

from foxcast.sim.grid import QuantileForecast

FIVE_DAY_HORIZON: Final = 5
NOT_BUILT: Final = (
    "forecast 6 (blend) combination method is not fixed until proving experiment 5 (Stage 2)"
)


def _passthrough(ingredient: QuantileForecast, *, horizon: int, reason: str) -> QuantileForecast:
    return replace(
        ingredient,
        forecast_id="blend",
        horizon_days=horizon,
        detail={
            **ingredient.detail,
            "blend_source": ingredient.forecast_id,
            "fallback": reason,
        },
    )


def forecast_blend(
    history: QuantileForecast | str,
    corrected: QuantileForecast | str,
    *,
    horizon_days: int,
) -> QuantileForecast | str:
    """The headline forecast, built from the forecast-3 and forecast-5 results."""
    if horizon_days == FIVE_DAY_HORIZON:
        if isinstance(history, str):
            return history
        return _passthrough(history, horizon=horizon_days, reason="5-day horizon")
    if isinstance(history, str):
        return f"blend unavailable: history forecast unavailable: {history}"
    if isinstance(corrected, str):
        return _passthrough(history, horizon=horizon_days, reason=corrected)
    return NOT_BUILT
