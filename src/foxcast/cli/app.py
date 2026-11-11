"""The foxcast command line."""

from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console

from foxcast.config import default_config_path, default_home, write_default_config
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


def main() -> None:
    app()
