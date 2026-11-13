"""The foxcast tool server, locked down (Stage 1A.7; F20 part 1; change T; row 4c).

:class:`ToolServer` is the whole server without its transport: ``call(tool, arguments)``
validates the input, runs the tool, wraps the answer in the common header
(:mod:`foxcast.mcp.envelope`), writes one line to the run's call log and returns the
result. The stdio transport (:mod:`foxcast.mcp.stdio`) and the CI replay
(:mod:`foxcast.mcp.replay`) both drive this one method, so what CI replays is exactly what
the agent ran.

The server reads only the local record that ``foxcast ingest`` built. It has no network
access, no shell and no file path an agent can choose: its only writes are the run folder,
derived facts that code computes, and the dossier files.
"""

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from importlib import resources
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from foxcast.claims.numbers_rule import MAX_CLAIM_CHARS
from foxcast.claims.render import MAX_CITES, ClaimType
from foxcast.dossier.builder import RunCost
from foxcast.dossier.schema import CLAIM_TARGETS
from foxcast.facts.record import has_hidden_characters, sha256_hex
from foxcast.ledger.lock import exclusive_lock
from foxcast.mcp import reads
from foxcast.mcp.dossier_tools import (
    MAX_CLAIMS_PER_RUN,
    Record,
    derive_fact,
    render_dossier,
    submit_claims,
)
from foxcast.mcp.envelope import (
    ErrorCode,
    Meter,
    Outcome,
    ToolError,
    canonical,
    envelope,
    sha256_text,
)
from foxcast.mcp.inputs import INPUTS, ResearchGetContext
from foxcast.mcp.runs import CallRecord, Run, RunInfo, Runs
from foxcast.sources.sec_filings import EIGHT_K_ITEM_FIELD

log = logging.getLogger(__name__)

WORKFLOW_FILE: Final = "WORKFLOW.md"
MAX_LOGGED_ARGUMENT_BYTES: Final = 200_000
MAX_UNBOUND_ARGUMENT_BYTES: Final = 4_000
MAX_NAME_CHARS: Final = 80  # the dossier header's limit for a company name


def workflow_text() -> str:
    """The workflow file that tells the agent how to build a dossier."""
    return resources.files("foxcast.mcp").joinpath(WORKFLOW_FILE).read_text(encoding="utf-8")


def workflow_sha256() -> str:
    return sha256_hex(workflow_text().encode("utf-8"))


@dataclass(frozen=True)
class Pins:
    """What the operator fixed when launching the server; the agent cannot change it.

    ``as_of``: the one frozen date every run in this room uses. ``companies``: the tickers
    the room may open, each with its CIK. Unset means not pinned (tests and development).
    """

    as_of: str | None = None
    companies: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ServerConfig:
    record: Record
    runs: Runs
    code_commit: str | None
    prompt_sha256: str
    pins: Pins = field(default_factory=Pins)


@dataclass(frozen=True)
class Timing:
    """When a call started and how long it took; the replay passes the logged values."""

    started_at: datetime | None = None
    seconds: float | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


RULES: Final = {
    "claim_sections": sorted(CLAIM_TARGETS),
    "claim_types": {
        str(ClaimType.FACT): "a statement the cited facts or passages show; no probability",
        str(ClaimType.OPINION): "a judgment resting on the cited items; no probability",
        str(ClaimType.ESTIMATE): "a judgment with a probability between 0 and 1",
        str(ClaimType.FORECAST): "a statement about the future with a probability",
    },
    "placeholders": "write {f_...} or {p_...} where code should insert a fact or passage; "
    "every placeholder must also be in cites",
    "numbers_rule": "no digits, number words, quantity words, quotes, $, % or links in "
    "your own words, except dates, periods (Q3 2025, FY2025, fiscal Q1 2026), form names "
    "and item numbers. Quantity words (doubled, half) need a placed derived fact; use "
    "facts_derive to make one",
    "fiscal_periods": "write a company's fiscal periods as 'fiscal Q1 2026' or 'Q1 FY2026'; "
    "plain 'Q1 2026' means the calendar quarter",
    "max_claim_chars": MAX_CLAIM_CHARS,
    "max_cites_per_claim": MAX_CITES,
    "max_claims_per_run": MAX_CLAIMS_PER_RUN,
}


class ToolServer:
    def __init__(
        self,
        config: ServerConfig,
        *,
        clock: Callable[[], datetime] = _utc_now,
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._config = config
        self._clock = clock
        self._timer = timer

    # --- the one entry point ---

    def call(self, tool: str, arguments: Any, timing: Timing | None = None) -> dict[str, Any]:
        timing = timing or Timing()
        started_at = timing.started_at or self._clock()
        begin = self._timer()
        with exclusive_lock(self._config.runs.folder / ".lock"):
            run, result = self._answer(tool, arguments, started_at)
            text = canonical(result)
            seconds = timing.seconds if timing.seconds is not None else self._timer() - begin
            record = CallRecord(
                seq=len(run.calls()) if run else 0,
                tool=tool[:100],
                arguments=_loggable(arguments, bound=run is not None),
                ok=bool(result["ok"]),
                result_sha256=sha256_text(text),
                result_bytes=len(text.encode("utf-8")),
                started_at=started_at.isoformat(),
                seconds=round(seconds, 3),
            )
            if run is not None:
                run.log(record)
            else:
                self._config.runs.log_unbound(record)
        return result

    def _answer(
        self, tool: str, arguments: Any, started_at: datetime
    ) -> tuple[Run | None, dict[str, Any]]:
        run: Run | None = None
        meter = Meter()
        try:
            # Bind the call to its run first, so a refused call is still logged and metered
            # in that run (row 4c), not only the calls that succeed.
            run = self._config.runs.find(_named_run(tool, arguments))
            if run is not None:
                meter = _meter(run)
            model = INPUTS.get(tool)
            if model is None:
                raise ToolError(ErrorCode.UNKNOWN_TOOL, f"no tool named {tool[:100]!r}")
            if not isinstance(arguments, dict):
                raise ToolError(ErrorCode.INVALID_INPUT, "arguments must be a JSON object")
            args = _validated(model, arguments)
            if isinstance(args, ResearchGetContext):
                opened, outcome = self._get_context(args, started_at)
                if run is None:
                    run, meter = opened, _meter(opened)
            else:
                run = self._config.runs.get(args.run_id)
                meter = _meter(run)
                outcome = self._dispatch(tool, args, run, started_at)
            info = run.info
            return run, envelope(
                tool, run_id=info.run_id, as_of=info.as_of, meter=meter, outcome=outcome
            )
        except ToolError as error:
            return run, self._refusal(tool, run, meter, error)
        except Exception as error:  # a bug: told plainly, logged in full, never fatal
            log.exception("tool %s failed", tool)
            failure = ToolError(ErrorCode.INTERNAL, f"internal error ({type(error).__name__})")
            return run, self._refusal(tool, run, meter, failure)

    def _refusal(
        self, tool: str, run: Run | None, meter: Meter, error: ToolError
    ) -> dict[str, Any]:
        info = run.info if run else None
        return envelope(
            tool[:100],
            run_id=info.run_id if info else None,
            as_of=info.as_of if info else None,
            meter=meter,
            error=error,
        )

    def _dispatch(self, tool: str, args: Any, run: Run, started_at: datetime) -> Outcome:
        record, info = self._config.record, run.info
        if tool == "sec_get_facts":
            return reads.get_facts(record.store, info, args)
        if tool == "sec_get_text_facts":
            return reads.get_text_facts(record.store, info, args)
        if tool == "sec_list_filings":
            return reads.list_filings(record.store, info, args)
        if tool == "sec_read_passages":
            return reads.read_passages(record.store, info, args)
        if tool == "sec_search_passages":
            return reads.search_passages(record.store, info, args)
        if tool == "facts_derive":
            return derive_fact(record, run, args)
        if tool == "dossier_submit_claims":
            return submit_claims(record, run, args)
        if tool == "dossier_render":
            cost = run.cost()
            spent = RunCost(cost.tool_calls, cost.result_bytes, cost.wall_seconds)
            return render_dossier(record, run, built_at=started_at, cost_before=spent)
        raise ToolError(ErrorCode.UNKNOWN_TOOL, f"no tool named {tool!r}")

    # --- research_get_context ---

    def _get_context(self, args: ResearchGetContext, started_at: datetime) -> tuple[Run, Outcome]:
        as_of = date.fromisoformat(args.as_of)
        if as_of > started_at.date():
            raise ToolError(ErrorCode.INVALID_INPUT, "the as-of date is in the future")
        for label, text in (("model_name", args.model_name), ("model_cutoff", args.model_cutoff)):
            if has_hidden_characters(text):
                raise ToolError(ErrorCode.INVALID_INPUT, f"{label} has hidden characters")
        pinned = self._check_pins(args)  # before the record is read, so nothing leaks
        store = self._config.record.store
        summary = _record_summary(store, args.cik, as_of)
        if not summary["numbers"] and not summary["filings"]:
            raise ToolError(
                ErrorCode.NOT_FOUND,
                f"nothing in the record for CIK {args.cik} as of {args.as_of}; the operator "
                "must run `foxcast ingest` for this company first",
            )
        company, verified = self._identify(args, as_of)
        if not (verified or pinned):
            raise ToolError(
                ErrorCode.CONFLICT,
                f"cannot confirm that {args.ticker} is CIK {args.cik}: no filing index is "
                "archived for it. The operator can pin the company when starting the server",
            )
        info = RunInfo(
            run_id=f"{args.ticker}-{args.as_of}",
            ticker=args.ticker,
            cik=args.cik,
            company=company,
            as_of=args.as_of,
            model_name=args.model_name.strip(),
            model_cutoff=args.model_cutoff.strip(),
            holding=args.holding,
            prompt_sha256=self._config.prompt_sha256,
            ticker_verified=verified or pinned,
            opened_at=started_at.isoformat(),
            code_commit=self._config.code_commit,
        )
        run, created = self._config.runs.open(info)
        info = run.info
        claims = run.claims()
        by_section: dict[str, int] = {}
        for claim in claims:
            by_section[claim["section"]] = by_section.get(claim["section"], 0) + 1
        status = "opened" if created else ("rendered" if run.rendered else "resumed")
        data = {
            "status": status,
            "run": {
                "ticker": info.ticker,
                "cik": info.cik,
                "as_of": info.as_of,
                "ticker_verified": info.ticker_verified,
                "model_name": info.model_name,
                "untrusted": {"company": info.company},  # the name as the filer gives it
            },
            "record": summary,
            "claims": {"submitted": len(claims), "by_section": by_section},
            "rules": RULES,
            "dossier": (run.record() or {}).get("dossier"),
        }
        return run, Outcome(data, untrusted=("run.untrusted",))

    def _check_pins(self, args: ResearchGetContext) -> bool:
        """Refuse a run outside the operator's pins; True if the company is pinned."""
        pins = self._config.pins
        if pins.as_of is not None and args.as_of != pins.as_of:
            raise ToolError(
                ErrorCode.INVALID_INPUT,
                f"this room is pinned to the as-of date {pins.as_of}; use that date",
            )
        if not pins.companies:
            return False
        if pins.companies.get(args.ticker) != args.cik:
            raise ToolError(
                ErrorCode.INVALID_INPUT,
                f"this room is pinned to {', '.join(sorted(pins.companies))}; "
                f"{args.ticker} with CIK {args.cik} is not one of them",
            )
        return True

    def _identify(self, args: ResearchGetContext, as_of: date) -> tuple[str | None, bool]:
        """The company's name, and whether the ticker is its, from the archived filing index.

        The ticker is checked against the newest index archived, since an early as-of date
        may precede every 8-K in the record. The name is taken only from an index public on
        the as-of date, so a later rename never shows in an earlier dossier.
        """
        store = self._config.record.store
        dated = store.texts_for(cik=args.cik, field_prefix=EIGHT_K_ITEM_FIELD, as_of=as_of)
        newest = store.texts_for(cik=args.cik, field_prefix=EIGHT_K_ITEM_FIELD, as_of=date.max)
        if not newest:
            return None, False
        payload = self._index(newest[-1].content_sha256)
        tickers = payload.get("tickers") if payload else None
        if not isinstance(tickers, list):
            return None, False
        if args.ticker not in tickers:
            raise ToolError(
                ErrorCode.CONFLICT,
                f"CIK {args.cik}'s filing index does not list the ticker {args.ticker}",
            )
        name = (self._index(dated[-1].content_sha256) or {}).get("name") if dated else None
        if not isinstance(name, str):
            return None, True
        cleaned = name.strip()
        plain = 0 < len(cleaned) <= MAX_NAME_CHARS and not has_hidden_characters(cleaned)
        return (cleaned if plain else None), True

    def _index(self, sha256: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._config.record.archive.load(sha256))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None


def _validated(model: Any, arguments: dict[str, Any]) -> Any:
    try:
        return model.model_validate(arguments)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
            for e in error.errors(include_url=False, include_input=False)[:10]
        )
        raise ToolError(ErrorCode.INVALID_INPUT, problems) from error


def _meter(run: Run) -> Meter:
    cost = run.cost()
    return Meter(cost.tool_calls, cost.result_bytes)


def _loggable(arguments: Any, *, bound: bool) -> Any:
    """The arguments as received, unless too large to log; JSON values only.

    A call that named no run is kept smaller, so stray calls cannot grow that log fast.
    """
    try:
        text = canonical(arguments)
    except (TypeError, ValueError):
        return {"unloggable": True}
    limit = MAX_LOGGED_ARGUMENT_BYTES if bound else MAX_UNBOUND_ARGUMENT_BYTES
    if len(text) > limit:
        return {"too_large_to_log": len(text)}
    return json.loads(text)


def _named_run(tool: str, arguments: Any) -> str | None:
    """The run a call names, before its input is validated."""
    if not isinstance(arguments, dict):
        return None
    if tool == "research_get_context":
        ticker, as_of = arguments.get("ticker"), arguments.get("as_of")
        if isinstance(ticker, str) and isinstance(as_of, str):
            return f"{ticker}-{as_of}"
        return None
    run_id = arguments.get("run_id")
    return run_id if isinstance(run_id, str) else None


def _family(field: str) -> str:
    """The prefix a field is listed under: ``us-gaap:``, ``sec:form4:``, ``sec:8-K:``."""
    parts = field.split(":")
    return ":".join(parts[:2] if parts[0] == "sec" else parts[:1]) + ":"


def _record_summary(store: Any, cik: int, as_of: date) -> dict[str, Any]:
    numbers: dict[str, int] = {}
    for fact in store.numbers_for(cik=cik, field_prefix="", as_of=as_of):
        numbers[_family(fact.field)] = numbers.get(_family(fact.field), 0) + 1
    derived = store.derived_as_of(cik=cik, as_of=as_of)
    if derived:
        numbers["derived:"] = len(derived)
    texts: dict[str, int] = {}
    for fact in store.texts_for(cik=cik, field_prefix="", as_of=as_of):
        texts[_family(fact.field)] = texts.get(_family(fact.field), 0) + 1
    filings = [
        {"accession": d.accession, "form": d.form, "public_on": d.public_on.isoformat()}
        for d in store.documents_for(cik=cik, as_of=as_of)
    ]
    return {"numbers": numbers, "text_facts": texts, "filings": filings}


def open_server(
    *,
    store_path: Path,
    archive: Any,
    runs_dir: Path,
    dossier_dir: Path,
    code_commit: str | None,
    prompt_sha256: str | None = None,
    pins: Pins | None = None,
) -> ToolServer:
    from foxcast.store.pit import FactStore

    record = Record(store=FactStore(store_path), archive=archive, dossier_dir=dossier_dir)
    config = ServerConfig(
        record=record,
        runs=Runs(runs_dir),
        code_commit=code_commit,
        prompt_sha256=prompt_sha256 or workflow_sha256(),
        pins=pins or Pins(),
    )
    return ToolServer(config)
