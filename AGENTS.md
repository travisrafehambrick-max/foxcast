# Agent coordination

foxcast is scorecard-first. Parallel agents use **git worktrees** so each agent has its own directory. Do not write in another agent's checkout.

**Worktrees are long-lived; branches are short-lived.**
Naming: `.worktrees/<agent>` (agent name only — never put the topic in the folder).
Each task gets a **new** branch `[agent]-[topic]` cut from green `origin/main` (e.g. `cursor-agents-protocol`, `claude-1a7-mcp`). Never share a branch across worktrees.

## Worktrees

| Path | Between tasks | Owner |
|------|---------------|--------|
| repo root (`foxcast/`) | `main` | Integration only; short hotfixes OK |
| `.worktrees/cursor` | `cursor-idle` (or a live `[agent]-[topic]`) | Cursor — CLI / docs / structure |
| `.worktrees/claude` | `claude-idle` (or a live topic) | Claude Code — Stage 1 claim/dossier lane |
| `.worktrees/antigravity` | `antigravity-idle` (or a live topic) | Antigravity — Stage 0.5 tests / operator docs |
| `.worktrees/devin` | `devin-idle` (or a live topic) | Devin — Stage 2 stubs |

`.worktrees/` is gitignored. One checked-out branch per worktree; never check out the same branch in two places. Between tasks, park on `<agent>-idle` at `origin/main`. When a topic starts, replace that with `git checkout -B <agent>-<topic> origin/main`.

## Ownership

| Lane | Owns | Does not touch |
|------|------|----------------|
| **Claude Code** | `claims/*`, `sec_ingest.py`, dossier assembly / section schema (row 1d), fiscal-period / gate runners, Stage 1A.6+ | Bulk rewrites of `cli/app.py`, `sim/*`, inventing scorecard pass lines |
| **Cursor** | CLI wiring (`ingest`, `dossier`, `check-claims`, doctor), `llm/` + `mcp/` interfaces, `docs/MODELS.md` accuracy, this file | Rewriting `checker.py` / `planted.py` / numbers rule |
| **Antigravity** (optional) | Boilerplate, operator docs, Stage 0.5 test hardening | Scorecard, ledger, claims truth path |
| **Devin** | Stage 2 sim stubs + contract tests | `claims/*`, dossier assembly, scorecard pass lines |
| **Travis** | Merge to `main`, scorecard wording, ship / don't ship | — |

Shared lock files (`pyproject.toml`, `uv.lock`, `SCORECARD.md`, `scorecard.toml`): **one owner per PR**; others rebase onto `main` before continuing.

## Protocol

1. **Start every task from green `main`.** In your worktree:
   ```powershell
   git fetch origin
   git checkout -B <agent>-<topic> origin/main
   ```
   `-B` recreates the branch tip at `origin/main` so you are never behind. Do not rename an old merged topic and keep working on it.
2. Work only in your worktree; `uv sync` there; do not share `.pytest_tmp` or DuckDB scratch across trees.
3. Open a short PR early so CI runs on the branch.
4. After your PR merges: delete the remote topic branch (`git push origin --delete <agent>-<topic>`), park the worktree on `<agent>-idle` at `origin/main` (`git checkout -B <agent>-idle origin/main`), then start the **next** task with step 1 and a **new** topic name. If another lane merges while you are idle, fast-forward idle the same way before starting work.
5. Prefer merge order: truth/library → CLI wiring → stubs/docs.
6. If two agents need the same file, **stop and serialize**.

### Do not

- Treat `[agent]-topic` as a permanent agent identity branch.
- Start new work on a tip that already merged to `main` without recreating from `origin/main`.
- Check out the same branch in two worktrees.
- Leave merged topic remotes hanging; delete them after merge.

## Stage 1A status (as of 2026-11-13)

- **1A.1–1A.5** on `main`.
- **1A.6** dossier builder on `main` (PR #3).
- Fiscal-period labels + archive guard on `main` (PR #2).
- Stage 0.5 test hardening on `main` (PR #1).
- `foxcast ingest` uses `settings.archive_dir` via `SourceArchive.outside_git` (not under the data repo).
- `foxcast dossier` wires `build_dossier` / `write_dossier` (no-model path; research claims in 1A.7) (PR #4).
- **Next:** Stage **1A.7** MCP tool server + cost meter. 10-K section splitting waits on `edgartools` until **2026-11-16**.

## Honesty reminders

- Scorecard pass lines change only with a dated changelog entry.
- The model never writes a number; code inserts figures from fact IDs.
- No lookahead; no scorecard number from a notebook.
- Ship code, never data.
