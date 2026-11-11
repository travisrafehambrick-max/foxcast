"""The foxcast command line."""

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
    universe: Annotated[Path, typer.Option("--universe", help="File with one ticker per line.")],
    seed: Annotated[int, typer.Option("--seed", help="Recorded seed for the random draw.")],
    size: Annotated[int | None, typer.Option("--size", help="Defaults to watchlist_size.")] = None,
    config: ConfigOption = DEFAULT_CONFIG,
) -> None:
    """Draw the watchlist at random from a universe file, with a recorded seed."""
    from datetime import date

    from foxcast.watchlist import draw_watchlist, save_watchlist

    settings = _settings(config)
    _, watchlist_path, _ = _paths(settings)
    members = universe.read_text(encoding="utf-8").splitlines()
    drawn = draw_watchlist(
        members,
        size=size or settings.watchlist_size,
        seed=seed,
        drawn_on=date.today(),
        universe_source=universe.name,
    )
    save_watchlist(drawn, watchlist_path)
    console.print(
        f"Drew {drawn.size} of {drawn.universe_size} (seed {seed}): {', '.join(drawn.tickers)}"
    )


@app.command()
def snapshot(
    config: ConfigOption = DEFAULT_CONFIG,
    force: Annotated[
        bool, typer.Option("--force", help="Run even if the market is closed.")
    ] = False,
) -> None:
    """Capture today's prices, option chains and analyst views for the watchlist."""
    from foxcast.snapshot import MarketClosedError, run_snapshot
    from foxcast.watchlist import load_watchlist

    data, watchlist_path, _ = _paths(_settings(config))
    if not watchlist_path.exists():
        console.print("[red]No watchlist yet; run `foxcast watchlist` first.[/red]")
        raise typer.Exit(code=1)
    tickers = load_watchlist(watchlist_path).tickers
    try:
        report = run_snapshot(tickers, data, require_market_open=not force)
    except MarketClosedError as exc:
        console.print(f"[yellow]{exc}; nothing captured.[/yellow]")
        raise typer.Exit(code=2) from exc
    style = "red" if report.alarm else "green"
    console.print(
        f"[{style}]{report.day}: {len(report.written)} tables written, "
        f"{len(report.failures)} failures ({report.failure_rate:.0%})[/{style}]"
    )
    for failure in report.failures:
        console.print(f"  {failure}")
    if report.alarm:
        raise typer.Exit(code=1)


@app.command()
def forecast(
    numbers_only: Annotated[
        bool, typer.Option("--numbers-only", help="Forecasts 1, 2 and 4; no model.")
    ] = True,
    day: Annotated[str | None, typer.Option("--day", help="Snapshot day, YYYY-MM-DD.")] = None,
    config: ConfigOption = DEFAULT_CONFIG,
) -> None:
    """Make the numbers-only forecasts from a snapshot and freeze them in the ledger."""
    from datetime import date

    from foxcast.daily import run_daily_forecasts
    from foxcast.watchlist import load_watchlist

    if not numbers_only:
        console.print("[red]Only --numbers-only forecasts exist before Stage 3.[/red]")
        raise typer.Exit(code=1)
    data, watchlist_path, ledger_path = _paths(_settings(config))
    tickers = load_watchlist(watchlist_path).tickers
    result = run_daily_forecasts(
        tickers,
        data,
        Ledger(ledger_path),
        day=day or date.today().isoformat(),
        project=Path(__file__).resolve().parents[3],
    )
    console.print(f"Frozen: {len(result.frozen)}. Skipped: {len(result.skipped)}.")
    for reason in result.skipped:
        console.print(f"  {reason}")


@app.command()
def daily(config: ConfigOption = DEFAULT_CONFIG) -> None:
    """What the scheduler runs: the snapshot, then the numbers-only forecasts."""
    snapshot(config=config, force=False)
    forecast(numbers_only=True, day=None, config=config)


@app.command()
def schedule(
    start_time: Annotated[str, typer.Option("--time", help="Local start time, HH:MM.")] = "11:00",
    apply: Annotated[
        bool, typer.Option("--apply", help="Register it (changes this machine).")
    ] = False,
) -> None:
    """Show, or with --apply register, the weekday job in the system's own scheduler."""
    import subprocess

    from foxcast.schedule import scheduler_command

    project = Path(__file__).resolve().parents[3]
    command = scheduler_command(project, start_time)
    console.print("Scheduler command:")
    console.print("  " + " ".join(command))
    if not apply:
        console.print("Not registered. Re-run with --apply to register it.")
        return
    subprocess.run(command, check=True)  # noqa: S603 (fixed argument list, no shell input)
    console.print("[green]Registered.[/green]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
