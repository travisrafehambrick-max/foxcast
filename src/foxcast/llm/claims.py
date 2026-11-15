"""Backend seam for the Stage 1B research-claims step.

A :class:`ClaimsBackend` drives the locked-down Stage 1A.7 tool server (via a
:class:`~foxcast.llm.tools.ToolCaller`) to open a run, read the record, submit claims
that cite fact and passage IDs, and render the dossier. Real model backends plug in
here later. The :class:`ReferenceClaimsBackend` is a fixture agent: it answers with
foxcast's own tools and recorded facts, with no external model and no hardcoded figures.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Protocol

from foxcast.llm.tools import ToolCaller

Holding = Literal["holds", "none", "not-declared"]


def _freeze(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    """A read-only view so a frozen audit record cannot be rewritten in place."""
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True)
class ClaimsRequest:
    """What the operator asks a claims backend to research."""

    ticker: str
    cik: int
    as_of: str
    model_name: str
    model_cutoff: str
    holding: Holding = "not-declared"


@dataclass(frozen=True)
class LoggedCall:
    """One tool invocation: arguments plus the full envelope the server returned."""

    tool: str
    arguments: Mapping[str, Any]
    result: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", _freeze(self.arguments))
        object.__setattr__(self, "result", _freeze(self.result))


@dataclass(frozen=True)
class ClaimsRunResult:
    """Outcome of a claims run, including the audit trail the gate runner needs."""

    run_id: str
    markdown_file: str | None
    json_file: str | None
    claims_verified: int
    claims_withheld: int
    cost: Mapping[str, int | float]
    calls: tuple[LoggedCall, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "cost", _freeze(self.cost))


class ClaimsBackend(Protocol):
    """Something that produces research claims by driving foxcast tools.

    ``run`` takes the :class:`~foxcast.llm.tools.ToolCaller` per invocation rather than
    as constructor state so one backend can be driven over different transports — the
    conformance-style cross-check later: same agent, in-process vs stdio, same claims.
    """

    name: str

    def run(self, caller: ToolCaller, request: ClaimsRequest) -> ClaimsRunResult: ...


def _ph(item_id: str) -> str:
    return "{" + item_id + "}"


def _require_ok(call: LoggedCall) -> Mapping[str, Any]:
    if not call.result.get("ok"):
        error = call.result.get("error") or {}
        raise RuntimeError(
            f"tool {call.tool!r} refused: {error.get('code')}: {error.get('message')}"
        )
    data = call.result.get("data")
    if not isinstance(data, Mapping):
        raise RuntimeError(f"tool {call.tool!r} returned no data")
    return data


class ReferenceClaimsBackend:
    """Fixture agent: scripts the MCP tools with no model and no hardcoded figures.

    Every number in a claim is a fact or passage placeholder taken from tool results (or
    from ``facts_derive``). Code inserts the figures later.
    """

    name = "reference"

    def run(self, caller: ToolCaller, request: ClaimsRequest) -> ClaimsRunResult:
        calls: list[LoggedCall] = []

        def invoke(tool: str, arguments: Mapping[str, Any]) -> LoggedCall:
            result = caller.call(tool, arguments)
            logged = LoggedCall(tool, dict(arguments), result)
            calls.append(logged)
            return logged

        open_args = {
            "ticker": request.ticker,
            "cik": request.cik,
            "as_of": request.as_of,
            "model_name": request.model_name,
            "model_cutoff": request.model_cutoff,
            "holding": request.holding,
        }
        opened_call = invoke("research_get_context", open_args)
        _require_ok(opened_call)
        run_id = opened_call.result.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise RuntimeError("research_get_context returned no run_id")

        filings = _require_ok(invoke("sec_list_filings", {"run_id": run_id}))
        facts = _require_ok(
            invoke("sec_get_facts", {"run_id": run_id, "field_prefix": "us-gaap:Revenues"})
        )
        annual = [
            f
            for f in facts["items"]
            if f.get("period_start")
            and str(f["period_start"]).endswith("-01-01")
            and str(f["period_end"]).endswith("-12-31")
        ]
        if len(annual) < 2:
            raise RuntimeError("reference backend needs at least two annual revenue facts")
        earlier, latest = annual[-2]["id"], annual[-1]["id"]

        _require_ok(invoke("sec_get_text_facts", {"run_id": run_id, "limit": 5}))
        ten_k = next((f for f in filings["items"] if f["form"] == "10-K"), None)
        if ten_k is None:
            raise RuntimeError("reference backend needs an archived 10-K in the record")
        passages = _require_ok(
            invoke("sec_read_passages", {"run_id": run_id, "accession": ten_k["accession"]})
        )
        if not passages["items"]:
            raise RuntimeError("reference backend needs at least one passage from the 10-K")
        _require_ok(
            invoke("sec_search_passages", {"run_id": run_id, "query": "exchange commission"})
        )
        growth = _require_ok(
            invoke(
                "facts_derive",
                {"run_id": run_id, "method": "growth", "a": earlier, "b": latest},
            )
        )
        growth_id = growth["fact"]["id"]
        passage_id = passages["items"][0]["id"]

        # Only placeholders and cites from tool results — never a typed figure.
        claims = [
            {
                "section": "financial_health",
                "type": "fact",
                "text": f"Revenue was {_ph(latest)}.",
                "cites": [latest],
            },
            {
                "section": "bull_case",
                "type": "opinion",
                "text": f"Revenue grew {_ph(growth_id)}, a sign of durable demand.",
                "cites": [growth_id, earlier, latest],
            },
            {
                "section": "business",
                "type": "fact",
                "text": f"The annual report describes the business: {_ph(passage_id)}",
                "cites": [passage_id],
            },
        ]
        _require_ok(invoke("dossier_submit_claims", {"run_id": run_id, "claims": claims}))
        rendered = _require_ok(invoke("dossier_render", {"run_id": run_id}))
        cost = rendered.get("cost")
        if not isinstance(cost, Mapping):
            raise RuntimeError("dossier_render returned no cost meter")
        return ClaimsRunResult(
            run_id=run_id,
            markdown_file=rendered.get("markdown_file"),
            json_file=rendered.get("json_file"),
            claims_verified=int(rendered["claims_verified"]),
            claims_withheld=int(rendered["claims_withheld"]),
            cost=dict(cost),
            calls=tuple(calls),
        )
