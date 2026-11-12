# Agent coordination

foxcast is scorecard-first. Parallel agents use **git worktrees** so each agent has its own directory and branch. Do not write in another agent's checkout.

## Worktrees

| Path | Branch | Owner |
|------|--------|--------|
| repo root (`foxcast/`) | `main` | Integration only; short hotfixes OK |
| `.worktrees/cursor-structure` | `feat/structure-cli-wiring` | Cursor — structure / CLI / docs accuracy |
| `.worktrees/claude-stage1` | `feat/stage1-…` (create when needed) | Claude Code — Stage 1 claim/dossier lane |
| `.worktrees/antigravity-scaffold` | optional | Antigravity — non-core scaffolding only |

`.worktrees/` is gitignored. One branch per worktree; never check out the same branch in two places.

## Ownership

| Lane | Owns | Does not touch |
|------|------|----------------|
| **Claude Code** | `claims/*`, `sec_ingest.py`, dossier assembly / section schema (row 1d), fiscal-period / gate runners, Stage 1A.6+ | Bulk rewrites of `cli/app.py`, `sim/*`, inventing scorecard pass lines |
| **Cursor** | CLI wiring (`ingest`, `check-claims`, doctor), `llm/` + `mcp/` interfaces, `docs/MODELS.md` accuracy, this file, Stage 2 **stubs only** | Rewriting `checker.py` / `planted.py` / numbers rule |
| **Antigravity** (optional) | Boilerplate, operator docs spikes on a throwaway branch | Scorecard, ledger, claims truth path |
| **Travis** | Merge to `main`, scorecard wording, ship / don't ship | — |

Shared lock files (`pyproject.toml`, `uv.lock`, `SCORECARD.md`, `scorecard.toml`): **one owner per PR**; others rebase.

## Protocol

1. Start from green `main` (CI green on the tip you branch from).
2. Work only in your worktree; `uv sync` there; do not share `.pytest_tmp` or DuckDB scratch across trees.
3. Open a short PR early so CI runs on the branch.
4. Rebase onto `main` after another lane merges.
5. Prefer merge order: truth/library → CLI wiring → stubs/docs.
6. If two agents need the same file, **stop and serialize**.

## Stage 1A status (as of 2026-11-11)

Tasks **1A.1–1A.5** are on `main` (through `fda8dfe`). Next Claude Code boundary: fiscal-period fix, then **1A.6** (dossier build path). 10-K section splitting waits on the `edgartools` seven-day pin until **2026-11-16**.

## Honesty reminders

- Scorecard pass lines change only with a dated changelog entry.
- The model never writes a number; code inserts figures from fact IDs.
- No lookahead; no scorecard number from a notebook.
- Ship code, never data.
