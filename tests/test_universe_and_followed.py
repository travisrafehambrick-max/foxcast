"""Sector-defined universe, recorded random draw, and hand-picked followed tickers."""

import hashlib
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from foxcast.cli import app as cli_app
from foxcast.daily import run_daily_forecasts
from foxcast.ledger.chain import Ledger
from foxcast.snapshot import run_snapshot
from foxcast.universe import UniverseSpec, select_universe, to_yahoo_symbol
from foxcast.watchlist import (
    draw_watchlist,
    follow,
    load_watchlist,
    save_watchlist,
    unfollow,
)
from tests.test_stage05 import OPEN, fetchers

CSV = (
    "Symbol,Security,GICS Sector,GICS Sub-Industry,Headquarters Location,Date added,CIK,Founded\n"
    "AAPL,Apple,Information Technology,Technology Hardware,x,1982-11-30,320193,1977\n"
    "BRK.B,Berkshire,Financials,Multi-Sector Holdings,x,2010-02-16,1067983,1839\n"
    "XOM,Exxon,Energy,Integrated Oil & Gas,x,1957-03-04,34088,1999\n"
    "LMT,Lockheed,Industrials,Aerospace & Defense,x,1984-07-31,936468,1995\n"
    "MMM,3M,Industrials,Industrial Conglomerates,x,1957-03-04,66740,1902\n"
    "KO,Coca-Cola,Consumer Staples,Soft Drinks,x,1957-03-04,21344,1886\n"
    "GOOGL,Alphabet,Communication Services,Interactive Media,x,2014-04-03,1652044,1998\n"
)
SPEC = UniverseSpec(
    sectors=("Communication Services", "Energy", "Financials", "Information Technology"),
    sub_industries=("Aerospace & Defense",),
)


@pytest.mark.unit
def test_universe_selects_declared_sectors_and_sub_industries() -> None:
    selected = select_universe(CSV, SPEC)
    assert selected == ["AAPL", "BRK-B", "GOOGL", "LMT", "XOM"]


@pytest.mark.unit
def test_yahoo_symbols_use_dashes_for_share_classes() -> None:
    assert to_yahoo_symbol("BRK.B") == "BRK-B"
    assert to_yahoo_symbol(" bf.b ") == "BF-B"


@pytest.mark.unit
def test_unknown_sector_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="not in the source"):
        select_universe(CSV, UniverseSpec(sectors=("Informaton Technology",), sub_industries=()))


@pytest.mark.unit
def test_draw_records_the_universe_definition_and_hash() -> None:
    members = select_universe(CSV, SPEC)
    drawn = draw_watchlist(
        members,
        size=3,
        seed=20261111,
        drawn_on=date(2026, 11, 11),
        universe_source="constituents.csv",
        universe_definition=SPEC.describe(),
    )
    assert drawn.universe_definition == SPEC.describe()
    assert drawn.universe_sha256 == hashlib.sha256("\n".join(members).encode()).hexdigest()
    assert drawn.followed == ()


@pytest.mark.unit
def test_follow_and_unfollow_return_new_watchlists(tmp_path: Path) -> None:
    drawn = draw_watchlist(
        ["AAA", "BBB"], size=1, seed=1, drawn_on=date(2026, 11, 11), universe_source="t"
    )
    followed = follow(drawn, "frmi")
    assert followed.followed == ("FRMI",)
    assert drawn.followed == ()
    assert follow(followed, "FRMI") == followed
    with pytest.raises(ValueError, match="already on the random"):
        follow(drawn, drawn.tickers[0])
    assert unfollow(followed, "FRMI").followed == ()
    save_watchlist(followed, tmp_path / "w.json")
    assert load_watchlist(tmp_path / "w.json") == followed


@pytest.mark.unit
def test_old_watchlist_files_still_load(tmp_path: Path) -> None:
    path = tmp_path / "w.json"
    path.write_text(
        '{"tickers": ["AAPL"], "seed": 1, "size": 1, "drawn_on": "2026-11-11",'
        ' "universe_source": "u", "universe_size": 2}'
    )
    loaded = load_watchlist(path)
    assert loaded.followed == ()
    assert loaded.universe_definition == ""


@pytest.mark.unit
def test_followed_forecasts_are_tagged_and_random_ones_are_not(tmp_path: Path) -> None:
    run_snapshot(["ABC", "FOL"], tmp_path, now=lambda: OPEN, fetchers=fetchers())
    ledger = Ledger(tmp_path / "ledger.jsonl")
    run_daily_forecasts(
        ["ABC", "FOL"], tmp_path, ledger, day="2026-11-11", project=tmp_path, followed=("FOL",)
    )
    samples = {r.payload["ticker"]: r.payload["sample"] for r in ledger.records()}
    assert samples == {"ABC": "random", "FOL": "followed"}


runner = CliRunner()


@pytest.mark.unit
def test_cli_watchlist_follow_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOXCAST_DATA_DIR", str(tmp_path / "data"))
    csv_path = tmp_path / "constituents.csv"
    csv_path.write_text(CSV)
    config = str(tmp_path / "none.toml")
    result = runner.invoke(
        cli_app.app,
        [
            "watchlist",
            "--universe",
            str(csv_path),
            "--seed",
            "7",
            "--size",
            "3",
            "--sector",
            "Information Technology",
            "--sector",
            "Financials",
            "--sector",
            "Energy",
            "--sector",
            "Communication Services",
            "--sub-industry",
            "Aerospace & Defense",
            "--config",
            config,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "of 5" in result.output
    result = runner.invoke(cli_app.app, ["follow", "FRMI", "--config", config])
    assert result.exit_code == 0, result.output
    saved = load_watchlist(tmp_path / "data" / "watchlist.json")
    assert saved.followed == ("FRMI",)
    assert len(saved.tickers) == 3
