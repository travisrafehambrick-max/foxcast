"""Stage 0.5: Yahoo snapshot, watchlist and on-demand forecasts."""

import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm
from typer.testing import CliRunner

from foxcast.cli import app as cli_app
from foxcast.daily import code_version, horizon_date, run_daily_forecasts, seed_for
from foxcast.ledger.chain import Ledger
from foxcast.snapshot import MarketClosedError, market_is_open, run_snapshot
from foxcast.sources.base import Unavailable
from foxcast.sources.yahoo import (
    YahooTable,
    fetch_analyst_views,
    fetch_option_chains,
    fetch_prices,
)
from foxcast.watchlist import draw_watchlist, load_watchlist, save_watchlist

OPEN = datetime(2026, 11, 11, 16, 0, tzinfo=UTC)  # 11:00 New York, a Wednesday
CLOSED = datetime(2026, 11, 14, 16, 0, tzinfo=UTC)  # a Saturday


def price_history(days: int = 800, seed: int = 3) -> pd.DataFrame:
    index = pd.bdate_range(end="2026-11-10", periods=days, tz="America/New_York")
    closes = 100 * np.exp(np.cumsum(np.random.default_rng(seed).normal(0, 0.015, days)))
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes * 1.01,
            "Low": closes * 0.99,
            "Close": closes,
            "Adj Close": closes,
            "Volume": 1e6,
            "Dividends": 0.0,
            "Stock Splits": 0.0,
        },
        index=index,
    )


def chain_frame(spot: float, sigma: float, days: int) -> pd.DataFrame:
    t = days / 365.0
    strikes = np.arange(spot * 0.6, spot * 1.4, spot * 0.02)
    d1 = (np.log(spot / strikes) + 0.5 * sigma**2 * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    calls = np.maximum(spot * norm.cdf(d1) - strikes * norm.cdf(d2), 0.01)
    puts = np.maximum(strikes * norm.cdf(-d2) - spot * norm.cdf(-d1), 0.01)

    def frame(prices: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "strike": strikes,
                "lastPrice": prices,
                "bid": prices * 0.99,
                "ask": prices * 1.01,
                "volume": 100.0,
                "openInterest": 500.0,
                "impliedVolatility": sigma,
                "lastTradeDate": pd.Timestamp("2026-11-11T15:00:00Z"),
            }
        )

    return SimpleNamespace(calls=frame(calls), puts=frame(puts))  # type: ignore[return-value]


class FakeTicker:
    def __init__(self, *, fail: bool = False, expiry_days: int = 30) -> None:
        self._fail = fail
        self._expiry = (date(2026, 11, 11) + timedelta(days=expiry_days)).isoformat()
        self.options = (self._expiry,)

    def history(self, **kwargs: Any) -> pd.DataFrame:
        if self._fail:
            raise ConnectionError("yahoo down")
        return price_history()

    def option_chain(self, date: str) -> Any:
        return chain_frame(float(price_history()["Close"].iloc[-1]), 0.3, 30)

    def get_analyst_price_targets(self) -> dict[str, Any]:
        return {"low": 80.0, "mean": 110.0, "high": 150.0, "current": 100.0}

    def get_recommendations_summary(self) -> pd.DataFrame:
        return pd.DataFrame(
            [{"period": "0m", "strongBuy": 5, "buy": 10, "hold": 8, "sell": 1, "strongSell": 0}]
        )


def fake(**kwargs: Any) -> Any:
    return lambda symbol: FakeTicker(**kwargs)


# --- Yahoo provider ----------------------------------------------------------------------


@pytest.mark.unit
def test_prices_pass_the_contract() -> None:
    result = fetch_prices("ABC", factory=fake(), now=lambda: OPEN)
    assert isinstance(result, YahooTable)
    assert len(result.table) == 800
    assert result.table["date"].is_monotonic_increasing


@pytest.mark.unit
def test_yahoo_failure_is_unavailable() -> None:
    result = fetch_prices("ABC", factory=fake(fail=True), now=lambda: OPEN)
    assert isinstance(result, Unavailable)
    assert "yahoo down" in result.reason


@pytest.mark.unit
def test_bad_prices_break_the_contract() -> None:
    class Bad(FakeTicker):
        def history(self, **kwargs: Any) -> pd.DataFrame:
            frame = price_history()
            frame.iloc[5, frame.columns.get_loc("Low")] = frame["High"].iloc[5] * 2
            return frame

    assert isinstance(fetch_prices("ABC", factory=lambda s: Bad(), now=lambda: OPEN), Unavailable)


@pytest.mark.unit
def test_chains_are_stamped_with_quote_time() -> None:
    result = fetch_option_chains("ABC", factory=fake(), now=lambda: OPEN)
    assert isinstance(result, YahooTable)
    assert set(result.table["option_type"]) == {"call", "put"}
    assert (result.table["quote_time"] == pd.Timestamp(OPEN)).all()


@pytest.mark.unit
def test_analyst_views_are_a_long_table() -> None:
    result = fetch_analyst_views("ABC", factory=fake(), now=lambda: OPEN)
    assert isinstance(result, YahooTable)
    items = dict(zip(result.table["item"], result.table["value"], strict=True))
    assert items["target_mean"] == 110.0
    assert items["rating_buy"] == 10.0


@pytest.mark.unit
def test_empty_or_failing_yahoo_tables_return_unavailable() -> None:
    class EmptyPrices(FakeTicker):
        def history(self, **kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    empty_prices = fetch_prices("ABC", factory=lambda s: EmptyPrices(), now=lambda: OPEN)
    assert isinstance(empty_prices, Unavailable)
    assert "no rows" in empty_prices.reason

    class NoExpiries(FakeTicker):
        def __init__(self) -> None:
            super().__init__()
            self.options = ()

    no_exp = fetch_option_chains("ABC", factory=lambda s: NoExpiries(), now=lambda: OPEN)
    assert isinstance(no_exp, Unavailable)
    assert "no listed expiries" in no_exp.reason

    class BrokenChains(FakeTicker):
        def option_chain(self, date: str) -> Any:
            raise ConnectionError("chain endpoint down")

    bad_chain = fetch_option_chains("ABC", factory=lambda s: BrokenChains(), now=lambda: OPEN)
    assert isinstance(bad_chain, Unavailable)
    assert "chain endpoint down" in bad_chain.reason

    class BrokenAnalysts(FakeTicker):
        def get_analyst_price_targets(self) -> dict[str, Any]:
            raise ConnectionError("analyst endpoint down")

    bad_analysts = fetch_analyst_views("ABC", factory=lambda s: BrokenAnalysts(), now=lambda: OPEN)
    assert isinstance(bad_analysts, Unavailable)
    assert "analyst endpoint down" in bad_analysts.reason

    class TargetsOnly(FakeTicker):
        def get_recommendations_summary(self) -> pd.DataFrame:
            return pd.DataFrame()

    targets_only = fetch_analyst_views("ABC", factory=lambda s: TargetsOnly(), now=lambda: OPEN)
    assert isinstance(targets_only, YahooTable)
    assert "target_mean" in set(targets_only.table["item"])

    class EmptyAnalysts(FakeTicker):
        def get_analyst_price_targets(self) -> dict[str, Any]:
            return {}

        def get_recommendations_summary(self) -> pd.DataFrame:
            return pd.DataFrame()

    empty_analysts = fetch_analyst_views("ABC", factory=lambda s: EmptyAnalysts(), now=lambda: OPEN)
    assert isinstance(empty_analysts, Unavailable)
    assert "no rows" in empty_analysts.reason


# --- snapshot ----------------------------------------------------------------------------


def fetchers(**kwargs: Any) -> dict[str, Any]:
    return {
        "prices": lambda s, now: fetch_prices(s, factory=fake(**kwargs), now=now),
        "chains": lambda s, now: fetch_option_chains(s, factory=fake(**kwargs), now=now),
    }


@pytest.mark.unit
def test_market_hours() -> None:
    assert market_is_open(OPEN)
    assert not market_is_open(CLOSED)
    assert not market_is_open(datetime(2026, 11, 26, 16, 0, tzinfo=UTC))  # Thanksgiving


@pytest.mark.unit
def test_snapshot_refuses_to_run_when_the_market_is_closed(tmp_path: Path) -> None:
    with pytest.raises(MarketClosedError):
        run_snapshot(["ABC"], tmp_path, now=lambda: CLOSED, fetchers=fetchers())


@pytest.mark.unit
def test_snapshot_writes_tables_and_a_health_line(tmp_path: Path) -> None:
    report = run_snapshot(["ABC", "XYZ"], tmp_path, now=lambda: OPEN, fetchers=fetchers())
    assert report.day == "2026-11-11"
    assert sorted(report.written) == [
        "ABC_chains.parquet",
        "ABC_prices.parquet",
        "XYZ_chains.parquet",
        "XYZ_prices.parquet",
    ]
    assert not report.alarm
    health = (tmp_path / "snapshots" / "health.jsonl").read_text().splitlines()
    assert json.loads(health[-1])["failure_rate"] == 0.0


@pytest.mark.unit
def test_snapshot_raises_the_alarm_above_one_in_ten(tmp_path: Path) -> None:
    report = run_snapshot(["ABC"], tmp_path, now=lambda: OPEN, fetchers=fetchers(fail=True))
    assert report.failure_rate == 0.5
    assert report.alarm

    tickers = [f"T{i:02d}" for i in range(10)]
    one_in_ten = {
        "prices": lambda s, now: fetch_prices(s, factory=fake(fail=(s == "T00")), now=now)
    }
    at_threshold = run_snapshot(tickers, tmp_path / "at", now=lambda: OPEN, fetchers=one_in_ten)
    assert at_threshold.failure_rate == pytest.approx(0.10)
    assert not at_threshold.alarm


@pytest.mark.unit
def test_cli_snapshot_and_run_exit_2_when_market_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    data_dir = tmp_path / "data"
    config = tmp_path / "foxcast.toml"
    config.write_text(f'data_dir = "{data_dir.as_posix()}"\n', encoding="utf-8")
    watchlist = draw_watchlist(
        ["ABC"], size=1, seed=1, drawn_on=date(2026, 11, 11), universe_source="test"
    )
    save_watchlist(watchlist, data_dir / "watchlist.json")

    def closed_snapshot(
        symbols: Any,
        target_dir: Path,
        *,
        require_market_open: bool = True,
    ) -> Any:
        return run_snapshot(
            symbols,
            target_dir,
            now=lambda: CLOSED,
            fetchers=fetchers(),
            require_market_open=require_market_open,
        )

    monkeypatch.setattr("foxcast.snapshot.run_snapshot", closed_snapshot)

    snap_closed = runner.invoke(cli_app.app, ["snapshot", "--config", str(config)])
    assert snap_closed.exit_code == 2
    assert "XNYS is closed" in snap_closed.output
    assert "nothing captured" in snap_closed.output
    assert not (data_dir / "snapshots").exists()

    run_closed = runner.invoke(cli_app.app, ["run", "--config", str(config)])
    assert run_closed.exit_code == 2
    assert "XNYS is closed" in run_closed.output
    assert "nothing captured" in run_closed.output
    assert not (data_dir / "snapshots").exists()
    assert not (data_dir / "ledger.jsonl").exists()

    snap_forced = runner.invoke(cli_app.app, ["snapshot", "--force", "--config", str(config)])
    assert snap_forced.exit_code == 0, snap_forced.output
    assert "2 tables written" in snap_forced.output
    assert (data_dir / "snapshots" / "2026-11-14" / "ABC_prices.parquet").exists()


# --- watchlist -------------------------------------------------------------------------


@pytest.mark.unit
def test_watchlist_draw_is_seeded_and_recorded(tmp_path: Path) -> None:
    universe = [f"T{i:03d}" for i in range(500)]
    first = draw_watchlist(
        universe, size=10, seed=42, drawn_on=date(2026, 11, 11), universe_source="test"
    )
    again = draw_watchlist(
        universe, size=10, seed=42, drawn_on=date(2026, 11, 11), universe_source="test"
    )
    other = draw_watchlist(
        universe, size=10, seed=43, drawn_on=date(2026, 11, 11), universe_source="test"
    )
    assert first == again
    assert first.tickers != other.tickers
    save_watchlist(first, tmp_path / "w.json")
    assert load_watchlist(tmp_path / "w.json") == first
    with pytest.raises(ValueError):
        draw_watchlist(
            universe[:3], size=10, seed=1, drawn_on=date(2026, 11, 11), universe_source="t"
        )


# --- daily forecasts ---------------------------------------------------------------------


@pytest.mark.unit
def test_horizon_date_counts_trading_days() -> None:
    assert horizon_date(date(2026, 11, 11), 5) == date(2026, 11, 18)
    assert horizon_date(date(2026, 11, 20), 5) == date(2026, 11, 30)  # skips Thanksgiving


@pytest.mark.unit
def test_seeds_differ_across_tickers_and_repeat_for_the_same_request() -> None:
    assert seed_for("2026-11-11", "ABC", "naive", 21) == seed_for("2026-11-11", "ABC", "naive", 21)
    assert seed_for("2026-11-11", "ABC", "naive", 21) != seed_for("2026-11-11", "XYZ", "naive", 21)


@pytest.mark.unit
def test_daily_forecasts_freeze_once_and_rerun_safely(tmp_path: Path) -> None:
    run_snapshot(["ABC"], tmp_path, now=lambda: OPEN, fetchers=fetchers())
    ledger = Ledger(tmp_path / "ledger.jsonl")
    result = run_daily_forecasts(["ABC"], tmp_path, ledger, day="2026-11-11", project=tmp_path)
    assert len(result.frozen) == 5  # naive and plain_vol at 5 and 21 days, option_raw
    again = run_daily_forecasts(["ABC"], tmp_path, ledger, day="2026-11-11", project=tmp_path)
    assert again.frozen == result.frozen
    assert len(ledger.records()) == 5
    assert ledger.verify().ok
    payload = ledger.records()[0].payload
    assert payload["resolution_rule"]["void"].startswith("Void only when no price exists")
    assert len(payload["forecast"]["values"]) == 101


@pytest.mark.unit
def test_daily_forecasts_skip_missing_snapshots_with_a_reason(tmp_path: Path) -> None:
    result = run_daily_forecasts(
        ["NOPE"], tmp_path, Ledger(tmp_path / "l.jsonl"), day="2026-11-11", project=tmp_path
    )
    assert result.frozen == ()
    assert "no price snapshot" in result.skipped[0]


@pytest.mark.unit
def test_option_forecast_is_labelled_in_trading_days(tmp_path: Path) -> None:
    run_snapshot(["ABC"], tmp_path, now=lambda: OPEN, fetchers=fetchers())
    ledger = Ledger(tmp_path / "ledger.jsonl")
    result = run_daily_forecasts(["ABC"], tmp_path, ledger, day="2026-11-11", project=tmp_path)
    assert "2026-11-11:ABC:option_raw:21" in result.frozen


@pytest.mark.unit
def test_code_version_reports_dirty_or_falls_back_without_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "foxcast.daily._git",
        lambda project, *args: "deadbeef" if args == ("rev-parse", "HEAD") else " M file.py",
    )
    assert code_version(tmp_path) == "deadbeef+dirty"

    def no_git(project: Path, *args: str) -> str:
        raise OSError("git not installed")

    monkeypatch.setattr("foxcast.daily._git", no_git)
    assert code_version(tmp_path).startswith("package ")
    assert "git unknown" in code_version(tmp_path)


@pytest.mark.unit
def test_daily_forecasts_skip_short_history_and_missing_or_distant_chains(tmp_path: Path) -> None:
    short_fetchers = {
        "prices": lambda s, now: fetch_prices(
            s,
            factory=lambda sym: SimpleNamespace(history=lambda **kw: price_history(days=10)),  # type: ignore[return-value]
            now=now,
        ),
    }
    run_snapshot(["SHORT"], tmp_path, now=lambda: OPEN, fetchers=short_fetchers)
    ledger = Ledger(tmp_path / "ledger.jsonl")
    result = run_daily_forecasts(["SHORT"], tmp_path, ledger, day="2026-11-11", project=tmp_path)
    assert result.frozen == ()
    assert len(result.skipped) == 5
    assert any("no option chain snapshot" in reason for reason in result.skipped)

    distant_chain = {
        "prices": lambda s, now: fetch_prices(
            s,
            factory=lambda sym: SimpleNamespace(history=lambda **kw: price_history(days=10)),  # type: ignore[return-value]
            now=now,
        ),
        "chains": lambda s, now: fetch_option_chains(s, factory=fake(expiry_days=90), now=now),
    }
    run_snapshot(["FAR"], tmp_path, now=lambda: OPEN, fetchers=distant_chain)
    far_result = run_daily_forecasts(["FAR"], tmp_path, ledger, day="2026-11-11", project=tmp_path)
    assert far_result.frozen == ()
    assert any("FAR option_raw: no option expiry" in reason for reason in far_result.skipped)
