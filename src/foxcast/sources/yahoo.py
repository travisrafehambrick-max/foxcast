"""Yahoo Finance through `yfinance` (unofficial access; brief F1, F3).

Yahoo has no free history for option chains or analyst views, so foxcast captures them in
a daily snapshot. Access is unofficial and can break without notice, so every call returns
a table or an ``Unavailable`` reason, and every table must pass a data contract (change
AI). Quotes are stamped with the snapshot time (change BD).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol

import numpy as np
import pandas as pd
import pandera.errors as pa_errors
import pandera.pandas as pa

from foxcast.sources.base import Unavailable


def _high_at_least_low(table: pd.DataFrame) -> pd.Series:
    return table["high"] >= table["low"]


PRICE_CONTRACT: Final = pa.DataFrameSchema(
    {
        "date": pa.Column(object, unique=True),
        "open": pa.Column(float, pa.Check.gt(0)),
        "high": pa.Column(float, pa.Check.gt(0)),
        "low": pa.Column(float, pa.Check.gt(0)),
        "close": pa.Column(float, pa.Check.gt(0)),
        "adj_close": pa.Column(float, pa.Check.gt(0)),
        "volume": pa.Column(float, pa.Check.ge(0)),
        "dividends": pa.Column(float, pa.Check.ge(0)),
        "splits": pa.Column(float, pa.Check.ge(0)),
    },
    checks=[
        pa.Check(_high_at_least_low, error="high below low"),
        pa.Check(lambda t: t["date"].is_monotonic_increasing, error="dates not sorted"),
    ],
    strict=True,
)

CHAIN_CONTRACT: Final = pa.DataFrameSchema(
    {
        "strike": pa.Column(float, pa.Check.gt(0)),
        "expiry": pa.Column("datetime64[ns]"),
        "option_type": pa.Column(str, pa.Check.isin(["call", "put"])),
        "last_price": pa.Column(float, pa.Check.ge(0), nullable=True),
        "bid": pa.Column(float, pa.Check.ge(0), nullable=True),
        "ask": pa.Column(float, pa.Check.ge(0), nullable=True),
        "volume": pa.Column(float, pa.Check.ge(0), nullable=True),
        "open_interest": pa.Column(float, pa.Check.ge(0), nullable=True),
        "implied_vol": pa.Column(float, pa.Check.ge(0), nullable=True),
        "last_trade_date": pa.Column("datetime64[ns, UTC]", nullable=True),
        "quote_time": pa.Column("datetime64[ns, UTC]"),
    },
    strict=True,
)


class YahooTicker(Protocol):
    """The parts of ``yfinance.Ticker`` foxcast uses; tests pass a fake."""

    options: tuple[str, ...]

    def history(self, **kwargs: Any) -> pd.DataFrame: ...

    def option_chain(self, date: str) -> Any: ...

    def get_analyst_price_targets(self) -> dict[str, Any]: ...

    def get_recommendations_summary(self) -> pd.DataFrame: ...


def default_ticker_factory(symbol: str) -> YahooTicker:
    import yfinance  # imported lazily: only snapshot and forecast commands need it

    ticker: YahooTicker = yfinance.Ticker(symbol)
    return ticker


@dataclass(frozen=True, eq=False)
class YahooTable:
    kind: str
    symbol: str
    table: pd.DataFrame
    fetched_at: datetime


YahooResult = YahooTable | Unavailable


def _validate(
    kind: str, symbol: str, table: pd.DataFrame, contract: pa.DataFrameSchema, now: datetime
) -> YahooResult:
    if table.empty:
        return Unavailable(f"yahoo {kind} {symbol}: no rows", "")
    try:
        valid = contract.validate(table, lazy=True)
    except (pa_errors.SchemaError, pa_errors.SchemaErrors, TypeError, ValueError) as exc:
        return Unavailable(f"yahoo {kind} {symbol}: data contract failed: {exc}", "")
    return YahooTable(kind, symbol, valid, now)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def fetch_prices(
    symbol: str,
    *,
    factory: Callable[[str], YahooTicker] = default_ticker_factory,
    now: Callable[[], datetime] = _utc_now,
) -> YahooResult:
    """Daily prices with dividends and splits, both raw and adjusted closes."""
    try:
        raw = factory(symbol).history(period="max", auto_adjust=False, actions=True)
    except Exception as exc:
        return Unavailable(f"yahoo prices {symbol}: {type(exc).__name__}: {exc}", "")
    table = pd.DataFrame(
        {
            "date": [ts.date() for ts in pd.to_datetime(raw.index)],
            "open": raw.get("Open", pd.Series(dtype=float)).to_numpy(dtype=float),
            "high": raw.get("High", pd.Series(dtype=float)).to_numpy(dtype=float),
            "low": raw.get("Low", pd.Series(dtype=float)).to_numpy(dtype=float),
            "close": raw.get("Close", pd.Series(dtype=float)).to_numpy(dtype=float),
            "adj_close": raw.get("Adj Close", pd.Series(dtype=float)).to_numpy(dtype=float),
            "volume": raw.get("Volume", pd.Series(dtype=float)).to_numpy(dtype=float),
            "dividends": raw.get("Dividends", pd.Series(0.0, index=raw.index)).to_numpy(float),
            "splits": raw.get("Stock Splits", pd.Series(0.0, index=raw.index)).to_numpy(float),
        }
    )
    return _validate("prices", symbol, table, PRICE_CONTRACT, now())


def _chain_rows(frame: pd.DataFrame, expiry: str, option_type: str) -> pd.DataFrame:
    def column(name: str) -> np.ndarray:
        return frame[name].to_numpy(dtype=float) if name in frame else np.full(len(frame), np.nan)

    trade = (
        frame["lastTradeDate"] if "lastTradeDate" in frame else pd.Series(pd.NaT, index=frame.index)
    )
    return pd.DataFrame(
        {
            "strike": column("strike"),
            "expiry": pd.Timestamp(expiry),
            "option_type": option_type,
            "last_price": column("lastPrice"),
            "bid": column("bid"),
            "ask": column("ask"),
            "volume": column("volume"),
            "open_interest": column("openInterest"),
            "implied_vol": column("impliedVolatility"),
            "last_trade_date": pd.to_datetime(trade, utc=True).to_numpy(),
        }
    )


def fetch_option_chains(
    symbol: str,
    *,
    factory: Callable[[str], YahooTicker] = default_ticker_factory,
    now: Callable[[], datetime] = _utc_now,
) -> YahooResult:
    """Every listed expiry's calls and puts, stamped with the snapshot time."""
    snapshot_time = now()
    try:
        ticker = factory(symbol)
        parts = []
        for expiry in ticker.options:
            chain = ticker.option_chain(expiry)
            parts.append(_chain_rows(chain.calls, expiry, "call"))
            parts.append(_chain_rows(chain.puts, expiry, "put"))
    except Exception as exc:
        return Unavailable(f"yahoo chains {symbol}: {type(exc).__name__}: {exc}", "")
    if not parts:
        return Unavailable(f"yahoo chains {symbol}: no listed expiries", "")
    table = pd.concat(parts, ignore_index=True)
    table["last_trade_date"] = pd.to_datetime(table["last_trade_date"], utc=True)
    table["quote_time"] = pd.Timestamp(snapshot_time).tz_convert("UTC")
    table["expiry"] = table["expiry"].astype("datetime64[ns]")
    table["last_trade_date"] = table["last_trade_date"].astype("datetime64[ns, UTC]")
    table["quote_time"] = table["quote_time"].astype("datetime64[ns, UTC]")
    return _validate("chains", symbol, table, CHAIN_CONTRACT, snapshot_time)


def fetch_analyst_views(
    symbol: str,
    *,
    factory: Callable[[str], YahooTicker] = default_ticker_factory,
    now: Callable[[], datetime] = _utc_now,
) -> YahooResult:
    """Price-target range and the recommendations summary, as one long table."""
    try:
        ticker = factory(symbol)
        targets = ticker.get_analyst_price_targets() or {}
        summary = ticker.get_recommendations_summary()
    except Exception as exc:
        return Unavailable(f"yahoo analysts {symbol}: {type(exc).__name__}: {exc}", "")
    rows = [
        {"item": f"target_{key}", "value": float(value)}
        for key, value in targets.items()
        if isinstance(value, int | float) and np.isfinite(value)
    ]
    if summary is not None and not summary.empty:
        latest = summary.iloc[0]
        rows += [
            {"item": f"rating_{col}", "value": float(latest[col])}
            for col in ("strongBuy", "buy", "hold", "sell", "strongSell")
            if col in latest
        ]
    contract = pa.DataFrameSchema(
        {"item": pa.Column(str, unique=True), "value": pa.Column(float)}, strict=True
    )
    return _validate(
        "analysts", symbol, pd.DataFrame(rows, columns=["item", "value"]), contract, now()
    )
