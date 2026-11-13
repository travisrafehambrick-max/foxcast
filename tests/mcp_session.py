"""A scripted agent session against the tool server, on the synthetic SEC world.

Used by the tool-server tests and to (re)write the committed call-log fixture that CI
replays. Regenerate the fixture with::

    FOXCAST_WRITE_REPLAY_FIXTURE=1 uv run pytest tests/test_mcp_replay.py
"""

import itertools
import shutil
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from foxcast.mcp.dossier_tools import Record
from foxcast.mcp.runs import Runs
from foxcast.mcp.server import Pins, ServerConfig, ToolServer, workflow_sha256
from foxcast.sec_ingest import IngestPlan, ingest_company
from foxcast.store.archive import SourceArchive
from foxcast.store.pit import FactStore

from .test_checker import AS_OF, CONCEPTS, handler
from .test_sec_ingest import GOOGL, NOW

FIXTURE_COMMIT = "fixture-commit"
SESSION_START = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
MODEL = {"model_name": "Claude Opus 5.5", "model_cutoff": "2026-06"}
OPEN = {"ticker": "GOOGL", "cik": GOOGL, "as_of": AS_OF.isoformat(), **MODEL}
RUN_ID = f"GOOGL-{AS_OF.isoformat()}"


def make_world(folder: Path) -> tuple[FactStore, SourceArchive]:
    folder.mkdir(parents=True, exist_ok=True)
    store, archive = FactStore(folder / "facts.duckdb"), SourceArchive(folder / "sources")
    ingest_company(
        GOOGL,
        as_of=AS_OF,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        contact="Jane Doe jane@example.com",
        store=store,
        archive=archive,
        plan=IngestPlan(concepts=tuple(CONCEPTS), max_form4=3),
        now=lambda: NOW,
    )
    return store, archive


def copy_world(template: Path, folder: Path) -> tuple[FactStore, SourceArchive]:
    shutil.copytree(template, folder)
    return FactStore(folder / "facts.duckdb"), SourceArchive(folder / "sources")


def ticking_clock(start: datetime = SESSION_START) -> Callable[[], datetime]:
    seconds: Iterator[int] = itertools.count(0, 7)
    return lambda: start + timedelta(seconds=next(seconds))


def steady_timer(step: float = 0.25) -> Callable[[], float]:
    ticks = itertools.count()
    return lambda: next(ticks) * step


def make_server(world: tuple[FactStore, SourceArchive], work: Path, **overrides: Any) -> ToolServer:
    store, archive = world
    config = ServerConfig(
        record=Record(store=store, archive=archive, dossier_dir=work / "dossiers"),
        runs=Runs(work / "runs"),
        code_commit=FIXTURE_COMMIT,
        prompt_sha256=workflow_sha256(),
        pins=overrides.get("pins", Pins()),
    )
    return ToolServer(config, clock=overrides.get("clock", ticking_clock()), timer=steady_timer())


def ph(item_id: str) -> str:
    return "{" + item_id + "}"


def scripted_session(server: ToolServer) -> list[dict[str, Any]]:
    """What an agent does: open, read, derive, submit good and bad claims, render."""
    results = [server.call("research_get_context", OPEN)]
    results.append(server.call("sec_list_filings", {"run_id": RUN_ID}))
    facts = server.call("sec_get_facts", {"run_id": RUN_ID, "field_prefix": "us-gaap:Revenues"})
    results.append(facts)
    annual = [
        f
        for f in facts["data"]["items"]
        if f["period_start"]
        and f["period_start"].endswith("-01-01")
        and f["period_end"].endswith("-12-31")
    ]
    earlier, latest = annual[-2]["id"], annual[-1]["id"]
    results.append(server.call("sec_get_text_facts", {"run_id": RUN_ID, "limit": 5}))
    filings = results[1]["data"]["items"]
    ten_k = next(f for f in filings if f["form"] == "10-K")
    passages = server.call("sec_read_passages", {"run_id": RUN_ID, "accession": ten_k["accession"]})
    results.append(passages)
    results.append(
        server.call("sec_search_passages", {"run_id": RUN_ID, "query": "exchange commission"})
    )
    growth = server.call(
        "facts_derive", {"run_id": RUN_ID, "method": "growth", "a": earlier, "b": latest}
    )
    results.append(growth)
    growth_id = growth["data"]["fact"]["id"]
    passage_id = passages["data"]["items"][0]["id"]
    claims = [
        {
            "section": "financial_health",
            "type": "fact",
            "text": f"Revenue was {ph(latest)}.",
            "cites": [latest],
        },
        {
            "section": "bull_case",
            "type": "opinion",
            "text": f"Revenue grew {ph(growth_id)}, a sign of durable demand.",
            "cites": [growth_id, earlier, latest],
        },
        {
            "section": "business",
            "type": "fact",
            "text": f"The annual report describes the business: {ph(passage_id)}",
            "cites": [passage_id],
        },
        # Withheld: the model typed a number.
        {
            "section": "financial_health",
            "type": "fact",
            "text": f"Revenue was about 400 billion dollars {ph(latest)}.",
            "cites": [latest],
        },
        # Withheld: an estimate needs a probability.
        {
            "section": "bear_case",
            "type": "estimate",
            "text": f"Growth may slow from {ph(growth_id)}.",
            "cites": [growth_id],
        },
    ]
    results.append(server.call("dossier_submit_claims", {"run_id": RUN_ID, "claims": claims}))
    results.append(server.call("dossier_render", {"run_id": RUN_ID}))
    return results
