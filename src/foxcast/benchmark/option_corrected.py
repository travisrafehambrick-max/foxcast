"""Forecast 5, option-implied corrected for its measured past bias (MODELS.md
row 5) — stub.

Forecast 4 adjusted for its measured past bias, refitted from the past only
(honesty rule 12). The correction arrives as a parameter — already estimated
from past data by the caller — because a corrected forecast without a fitted
correction is just the raw forecast, and the signature should make that
impossible to forget. Today the model is not built, so every call reports that
instead of a number.
"""

from collections.abc import Sequence
from datetime import date
from typing import Final, Protocol

import pandas as pd

from foxcast.sim.grid import QuantileForecast

NOT_BUILT: Final = "forecast 5 (option-implied, corrected) is not built (Stage 2)"


class OptionBiasCorrection(Protocol):
    """A fitted past-bias adjustment for the raw option-implied quantiles (honesty
    rule 12).

    Provisional seam: the correction method is the real model's decision. Anything
    with this method satisfies the stub.
    """

    def adjust(self, values: Sequence[float], levels: Sequence[float]) -> tuple[float, ...]: ...


def forecast_option_corrected(
    chain: pd.DataFrame,
    *,
    spot: float,
    valuation: date,
    horizon_date: date,
    correction: OptionBiasCorrection,
    risk_free_rate: float = 0.0,
    horizon_days: int | None = None,
) -> QuantileForecast | str:
    """The bias-corrected option-implied forecast, or the reason it is unavailable."""
    return NOT_BUILT
