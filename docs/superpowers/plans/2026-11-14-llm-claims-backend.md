# LLM claims backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Define the `foxcast.llm` backend seam for Stage 1B research claims (Protocol + reference fixture agent that drives the Stage 1A.7 MCP `ToolServer`), and mark MODELS.md rows 3/5/6/B as stubbed.

**Architecture:** Mirror `conformance.Backend`: `ToolCaller` abstracts transport; `ClaimsBackend.run(caller, request)` takes the caller per invocation so one agent can be cross-checked in-process vs stdio later. `InProcessToolCaller` only delegates to `ToolServer.call`. `ReferenceClaimsBackend` is a deterministic fixture agent that opens, reads, derives, submits placeholders, and renders — every figure is a fact ID.

**Tech Stack:** Python 3.13, pytest, existing `foxcast.mcp.ToolServer` test world (`tests/mcp_session.py`).

## Global Constraints

- Work only in `.worktrees/cursor` on `cursor-llm-claims-client` from `origin/main`.
- Do not touch `pyproject.toml` / `uv.lock`, `SCORECARD.md`, `scorecard.toml`, `daily.py`, `claims/*`, or `sim/*`.
- Honesty: reference backend emits no hardcoded figures — only `{f_...}` / `{p_...}` placeholders from tool results / `facts_derive`.
- `InProcessToolCaller` must call `ToolServer.call`, never tool handler functions directly.

---

## File map

| File | Responsibility |
|------|----------------|
| `src/foxcast/llm/tools.py` | `ToolCaller` Protocol, `InProcessToolCaller` |
| `src/foxcast/llm/claims.py` | `ClaimsRequest`, `ClaimsRunResult`, `ClaimsBackend`, `ReferenceClaimsBackend` |
| `src/foxcast/llm/__init__.py` | Re-export public API |
| `tests/test_llm_claims.py` | Unit/integration tests against synthetic MCP world |
| `docs/MODELS.md` | Add `stubbed` status; rows 3, 5, 6, B |

---

### Task 1: Failing tests for ToolCaller + ClaimsBackend

- [x] Write `tests/test_llm_claims.py` covering: InProcess delegates to `ToolServer.call`; reference `run` returns `ClaimsRunResult` with envelopes + cost; claim text has no digits of its own; MODELS stubbed rows.
- [x] Run `uv run pytest tests/test_llm_claims.py -q` and confirm RED (import / missing API).

### Task 2: Implement `llm/tools.py` + `llm/claims.py`

- [x] Minimal code to pass tests.
- [x] Docstring on `ClaimsBackend.run` explaining per-invocation `caller`.
- [x] `uv run pytest tests/test_llm_claims.py -q` GREEN.

### Task 3: MODELS.md + package exports + quality gates

- [x] Status vocab includes **stubbed**; rows 3, 5, 6, B → stubbed.
- [x] `uv run pytest`, `uv run ruff check .`, `uv run mypy src`.
- [ ] Commit; open early PR; do not merge.
