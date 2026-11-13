"""Stage 0.5 CLI paths: watchlist, follow/unfollow, snapshot, forecast, run & repair-ledger."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from foxcast.cli import app as cli_app
from foxcast.ledger.chain import Ledger
from foxcast.snapshot import run_snapshot
from foxcast.sources.yahoo import fetch_option_chains, fetch_prices
from foxcast.watchlist import load_watchlist
from tests.test_stage05 import OPEN, FakeTicker, fetchers

runner = CliRunner()


@pytest.mark.unit
def test_verify_and_repair_ledger_cli(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = Ledger(ledger_path)
    ledger.append(kind="forecast", request_id="r1", payload={"x": 1})
    result = runner.invoke(cli_app.app, ["verify-ledger", "--ledger", str(ledger_path)])
    assert result.exit_code == 0, result.output
    assert "1 record." in result.output

    ledger.append(kind="forecast", request_id="r2", payload={"x": 2})
    result_two = runner.invoke(cli_app.app, ["verify-ledger", "--ledger", str(ledger_path)])
    assert result_two.exit_code == 0, result_two.output
    assert "2 records." in result_two.output

    # Repair refuses a clean ledger with no torn tail.
    no_tear = runner.invoke(cli_app.app, ["repair-ledger", "--ledger", str(ledger_path)])
    assert no_tear.exit_code == 1
    assert "no torn last line" in no_tear.output

    # Simulate an interrupted write (torn last line) and repair via CLI.
    original_text = ledger_path.read_text(encoding="utf-8")
    torn_text = original_text[:-12]
    ledger_path.write_text(torn_text, encoding="utf-8")

    broken = runner.invoke(cli_app.app, ["verify-ledger", "--ledger", str(ledger_path)])
    assert broken.exit_code == 1
    assert "torn" in broken.output

    repaired = runner.invoke(cli_app.app, ["repair-ledger", "--ledger", str(ledger_path)])
    assert repaired.exit_code == 0, repaired.output
    assert "Repaired." in repaired.output
    backups = list((tmp_path / "ledger-backups").glob("ledger-*.jsonl"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == torn_text

    verified_after = runner.invoke(cli_app.app, ["verify-ledger", "--ledger", str(ledger_path)])
    assert verified_after.exit_code == 0, verified_after.output
    assert "1 record." in verified_after.output

    # Tampering with an in-place record (still newline-terminated) breaks verify
    # and is refused by repair.
    ledger_path.write_text(
        ledger_path.read_text(encoding="utf-8").replace('"x":1', '"x":9'), encoding="utf-8"
    )
    tampered = runner.invoke(cli_app.app, ["verify-ledger", "--ledger", str(ledger_path)])
    assert tampered.exit_code == 1
    assert "changed" in tampered.output
    refused = runner.invoke(cli_app.app, ["repair-ledger", "--ledger", str(ledger_path)])
    assert refused.exit_code == 1
    assert "no torn last line" in refused.output


@pytest.mark.unit
def test_watchlist_plain_line_universe_and_cli_error_paths(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    config = tmp_path / "foxcast.toml"
    config.write_text(f'data_dir = "{data_dir.as_posix()}"\nwatchlist_size = 2\n', encoding="utf-8")
    universe = tmp_path / "tickers.txt"
    universe.write_text("AAPL\nMSFT\nGOOGL\n", encoding="utf-8")

    # Follow and snapshot before watchlist exists fail with exit code 1.
    no_wl_follow = runner.invoke(cli_app.app, ["follow", "FRMI", "--config", str(config)])
    assert no_wl_follow.exit_code == 1
    assert "No watchlist yet" in no_wl_follow.output

    no_wl_snap = runner.invoke(cli_app.app, ["snapshot", "--config", str(config)])
    assert no_wl_snap.exit_code == 1
    assert "No watchlist yet" in no_wl_snap.output

    # Plain line-per-ticker universe uses default watchlist_size from config.
    drawn = runner.invoke(
        cli_app.app,
        ["watchlist", "--universe", str(universe), "--seed", "42", "--config", str(config)],
    )
    assert drawn.exit_code == 0, drawn.output
    assert "Universe: every ticker in tickers.txt (3 tickers)." in drawn.output
    assert "Drew 2 of 3 (seed 42):" in drawn.output
    saved = load_watchlist(data_dir / "watchlist.json")
    assert saved.size == 2
    assert saved.universe_size == 3

    # Unknown sector in CSV universe fails with exit code 1.
    csv_universe = tmp_path / "constituents.csv"
    csv_universe.write_text(
        "Symbol,GICS Sector,GICS Sub-Industry\nAAPL,Information Technology,Hardware\n",
        encoding="utf-8",
    )
    bad_sector = runner.invoke(
        cli_app.app,
        [
            "watchlist",
            "--universe",
            str(csv_universe),
            "--seed",
            "1",
            "--sector",
            "Nonexistent Sector",
            "--config",
            str(config),
        ],
    )
    assert bad_sector.exit_code == 1
    assert "not in the source list" in bad_sector.output

    # Leaked key in config file fails with exit code 1.
    bad_config = tmp_path / "bad.toml"
    bad_config.write_text('fred_api_key = "SECRET"\n', encoding="utf-8")
    leaked = runner.invoke(
        cli_app.app,
        ["watchlist", "--universe", str(universe), "--seed", "1", "--config", str(bad_config)],
    )
    assert leaked.exit_code == 1
    assert "environment" in leaked.output


@pytest.mark.unit
def test_cli_snapshot_forecast_and_run_happy_and_alarm_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    config = tmp_path / "foxcast.toml"
    config.write_text(f'data_dir = "{data_dir.as_posix()}"\nwatchlist_size = 1\n', encoding="utf-8")
    universe = tmp_path / "tickers.txt"
    universe.write_text("ABC\n", encoding="utf-8")
    assert (
        runner.invoke(
            cli_app.app,
            ["watchlist", "--universe", str(universe), "--seed", "1", "--config", str(config)],
        ).exit_code
        == 0
    )

    def open_snapshot(
        symbols: Any,
        target_dir: Path,
        *,
        require_market_open: bool = True,
    ) -> Any:
        return run_snapshot(
            symbols,
            target_dir,
            now=lambda: OPEN,
            fetchers=fetchers(),
            require_market_open=require_market_open,
        )

    monkeypatch.setattr("foxcast.snapshot.run_snapshot", open_snapshot)

    # First snapshot writes tables; second snapshot on the same day keeps existing tables.
    snap1 = runner.invoke(cli_app.app, ["snapshot", "--config", str(config)])
    assert snap1.exit_code == 0, snap1.output
    assert "2026-11-11: 2 tables written, 0 kept" in snap1.output

    snap2 = runner.invoke(cli_app.app, ["snapshot", "--config", str(config)])
    assert snap2.exit_code == 0, snap2.output
    assert "0 tables written, 2 kept" in snap2.output

    # Forecast validation flags: numbers_only=False and invalid --day.
    with pytest.raises(typer.Exit) as exc_info:
        cli_app.forecast(numbers_only=False, day="2026-11-11", config=config)
    assert exc_info.value.exit_code == 1

    bad_day = runner.invoke(
        cli_app.app, ["forecast", "--day", "not-a-date", "--config", str(config)]
    )
    assert bad_day.exit_code == 1
    assert "YYYY-MM-DD" in bad_day.output

    # Forecast happy path with default day (via _new_york_today).
    assert date.fromisoformat(cli_app._new_york_today())
    monkeypatch.setattr(cli_app, "_new_york_today", lambda: "2026-11-11")
    fc = runner.invoke(cli_app.app, ["forecast", "--config", str(config)])
    assert fc.exit_code == 0, fc.output
    assert "2026-11-11: frozen 5, skipped 0." in fc.output

    # On-demand `foxcast run` happy path (snapshot + forecast in one command).
    run_ok = runner.invoke(cli_app.app, ["run", "--config", str(config)])
    assert run_ok.exit_code == 0, run_ok.output
    assert "2026-11-11: frozen 5, skipped 0." in run_ok.output

    # When a followed ticker fails in snapshot, alarm is raised (failure rate > 10%):
    # `foxcast snapshot` exits 1, and `foxcast run` still makes forecasts for captured
    # tickers before exiting 1.
    assert runner.invoke(cli_app.app, ["follow", "BAD", "--config", str(config)]).exit_code == 0

    def alarm_snapshot(
        symbols: Any,
        target_dir: Path,
        *,
        require_market_open: bool = True,
    ) -> Any:
        mixed_fetchers = {
            "prices": lambda s, now: fetch_prices(
                s, factory=lambda sym: FakeTicker(fail=(sym == "BAD")), now=now
            ),
            "chains": lambda s, now: fetch_option_chains(
                s, factory=lambda sym: FakeTicker(fail=False), now=now
            ),
        }
        return run_snapshot(
            symbols,
            target_dir,
            now=lambda: OPEN,
            fetchers=mixed_fetchers,
            require_market_open=require_market_open,
        )

    monkeypatch.setattr("foxcast.snapshot.run_snapshot", alarm_snapshot)

    snap_alarm = runner.invoke(cli_app.app, ["snapshot", "--config", str(config)])
    assert snap_alarm.exit_code == 1
    assert "yahoo down" in snap_alarm.output

    run_alarm = runner.invoke(cli_app.app, ["run", "--config", str(config)])
    assert run_alarm.exit_code == 1
    assert "2026-11-11: frozen 5, skipped 1." in run_alarm.output
    assert "BAD: no price snapshot for 2026-11-11" in run_alarm.output
