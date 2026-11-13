"""``python -m foxcast.mcp``: run the tool server over stdio, or write the room.

    python -m foxcast.mcp serve [--config PATH] [--as-of DATE] [--company TICKER:CIK ...]
    python -m foxcast.mcp room FOLDER --as-of DATE [--company TICKER:CIK ...] [--config PATH]

``serve`` is what the room's ``.mcp.json`` launches, with the room's pins: the one as-of
date and the companies the agent may open. Without pins it serves any ingested company on
any past date, for development only. It reads the store and archive named
in the foxcast config, keeps runs in ``<data>/runs`` and writes dossiers to
``<data>/dossiers``. stdout is the protocol channel; messages go to stderr.
"""

import argparse
import logging
import re
import sys
from datetime import date
from pathlib import Path

from foxcast.config import default_config_path, load_settings
from foxcast.daily import code_version
from foxcast.mcp.inputs import TICKER_PATTERN
from foxcast.mcp.room import write_room
from foxcast.mcp.server import Pins, open_server
from foxcast.mcp.stdio import serve_stdio
from foxcast.store.archive import SourceArchive

TICKER = re.compile(TICKER_PATTERN)
PROJECT = Path(__file__).resolve().parents[3]  # src/foxcast/mcp/__main__.py -> the repo


def _company(text: str) -> tuple[str, int]:
    ticker, _, cik = text.partition(":")
    if not (TICKER.fullmatch(ticker) and cik.isdigit()):
        raise argparse.ArgumentTypeError(f"expected TICKER:CIK, got {text!r}")
    return ticker, int(cik)


def _as_of(text: str) -> str:
    try:
        date.fromisoformat(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {text!r}") from error
    return text


def _serve(config: Path, pins: Pins) -> int:
    settings = load_settings(config)
    store_path = settings.data_dir / "facts.duckdb"
    if not store_path.exists():
        print(
            f"foxcast: no fact store at {store_path}; run `foxcast ingest` first", file=sys.stderr
        )
        return 1
    try:
        archive = SourceArchive.outside_git(settings.archive_dir)
    except ValueError as error:
        print(f"foxcast: {error}", file=sys.stderr)
        return 1
    server = open_server(
        store_path=store_path,
        archive=archive,
        runs_dir=settings.data_dir / "runs",
        dossier_dir=settings.data_dir / "dossiers",
        code_commit=code_version(PROJECT),
        pins=pins,
    )
    serve_stdio(server)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m foxcast.mcp")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the tool server over stdio")
    serve.add_argument("--config", type=Path, default=default_config_path())
    serve.add_argument("--as-of", type=_as_of, default=None)
    serve.add_argument("--company", type=_company, action="append", default=[])
    room = commands.add_parser("room", help="write the locked-down dossier folder")
    room.add_argument("folder", type=Path)
    room.add_argument("--as-of", type=_as_of, required=True)
    room.add_argument("--company", type=_company, action="append", default=[])
    room.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    companies = dict(args.company)
    if args.command == "serve":
        return _serve(args.config, Pins(as_of=args.as_of, companies=companies))
    try:
        written = write_room(
            args.folder,
            project=PROJECT,
            as_of=args.as_of,
            companies=companies,
            config=args.config,
        )
    except ValueError as error:
        print(f"foxcast: {error}", file=sys.stderr)
        return 1
    for path in written:
        print(path, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
