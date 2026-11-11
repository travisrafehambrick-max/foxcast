"""The foxcast command line."""

from dataclasses import replace
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console

from foxcast.config import (
    Settings,
    default_config_path,
    default_home,
    load_settings,
    write_default_config,
)
from foxcast.doctor import Status, run_checks
from foxcast.ledger.chain import Ledger
from foxcast.notice import NOTICE_HEADLINE
from foxcast.snapshot import SnapshotReport

app = typer.Typer(
    help=f"foxcast: sourced stock research and scored forecasts. {NOTICE_HEADLINE}",
    no_args_is_help=True,
)
console = Console()

DEFAULT_CONFIG = default_config_path()
STATUS_STYLE = {Status.PASS: "green", Status.WARN: "yellow", Status.FAIL: "red"}

ConfigOption = Annotated[Path, typer.Option("--config", help="Path to foxcast.toml.")]


@app.command()
def init(
    config: ConfigOption = DEFAULT_CONFIG,
    data_dir: Annotated[
        Path | None, typer.Option("--data-dir", help="Where fetched data is stored.")
    ] = None,
) -> None:
    """Write a starter config file. Keys are set as environment variables, not here."""
    try:
        write_default_config(config, data_dir=data_dir or default_home() / "data")
    except FileExistsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"Wrote {config}. Next: set FOXCAST_SEC_CONTACT, then run `foxcast doctor`.")


@app.command()
def doctor(config: ConfigOption = DEFAULT_CONFIG) -> None:
    """Check Python, config, keys, disk space and data sources before any run."""
    with httpx.Client() as client:
        results = run_checks(config, client)
    for result in results:
        style = STATUS_STYLE[result.status]
        console.print(
            f"[{style}]{result.status.value:>4}[/{style}]  {result.name}: {result.detail}"
        )
    if any(result.status is Status.FAIL for result in results):
        raise typer.Exit(code=1)


@app.command("verify-ledger")
def verify_ledger(
    ledger: Annotated[Path, typer.Option("--ledger", help="Path to the ledger file.")],
) -> None:
    """Check the hash chain: any edit, deletion or reordering is reported."""
    result = Ledger(ledger).verify()
    if not result.ok:
        console.print(f"[red]Ledger broken:[/red] {result.reason}")
        raise typer.Exit(code=1)
    plural = "" if result.records_checked == 1 else "s"
    console.print(f"[green]Ledger verifies:[/green] {result.records_checked} record{plural}.")


def _settings(config: Path) -> Settings:
    try:
        return load_settings(config)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


def _paths(settings: Settings) -> tuple[Path, Path, Path]:
    data = settings.data_dir
    return data, data / "watchlist.json", data / "ledger.jsonl"


@app.command("watchlist")
def watchlist_draw(
    universe: Annotated[
        Path,
        typer.Option(
            "--universe",
            help="S&P 500 constituents CSV (with GICS columns), or one ticker per line.",
        ),
    ],
    seed: Annotated[int, typer.Option("--seed", help="Recorded seed for the random draw.")],
    size: Annotated[int | None, typer.Option("--size", help="Defaults to watchlist_size.")] = None,
    sector: Annotated[
        list[str] | None, typer.Option("--sector", help="GICS sector to include (repeatable).")
    ] = None,
    sub_industry: Annotated[
        list[str] | None,
        typer.Option("--sub-industry", help="GICS sub-industry to include (repeatable)."),
    ] = None,
    config: ConfigOption = DEFAULT_CONFIG,
) -> None:
    """Draw the scored watchlist at random from a declared universe, with a recorded seed.

    Followed tickers on an existing watchlist are kept.
    """
    from datetime import date

    from foxcast.universe import UniverseSpec, select_universe
    from foxcast.watchlist import draw_watchlist, load_watchlist, save_watchlist

    settings = _settings(config)
    _, watchlist_path, _ = _paths(settings)
    text = universe.read_text(encoding="utf-8")
    if sector or sub_industry:
        spec = UniverseSpec(sectors=tuple(sector or ()), sub_industries=tuple(sub_industry or ()))
        try:
            members = select_universe(text, spec)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        definition = spec.describe()
    else:
        members = text.splitlines()
        definition = f"every ticker in {universe.name}"
    drawn = draw_watchlist(
        members,
        size=size or settings.watchlist_size,
        seed=seed,
        drawn_on=date.today(),
        universe_source=universe.name,
        universe_definition=definition,
    )
    if watchlist_path.exists():
        kept = tuple(t for t in load_watchlist(watchlist_path).followed if t not in drawn.tickers)
        drawn = replace(drawn, followed=kept)
    save_watchlist(drawn, watchlist_path)
    console.print(f"Universe: {definition} ({drawn.universe_size} tickers).")
    console.print(
        f"Drew {drawn.size} of {drawn.universe_size} (seed {seed}): {', '.join(drawn.tickers)}"
    )
    if drawn.followed:
        console.print(f"Still followed (not scored): {', '.join(drawn.followed)}")


def _edit_followed(config: Path, ticker: str, *, add: bool) -> None:
    from foxcast.watchlist import follow as add_followed
    from foxcast.watchlist import load_watchlist, save_watchlist, unfollow

    _, watchlist_path, _ = _paths(_settings(config))
    if not watchlist_path.exists():
        console.print("[red]No watchlist yet; run `foxcast watchlist` first.[/red]")
        raise typer.Exit(code=1)
    current = load_watchlist(watchlist_path)
    try:
        updated = add_followed(current, ticker) if add else unfollow(current, ticker)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    save_watchlist(updated, watchlist_path)
    shown = ", ".join(updated.followed) or "none"
    console.print(f"Followed (hand-picked, excluded from the scorecard): {shown}")


@app.command("follow")
def follow_ticker(
    ticker: Annotated[str, typer.Argument(help="Ticker to follow, e.g. FRMI.")],
    config: ConfigOption = DEFAULT_CONFIG,
) -> None:
    """Snapshot and forecast a hand-picked ticker, tagged so the scorecard leaves it out."""
    _edit_followed(config, ticker, add=True)


@app.command("unfollow")
def unfollow_ticker(
    ticker: Annotated[str, typer.Argument(help="Ticker to stop following.")],
    config: ConfigOption = DEFAULT_CONFIG,
) -> None:
    """Stop snapshotting and forecasting a followed ticker (its frozen records stay)."""
    _edit_followed(config, ticker, add=False)


def _new_york_today() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _take_snapshot(config: Path, force: bool) -> SnapshotReport:
    from foxcast.snapshot import MarketClosedError, run_snapshot
    from foxcast.watchlist import load_watchlist

    data, watchlist_path, _ = _paths(_settings(config))
    if not watchlist_path.exists():
        console.print("[red]No watchlist yet; run `foxcast watchlist` first.[/red]")
        raise typer.Exit(code=1)
    tickers = load_watchlist(watchlist_path).all_tickers
    try:
        report = run_snapshot(tickers, data, require_market_open=not force)
    except MarketClosedError as exc:
        console.print(f"[yellow]{exc}; nothing captured.[/yellow]")
        raise typer.Exit(code=2) from exc
    style = "red" if report.alarm else "green"
    console.print(
        f"[{style}]{report.day}: {len(report.written)} tables written, {len(report.kept)} kept "
        f"from an earlier run today, {len(report.failures)} failures "
        f"({report.failure_rate:.0%})[/{style}]"
    )
    for failure in report.failures:
        console.print(f"  {failure}")
    return report


@app.command()
def snapshot(
    config: ConfigOption = DEFAULT_CONFIG,
    force: Annotated[
        bool, typer.Option("--force", help="Run even if the market is closed.")
    ] = False,
) -> None:
    """Capture today's prices, option chains and analyst views for the watchlist."""
    if _take_snapshot(config, force).alarm:
        raise typer.Exit(code=1)


def _make_forecasts(config: Path, day: str) -> None:
    from datetime import date

    from foxcast.daily import run_daily_forecasts
    from foxcast.watchlist import load_watchlist

    try:
        date.fromisoformat(day)
    except ValueError as exc:
        console.print(f"[red]--day must be YYYY-MM-DD, got {day!r}[/red]")
        raise typer.Exit(code=1) from exc
    data, watchlist_path, ledger_path = _paths(_settings(config))
    watchlist = load_watchlist(watchlist_path)
    result = run_daily_forecasts(
        watchlist.all_tickers,
        data,
        Ledger(ledger_path),
        day=day,
        project=Path(__file__).resolve().parents[3],
        followed=watchlist.followed,
    )
    console.print(f"{day}: frozen {len(result.frozen)}, skipped {len(result.skipped)}.")
    for reason in result.skipped:
        console.print(f"  {reason}")


@app.command()
def forecast(
    numbers_only: Annotated[
        bool, typer.Option("--numbers-only", help="Forecasts 1, 2 and 4; no model.")
    ] = True,
    day: Annotated[
        str | None, typer.Option("--day", help="Snapshot day (New York date), YYYY-MM-DD.")
    ] = None,
    config: ConfigOption = DEFAULT_CONFIG,
) -> None:
    """Make the numbers-only forecasts from a snapshot and freeze them in the ledger."""
    if not numbers_only:
        console.print("[red]Only --numbers-only forecasts exist before Stage 3.[/red]")
        raise typer.Exit(code=1)
    _make_forecasts(config, day or _new_york_today())


@app.command()
def daily(config: ConfigOption = DEFAULT_CONFIG) -> None:
    """What the scheduler runs: the snapshot, then the numbers-only forecasts for that day.

    Forecasts are made even when the snapshot raised the alarm, for every ticker that was
    captured; the command still exits with an error so the alarm is not missed.
    """
    report = _take_snapshot(config, force=False)
    _make_forecasts(config, report.day)
    if report.alarm:
        raise typer.Exit(code=1)


@app.command("repair-ledger")
def repair_ledger(
    ledger: Annotated[Path, typer.Option("--ledger", help="Path to the ledger file.")],
) -> None:
    """Cut off a torn last line left by an interrupted write, after a backup."""
    target = Ledger(ledger)
    try:
        backup = target.repair_torn_tail(ledger.parent / "ledger-backups")
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Repaired.[/green] The original is kept at {backup}.")


@app.command()
def schedule(
    start_time: Annotated[
        str, typer.Option("--time", help="Start time in New York, HH:MM (market hours).")
    ] = "10:30",
    apply: Annotated[
        bool, typer.Option("--apply", help="Register it (changes this machine).")
    ] = False,
) -> None:
    """Show, or with --apply register, the weekday job in the system's own scheduler."""
    from foxcast.schedule import apply as apply_schedule
    from foxcast.schedule import describe

    project = Path(__file__).resolve().parents[3]
    try:
        plan = describe(project, start_time)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"Would run: {plan}")
    if not apply:
        console.print("Not registered. Re-run with --apply to register it.")
        return
    console.print(f"[green]{apply_schedule(project, start_time)}[/green]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
