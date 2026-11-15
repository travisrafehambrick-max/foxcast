"""The Stage 1A gate dossier runner: one frozen dossier set per ticker set and as-of date.

Rows 1c and 1e are read from a fixed set of dossiers: the 20-ticker gate set
(``[gate_set.1a]`` in ``scorecard.toml``) and the five row 1e audit tickers. Each set is
built on one frozen as-of date, chosen before its first dossier is built and recorded with
the run. :func:`run_gate` builds every dossier of a set on the no-model path (no research
step runs), writes them into one folder, and writes the run record beside them: the as-of
date, the build time, the seeds, the code version and the SHA-256 of every file written.
Once the run record exists the set is frozen, and the runner refuses to build it again.

Everything is checked before the first dossier is built: each ticker has its own CIK and
something in the record as of the date, the date is not after the build, and the code is a
clean commit. :func:`verify_gate_run` re-hashes a frozen set against its run record.
"""

import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final, Literal

from foxcast import __version__
from foxcast.claims.checker import WORDS_CHECK_VERSION
from foxcast.dossier.builder import DossierRequest, build_dossier
from foxcast.dossier.output import UnverifiedClaimError, write_dossier
from foxcast.dossier.schema import Dossier, SilentGapError, Status
from foxcast.mcp.runs import write_json_atomic
from foxcast.store.archive import SourceArchive
from foxcast.store.pit import FactStore
from foxcast.watchlist import validate_ticker

SCHEMA: Final = "foxcast.gate_run/1"
RUN_RECORD: Final = "run.json"
CLEAN_COMMIT: Final = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
RESEARCH_STEP: Final = "none: every dossier is built without a model (the no-model path)"
SET_NAMES: Final = {"gate": "gate-1a", "audit": "audit-1e"}


class GateRunError(ValueError):
    """A gate run that cannot start, or a dossier in it that cannot be built."""


@dataclass(frozen=True)
class TickerSet:
    """A ticker set as written in ``scorecard.toml``, with the seeds the run records."""

    name: str
    rows: tuple[str, ...]
    tickers: tuple[str, ...]
    seeds: tuple[tuple[str, int], ...]
    scorecard_sha256: str

    def __post_init__(self) -> None:
        for ticker in self.tickers:
            try:
                validate_ticker(ticker)
            except ValueError as error:
                raise GateRunError(str(error)) from error
        repeated = sorted({t for t in self.tickers if self.tickers.count(t) > 1})
        if repeated:
            raise GateRunError(f"{', '.join(repeated)} listed twice in {self.name}")


def load_ticker_set(scorecard: Path, which: Literal["gate", "audit"]) -> TickerSet:
    """The gate set (rows 1a-1d, 4c) or the row 1e audit set, read from ``scorecard``."""
    raw = scorecard.read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    try:
        gate, audit = data["gate_set"]["1a"], data["definition"]["1e"]
        seeds = (
            ("gate_set_draw", int(gate["seed"])),
            ("sampler_1e", int(audit["sampler_seed"])),
            ("planted_1c", int(data["definition"]["1c"]["planted_seed"])),
        )
        if which == "gate":
            rows, tickers = tuple(gate["rows"]), tuple(gate["tickers"])
            if len(tickers) != int(gate["size"]):
                raise GateRunError(
                    f"the gate set lists {len(tickers)} tickers but its size is {gate['size']}"
                )
        else:
            rows, tickers = ("1e",), tuple(audit["audit_tickers"])
    except KeyError as error:
        raise GateRunError(f"{scorecard.name} has no {error.args[0]!r} entry") from error
    return TickerSet(
        name=SET_NAMES[which],
        rows=rows,
        tickers=tickers,
        seeds=seeds,
        scorecard_sha256=hashlib.sha256(raw).hexdigest(),
    )


@dataclass(frozen=True)
class GateRequest:
    ticker_set: TickerSet
    as_of: date  # the frozen as-of date, chosen before the first dossier is built
    built_at: datetime  # one build time for the whole set
    companies: Mapping[str, int]  # ticker to SEC CIK, as the operator pins them
    code_commit: str | None  # foxcast.daily.code_version of the checkout
    allow_unclean_code: bool = field(default=False)

    def __post_init__(self) -> None:
        if self.built_at.tzinfo is None:
            raise GateRunError("the build time needs a time zone")


@dataclass(frozen=True)
class GateRun:
    folder: Path
    dossiers_sha256: str


# --- checks made before any dossier is built ---


def _code_is_clean(commit: str | None) -> bool:
    return commit is not None and CLEAN_COMMIT.fullmatch(commit) is not None


def _resolve(request: GateRequest) -> dict[str, int]:
    tickers = request.ticker_set.tickers
    missing = [t for t in tickers if t not in request.companies]
    if missing:
        raise GateRunError(f"no CIK given for {', '.join(missing)}")
    ciks = {t: request.companies[t] for t in tickers}
    by_cik: dict[int, list[str]] = {}
    for ticker, cik in ciks.items():
        if cik <= 0:
            raise GateRunError(f"{ticker}'s CIK must be a positive number, got {cik}")
        by_cik.setdefault(cik, []).append(ticker)
    shared = [" and ".join(names) for names in by_cik.values() if len(names) > 1]
    if shared:
        raise GateRunError(f"{'; '.join(shared)} are given the same CIK")
    return ciks


def _has_record(store: FactStore, cik: int, as_of: date) -> bool:
    return bool(
        store.numbers_for(cik=cik, field_prefix="", as_of=as_of)
        or store.texts_for(cik=cik, field_prefix="", as_of=as_of)
        or store.documents_for(cik=cik, as_of=as_of)
    )


def _preflight(request: GateRequest, store: FactStore, folder: Path) -> dict[str, int]:
    if (folder / RUN_RECORD).exists():
        raise GateRunError(f"{folder.name} is frozen: its run record exists; it is never rebuilt")
    if request.as_of > request.built_at.date():
        raise GateRunError(
            f"the as-of date {request.as_of} is after the build date {request.built_at.date()}"
        )
    if not request.allow_unclean_code and not _code_is_clean(request.code_commit):
        raise GateRunError(
            f"a gate run needs a clean commit, got {request.code_commit or 'no commit'}; "
            "commit the code first"
        )
    ciks = _resolve(request)
    empty = [t for t, cik in ciks.items() if not _has_record(store, cik, request.as_of)]
    if empty:
        raise GateRunError(
            f"nothing in the record as of {request.as_of} for {', '.join(empty)}; "
            "run `foxcast ingest` for them first"
        )
    return ciks


# --- the run ---


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_one(
    request: GateRequest,
    ticker: str,
    cik: int,
    store: FactStore,
    archive: SourceArchive,
    folder: Path,
) -> dict[str, Any]:
    dossier_request = DossierRequest(
        ticker=ticker,
        cik=cik,
        as_of=request.as_of,
        built_at=request.built_at,
        code_commit=request.code_commit,
    )
    try:
        dossier = build_dossier(dossier_request, store, archive)
        md_path, json_path = write_dossier(dossier, folder)
    except (SilentGapError, UnverifiedClaimError, ValueError) as error:
        raise GateRunError(f"{ticker}'s dossier could not be built: {error}") from error
    return _entry(dossier, md_path, json_path)


def _entry(dossier: Dossier, md_path: Path, json_path: Path) -> dict[str, Any]:
    c = dossier.certification
    filled = sum(1 for s in dossier.sections if s.status is Status.FILLED)
    return {
        "ticker": dossier.ticker,
        "cik": dossier.cik,
        "markdown_file": md_path.name,
        "markdown_sha256": _sha256_file(md_path),
        "json_file": json_path.name,
        "json_sha256": _sha256_file(json_path),
        "sections_filled": filled,
        "sections_unavailable": len(dossier.sections) - filled,
        "claims_counted": c.claims_counted,
        "claims_verified": c.claims_verified,
        "claims_withheld": c.claims_withheld,
        "items_shown": c.items_shown,
    }


def _digest(entries: list[dict[str, Any]]) -> str:
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _record(request: GateRequest, entries: list[dict[str, Any]]) -> dict[str, Any]:
    ticker_set = request.ticker_set
    return {
        "schema": SCHEMA,
        "set": ticker_set.name,
        "rows": list(ticker_set.rows),
        "tickers": list(ticker_set.tickers),
        "as_of": request.as_of.isoformat(),
        "built_at": request.built_at.isoformat(),
        "seeds": dict(ticker_set.seeds),
        "scorecard_sha256": ticker_set.scorecard_sha256,
        "code": {
            "version": __version__,
            "commit": request.code_commit,
            "clean": _code_is_clean(request.code_commit),
            "words_check_version": WORDS_CHECK_VERSION,
        },
        "model": None,
        "research_step": RESEARCH_STEP,
        "dossiers": entries,
        "dossiers_sha256": _digest(entries),
    }


def run_gate(request: GateRequest, store: FactStore, archive: SourceArchive, out: Path) -> GateRun:
    """Build and freeze the set's dossiers in ``out/<set>-<as of>``; write its run record.

    The dossiers are written first and the run record last, so a run that stops part way
    leaves no record and can be started again; a set with a record is frozen.
    """
    folder = out / f"{request.ticker_set.name}-{request.as_of.isoformat()}"
    ciks = _preflight(request, store, folder)
    folder.mkdir(parents=True, exist_ok=True)
    entries = [
        _build_one(request, ticker, ciks[ticker], store, archive, folder)
        for ticker in request.ticker_set.tickers
    ]
    record = _record(request, entries)
    write_json_atomic(folder / RUN_RECORD, record)
    return GateRun(folder=folder, dossiers_sha256=record["dossiers_sha256"])


def verify_gate_run(folder: Path) -> list[str]:
    """Each problem with a frozen set: a dossier missing or not matching its recorded hash."""
    path = folder / RUN_RECORD
    if not path.exists():
        return [f"no {RUN_RECORD} in {folder}"]
    record = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    if _digest(record["dossiers"]) != record["dossiers_sha256"]:
        problems.append(f"the dossier list in {RUN_RECORD} does not match its recorded hash")
    for entry in record["dossiers"]:
        for kind in ("markdown", "json"):
            name = entry[f"{kind}_file"]
            if not (folder / name).exists():
                problems.append(f"{name} is missing")
            elif _sha256_file(folder / name) != entry[f"{kind}_sha256"]:
                problems.append(f"{name} does not match its recorded hash")
    return problems


__all__ = [
    "RUN_RECORD",
    "SCHEMA",
    "GateRequest",
    "GateRun",
    "GateRunError",
    "TickerSet",
    "load_ticker_set",
    "run_gate",
    "verify_gate_run",
]
