"""Forecast 4, option-implied, raw (brief 2.2; `oipd` behind our own interface).

The risk-neutral distribution of the price at the option expiry nearest the horizon,
from Breeden-Litzenberger on an SVI-fitted smile. It is used at the 21-day horizon only
(amendment D). If no expiry lies close enough to the horizon date, the forecast is
unavailable rather than stretched.

Known limits (docs/MODELS.md): it reflects risk appetite as well as expectations, and
`oipd` makes no early-exercise or dividend correction.
"""

import warnings
from datetime import date
from typing import Final

import pandas as pd
from oipd import MarketInputs, ProbCurve

from foxcast.sim.grid import QUANTILE_LEVELS, QuantileForecast

MAX_EXPIRY_GAP_DAYS: Final = 7
FORECAST_ID: Final = "option_raw"


def nearest_expiry(chain: pd.DataFrame, horizon_date: date) -> date | None:
    expiries = sorted({pd.Timestamp(e).date() for e in chain["expiry"]})
    if not expiries:
        return None
    best = min(expiries, key=lambda e: abs((e - horizon_date).days))
    return best if abs((best - horizon_date).days) <= MAX_EXPIRY_GAP_DAYS else None


def forecast_option_implied(
    chain: pd.DataFrame,
    *,
    spot: float,
    valuation: date,
    horizon_date: date,
    risk_free_rate: float = 0.0,
) -> QuantileForecast | str:
    """Quantiles of the simple return to the nearest expiry, or the reason it is unavailable."""
    expiry = nearest_expiry(chain, horizon_date)
    if expiry is None:
        return f"no option expiry within {MAX_EXPIRY_GAP_DAYS} days of {horizon_date}"
    single = chain[chain["expiry"].map(lambda e: pd.Timestamp(e).date()) == expiry]
    market = MarketInputs(
        risk_free_rate=risk_free_rate, valuation_date=valuation, underlying_price=spot
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            curve = ProbCurve.from_chain(single.reset_index(drop=True), market)
            prices = [float(curve.quantile(level)) for level in QUANTILE_LEVELS]
    except Exception as exc:
        return f"option-implied fit failed: {type(exc).__name__}: {exc}"
    returns = [price / spot - 1.0 for price in prices]
    monotone = [max(returns[: i + 1]) for i in range(len(returns))]
    return QuantileForecast(
        forecast_id=FORECAST_ID,
        horizon_days=(expiry - valuation).days,
        values=tuple(monotone),
        seed=0,
        paths=0,
        detail={
            "expiry": expiry.isoformat(),
            "spot": spot,
            "calendar_days": (expiry - valuation).days,
        },
    )
