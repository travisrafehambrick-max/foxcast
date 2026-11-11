"""The quantile grid every forecast is frozen on (brief 2.2, "What is forecast").

101 levels: 0.5%, then 1% to 99%, then 99.5%, so the frozen record can express the 99%
interval that scorecard row 2b tests.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np

QUANTILE_LEVELS: Final[tuple[float, ...]] = (
    0.005,
    *(round(p / 100, 2) for p in range(1, 100)),
    0.995,
)
DEFAULT_PATHS: Final = 20_000


@dataclass(frozen=True)
class QuantileForecast:
    """A forecast of the simple return over ``horizon_days`` trading days, as quantiles."""

    forecast_id: str
    horizon_days: int
    values: tuple[float, ...]
    seed: int
    paths: int
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.values) != len(QUANTILE_LEVELS):
            raise ValueError(f"need {len(QUANTILE_LEVELS)} quantiles, got {len(self.values)}")
        if any(b < a for a, b in zip(self.values, self.values[1:], strict=False)):
            raise ValueError("quantiles must be non-decreasing")
        if not all(np.isfinite(self.values)):
            raise ValueError("quantiles must be finite")

    def to_payload(self) -> dict[str, Any]:
        """The ledger payload: levels and values stored side by side, explicitly."""
        return {
            "forecast_id": self.forecast_id,
            "horizon_days": self.horizon_days,
            "levels": list(QUANTILE_LEVELS),
            "values": list(self.values),
            "seed": self.seed,
            "paths": self.paths,
            "detail": dict(self.detail),
        }


def quantiles_of(samples: Sequence[float] | np.ndarray) -> tuple[float, ...]:
    """Empirical quantiles of simulated simple returns on the standard grid."""
    values = np.quantile(np.asarray(samples, dtype=float), QUANTILE_LEVELS)
    return tuple(float(v) for v in np.maximum.accumulate(values))
