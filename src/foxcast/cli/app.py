"""The foxcast command line."""

from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any

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


def _require_sec_contact(settings: Settings) -> str:
    from foxcast.doctor import check_sec_contact

    result = check_sec_contact(settings)
    if result.status is Status.FAIL or settings.sec_contact is None:
        console.print(f"[red]{result.detail}[/red]")
        raise typer.Exit(code=1)
    return settings.sec_contact


def _resolve_cik(client: httpx.Client, contact: str, ticker: str) -> int:
    from foxcast.doctor import SEC_PROBE_URL
    from foxcast.sources.tickers import parse_sec_ticker_file
    from foxcast.watchlist import validate_ticker

    symbol = validate_ticker(ticker.strip().upper())
    response = client.get(SEC_PROBE_URL, headers={"User-Agent": contact}, timeout=30.0)
    response.raise_for_status()
    mapping = parse_sec_ticker_file(response.content)
    cik = mapping.get(symbol)
    if cik is None:
        raise ValueError(f"ticker {symbol!r} is not in the SEC company list")
    return cik


@app.command()
def ingest(
    ticker: Annotated[str, typer.Argument(help="US ticker symbol (resolved via SEC).")],
    as_of: Annotated[
        str,
        typer.Option("--as-of", help="Keep only facts public on or before this date (YYYY-MM-DD)."),
    ],
    config: ConfigOption = DEFAULT_CONFIG,
    cik: Annotated[
        int | None,
        typer.Option("--cik", help="SEC CIK; skips the ticker lookup when set."),
    ] = None,
) -> None:
    """Fetch one company's SEC record into the local fact store and source archive."""
    from datetime import date

    from foxcast.sec_ingest import ingest_company
    from foxcast.store.archive import SourceArchive
    from foxcast.store.pit import FactStore

    settings = _settings(config)
    contact = _require_sec_contact(settings)
    try:
        as_of_date = date.fromisoformat(as_of)
    except ValueError as exc:
        console.print(f"[red]--as-of must be YYYY-MM-DD, got {as_of!r}[/red]")
        raise typer.Exit(code=1) from exc

    data = settings.data_dir
    data.mkdir(parents=True, exist_ok=True)
    store = FactStore(data / "facts.duckdb")
    try:
        archive = SourceArchive.outside_git(settings.archive_dir)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    with httpx.Client() as client:
        try:
            resolved = cik if cik is not None else _resolve_cik(client, contact, ticker)
        except (ValueError, httpx.HTTPError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        report = ingest_company(
            resolved,
            as_of=as_of_date,
            client=client,
            contact=contact,
            store=store,
            archive=archive,
        )

    console.print(
        f"[green]CIK {report.cik} as of {report.as_of}:[/green] "
        f"{report.passages} passages, {report.text_facts} text facts, "
        f"{report.numbers} numbers; {len(report.documents)} documents."
    )
    if not report.index_complete:
        console.print("[yellow]Filing index incomplete (older filings may be missing).[/yellow]")
    for reason in report.unavailable:
        console.print(f"  unavailable: {reason}")


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


@app.command("run")
def run_now(config: ConfigOption = DEFAULT_CONFIG) -> None:
    """Pull today's data and freeze today's forecasts, on demand, during market hours.

    Every run covers the whole watchlist (the random draw and every followed ticker), never
    a chosen few, so the scored sample is not shaped by when someone chose to look. Each
    run is logged in snapshots/health.jsonl, which is how the cadence is reported.

    Forecasts are made even when the snapshot raised the alarm, for every ticker that was
    captured; the command still exits with an error so the alarm is not missed.
    """
    report = _take_snapshot(config, force=False)
    _make_forecasts(config, report.day)
    if report.alarm:
        raise typer.Exit(code=1)


@app.command()
def dossier(
    ticker: Annotated[
        str, typer.Argument(help="US ticker symbol (resolved via SEC unless --cik).")
    ],
    as_of: Annotated[
        str,
        typer.Option("--as-of", help="Point-in-time date for the record (YYYY-MM-DD)."),
    ],
    config: ConfigOption = DEFAULT_CONFIG,
    cik: Annotated[
        int | None,
        typer.Option("--cik", help="SEC CIK; skips the ticker lookup when set."),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Folder for the .md and .json files (default: data/dossiers)."),
    ] = None,
    company: Annotated[
        str | None,
        typer.Option("--company", help="Short company name for the header (optional)."),
    ] = None,
    holding: Annotated[
        str,
        typer.Option(
            "--holding",
            help="Operator position: 'holds', 'none', or 'not-declared' (default).",
        ),
    ] = "not-declared",
) -> None:
    """Build a no-model dossier from the local fact store and write Markdown + JSON.

    Research claims and the MCP tool server arrive in Stage 1A.7; this command is the
    Stage 1A.6 path that fills sections from the record and marks the rest unavailable.
    """
    from datetime import UTC, date, datetime

    from foxcast.daily import code_version
    from foxcast.dossier.builder import DossierRequest, build_dossier
    from foxcast.dossier.output import UnverifiedClaimError, write_dossier
    from foxcast.dossier.schema import Holding, SilentGapError, Status
    from foxcast.store.archive import SourceArchive
    from foxcast.store.pit import FactStore
    from foxcast.watchlist import validate_ticker

    holdings = {
        "holds": Holding.HOLDS,
        "none": Holding.NONE,
        "not-declared": Holding.NOT_DECLARED,
    }
    if holding not in holdings:
        console.print(
            f"[red]--holding must be one of {', '.join(sorted(holdings))}, got {holding!r}[/red]"
        )
        raise typer.Exit(code=1)

    settings = _settings(config)
    try:
        symbol = validate_ticker(ticker.strip().upper())
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    try:
        as_of_date = date.fromisoformat(as_of)
    except ValueError as exc:
        console.print(f"[red]--as-of must be YYYY-MM-DD, got {as_of!r}[/red]")
        raise typer.Exit(code=1) from exc

    data = settings.data_dir
    store_path = data / "facts.duckdb"
    if not store_path.exists():
        console.print(
            f"[red]No fact store at {store_path}. Run `foxcast ingest` for this ticker first.[/red]"
        )
        raise typer.Exit(code=1)

    try:
        archive = SourceArchive.outside_git(settings.archive_dir)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if cik is None:
        contact = _require_sec_contact(settings)
        with httpx.Client() as client:
            try:
                resolved = _resolve_cik(client, contact, symbol)
            except (ValueError, httpx.HTTPError) as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1) from exc
    else:
        resolved = cik

    folder = out if out is not None else data / "dossiers"
    # Same project root as forecast: src/foxcast/cli/app.py → repo root is parents[3].
    project = Path(__file__).resolve().parents[3]
    store = FactStore(store_path)
    try:
        request = DossierRequest(
            ticker=symbol,
            cik=resolved,
            as_of=as_of_date,
            built_at=datetime.now(UTC),
            company=company,
            holding=holdings[holding],
            code_commit=code_version(project),
        )
        built = build_dossier(request, store, archive)
        md_path, json_path = write_dossier(built, folder)
    except (ValueError, SilentGapError, UnverifiedClaimError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    filled = sum(1 for s in built.sections if s.status is Status.FILLED)
    unavailable = len(built.sections) - filled
    console.print(
        f"[green]{symbol} as of {as_of_date}:[/green] "
        f"{filled} sections filled, {unavailable} unavailable; "
        f"wrote {md_path} and {json_path}."
    )


def _load_submitted_claims(path: Path) -> tuple[list[Any], dict[str, int]]:
    """Read a claims JSON file: a list, or ``{"claims": [...], "companies": {...}}``."""
    import json

    from foxcast.claims.render import ClaimType, SubmittedClaim

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read claims file {path}: {exc}") from exc

    companies: dict[str, int] = {}
    if isinstance(data, list):
        raw_claims = data
    elif isinstance(data, dict):
        raw = data.get("claims")
        if not isinstance(raw, list):
            raise ValueError("claims file must be a list or an object with a 'claims' list")
        raw_claims = raw
        raw_companies = data.get("companies") or {}
        if not isinstance(raw_companies, dict):
            raise ValueError("claims file 'companies' must be an object of name/ticker → CIK")
        try:
            companies = {str(name): int(cik) for name, cik in raw_companies.items()}
        except (TypeError, ValueError) as exc:
            raise ValueError(f"claims file 'companies' values must be CIK integers: {exc}") from exc
    else:
        raise ValueError("claims file must be a list or an object with a 'claims' list")

    claims: list[SubmittedClaim] = []
    for index, item in enumerate(raw_claims):
        if not isinstance(item, dict):
            raise ValueError(f"claim {index} must be an object")
        try:
            cites = item["cites"]
            if not isinstance(cites, list) or not all(isinstance(c, str) for c in cites):
                raise ValueError("cites must be a list of strings")
            claims.append(
                SubmittedClaim(
                    section=str(item["section"]),
                    type=ClaimType(str(item["type"])),
                    text=str(item["text"]),
                    cites=tuple(cites),
                    probability=item.get("probability"),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"claim {index} is invalid: {exc}") from exc
    return claims, companies


@app.command("check-claims")
def check_claims(
    ticker: Annotated[
        str, typer.Argument(help="US ticker symbol (resolved via SEC unless --cik).")
    ],
    as_of: Annotated[
        str,
        typer.Option("--as-of", help="Point-in-time date for the record (YYYY-MM-DD)."),
    ],
    claims: Annotated[
        Path,
        typer.Option(
            "--claims",
            help="JSON file: a list of submitted claims, or {claims, companies}.",
        ),
    ],
    config: ConfigOption = DEFAULT_CONFIG,
    cik: Annotated[
        int | None,
        typer.Option("--cik", help="SEC CIK; skips the ticker lookup when set."),
    ] = None,
    company: Annotated[
        str | None,
        typer.Option("--company", help="Company name added to the words-check map (optional)."),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write a JSON report to this path (optional)."),
    ] = None,
) -> None:
    """Review and check submitted claims against the local fact store (rows 1a / 1c).

    Same path the dossier builder uses: render/review each claim, then ``check_dossier``.
    Exit code 1 if any claim is withheld.
    """
    import json
    from datetime import date

    from foxcast.claims.checker import CheckContext, check_dossier
    from foxcast.claims.render import (
        RenderedClaim,
        WithheldClaim,
        claim_id_of,
        review_claim,
    )
    from foxcast.dossier.builder import NOT_A_SECTION, shown_reason
    from foxcast.dossier.schema import CLAIM_TARGETS
    from foxcast.store.archive import SourceArchive
    from foxcast.store.pit import FactStore
    from foxcast.watchlist import validate_ticker

    settings = _settings(config)
    try:
        symbol = validate_ticker(ticker.strip().upper())
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    try:
        as_of_date = date.fromisoformat(as_of)
    except ValueError as exc:
        console.print(f"[red]--as-of must be YYYY-MM-DD, got {as_of!r}[/red]")
        raise typer.Exit(code=1) from exc

    data = settings.data_dir
    store_path = data / "facts.duckdb"
    if not store_path.exists():
        console.print(
            f"[red]No fact store at {store_path}. Run `foxcast ingest` for this ticker first.[/red]"
        )
        raise typer.Exit(code=1)

    try:
        archive = SourceArchive.outside_git(settings.archive_dir)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if cik is None:
        contact = _require_sec_contact(settings)
        with httpx.Client() as client:
            try:
                resolved = _resolve_cik(client, contact, symbol)
            except (ValueError, httpx.HTTPError) as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1) from exc
    else:
        resolved = cik

    try:
        submitted, file_companies = _load_submitted_claims(claims)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    # File map first; CLI subject ticker (and optional --company) always win.
    companies = {**file_companies, symbol: resolved}
    if company:
        companies[company] = resolved

    store = FactStore(store_path)
    view = store.at(as_of_date)
    # Same review loop as foxcast.dossier.builder._review (CLAIM_TARGETS + review_claim).
    reviewed: list[RenderedClaim | WithheldClaim] = []
    for claim in submitted:
        if claim.section not in CLAIM_TARGETS:
            reviewed.append(
                WithheldClaim(
                    claim_id_of(claim),
                    claim.section,
                    claim.type,
                    claim.text,
                    claim.cites,
                    (NOT_A_SECTION,),
                )
            )
        else:
            reviewed.append(review_claim(claim, view))

    context = CheckContext(view=view, archive=archive, subject_cik=resolved, companies=companies)
    result = check_dossier(reviewed, context)

    style = "yellow" if result.withheld else "green"
    rate = f"; rate {result.rate:.0%}" if result.rate is not None else ""
    console.print(
        f"[{style}]{symbol} as of {as_of_date}:[/{style}] "
        f"{result.verified} verified, {len(result.withheld)} withheld "
        f"({result.counted} counted){rate}; "
        f"words check {result.words_check_version}."
    )
    for entry in result.withheld:
        reasons = "; ".join(shown_reason(r) for r in entry.reasons)
        console.print(f"  withheld {entry.claim_id} ({entry.section}/{entry.type}): {reasons}")

    if out is not None:
        report = {
            "ticker": symbol,
            "cik": resolved,
            "as_of": as_of_date.isoformat(),
            "words_check_version": result.words_check_version,
            "counted": result.counted,
            "verified": result.verified,
            "withheld": len(result.withheld),
            "rate": result.rate,
            "shown": [c.claim_id for c in result.shown],
            "withheld_claims": [
                {
                    "claim_id": w.claim_id,
                    "section": w.section,
                    "type": str(w.type),
                    "reasons": [shown_reason(r) for r in w.reasons],
                }
                for w in result.withheld
            ],
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        console.print(f"Wrote {out}.")

    if result.withheld:
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
