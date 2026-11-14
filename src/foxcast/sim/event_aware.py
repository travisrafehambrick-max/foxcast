"""Forecast 3, the event-aware model (brief 2.2; MODELS.md row 3) — stub.

Forecast 2 plus the event calendar and measured event effects. The signature below
is the real model's interface: ``events`` is the event calendar (one row per
scheduled event, at least ``event_date`` and ``kind``), ``fitted`` an optional
pre-fit of the underlying volatility model from past-only data (honesty rule 12),
and ``seed`` drives the simulated paths. Today the model is not built, so every
call reports that instead of a number.
"""

from datetime import date
from typing import Final

import numpy as np
import pandas as pd
from arch.univariate.base import ARCHModelResult

from foxcast.sim.grid import DEFAULT_PATHS, QuantileForecast

REQUIRED_EVENT_COLUMNS: Final = ("event_date", "kind")
NOT_BUILT: Final = "forecast 3 (event-aware) is not built (Stage 2)"


def forecast_event_aware(
    log_returns: np.ndarray,
    events: pd.DataFrame,
    *,
    forecast_day: date,
    horizon: int,
    seed: int,
    paths: int = DEFAULT_PATHS,
    fitted: ARCHModelResult | None = None,
) -> QuantileForecast | str:
    """The event-aware forecast, or the reason it is unavailable."""
    missing = [c for c in REQUIRED_EVENT_COLUMNS if c not in events.columns]
    if missing:
        raise ValueError(f"event calendar is missing required column {missing[0]!r}")
    return NOT_BUILT
