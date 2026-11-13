# Agent coordination

foxcast is scorecard-first. Parallel agents use **git worktrees** so each agent has its own directory and branch. Do not write in another agent's checkout.

Naming: `.worktrees/<agent>` (agent name only). Put the topic on the **branch**, e.g. `feat/cursor-…`, `feat/claude-…`.

## Worktrees

| Path | Branch | Owner |
|------|--------|--------|
| repo root (`foxcast/`) | `main` | Integration only; short hotfixes OK |
| `.worktrees/cursor` | `feat/structure-cli-wiring` | Cursor — CLI / docs / structure |
| `.worktrees/claude-stage1` | `feat/stage1-fiscal-1a6` | Claude Code — Stage 1 claim/dossier lane |
| `.worktrees/antigravity-scaffold` | `chore/package-skeletons` | Antigravity — Stage 0.5 tests / operator docs |
| `.worktrees/devin-stage2-stubs` | `feat/devin-stage2-stubs` | Devin — Stage 2 stubs |

`.worktrees/` is gitignored. One branch per worktree; never check out the same branch in two places.

## Ownership

| Lane | Owns | Does not touch |
|------|------|----------------|
| **Claude Code** | `claims/*`, `sec_ingest.py`, dossier assembly / section schema (row 1d), fiscal-period / gate runners, Stage 1A.6+ | Bulk rewrites of `cli/app.py`, `sim/*`, inventing scorecard pass lines |
| **Cursor** | CLI wiring (`ingest`, `check-claims`, doctor), `llm/` + `mcp/` interfaces, `docs/MODELS.md` accuracy, this file | Rewriting `checker.py` / `planted.py` / numbers rule |
| **Antigravity** (optional) | Boilerplate, operator docs, Stage 0.5 test hardening | Scorecard, ledger, claims truth path |
| **Devin** | Stage 2 sim stubs + contract tests | `claims/*`, dossier assembly, scorecard pass lines |
| **Travis** | Merge to `main`, scorecard wording, ship / don't ship | — |

Shared lock files (`pyproject.toml`, `uv.lock`, `SCORECARD.md`, `scorecard.toml`): **one owner per PR**; others rebase.

## Protocol

1. Start from green `main` (CI green on the tip you branch from).
2. Work only in your worktree; `uv sync` there; do not share `.pytest_tmp` or DuckDB scratch across trees.
3. Open a short PR early so CI runs on the branch.
4. Rebase onto `main` after another lane merges.
5. Prefer merge order: truth/library → CLI wiring → stubs/docs.
6. If two agents need the same file, **stop and serialize**.

## Stage 1A status (as of 2026-11-12)

- **1A.1–1A.5** on `main`.
- Fiscal-period labels + archive guard on `main` (PR #2).
- Stage 0.5 test hardening on `main` (PR #1).
- `foxcast ingest` uses `settings.archive_dir` via `SourceArchive.outside_git` (not under the data repo).
- **Next Claude:** Stage **1A.6** dossier builder in a fresh session on `.worktrees/claude-stage1` (new branch off `main`, e.g. `feat/claude-1a6-dossier`). 10-K section splitting waits on `edgartools` until **2026-11-16**.

## Honesty reminders

- Scorecard pass lines change only with a dated changelog entry.
- The model never writes a number; code inserts figures from fact IDs.
- No lookahead; no scorecard number from a notebook.
- Ship code, never data.
