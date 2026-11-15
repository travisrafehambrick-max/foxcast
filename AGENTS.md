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
| **Cursor** | CLI wiring (`ingest`, `dossier`, `check-claims`, doctor), `llm/` + `mcp/` interfaces, `docs/MODELS.md` accuracy | Rewriting `checker.py` / `planted.py` / numbers rule |
| **Antigravity** (optional) | Boilerplate, operator docs, Stage 0.5 test hardening | Scorecard, ledger, claims truth path |
| **Devin** | Stage 2 sim stubs + contract tests, this file | `claims/*`, dossier assembly, scorecard pass lines |
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

## Status (as of 2026-11-14)

**Stage 1A complete.** 1A.1–1A.7 on `main`: claims/checker/numbers-rule hardening (PR #1), fiscal-period labels + archive guard (PR #2), dossier builder (PR #3), `dossier` CLI wiring (PR #4), `check-claims` CLI (PR #7), locked-down MCP tool server + cost meter + call-log replay (PR #8), operator guide (PR #9), gate dossier runner freezing the 20-ticker set with content hashes (PR #14). `foxcast ingest` uses `settings.archive_dir` via `SourceArchive.outside_git` (not under the data repo).

**Stage 1B started.** LLM backend interface for research claims + hardened audit trail (PR #18); research-claims dossier section on `main`, reported unavailable until a backend runs it (PR #20).

**Stage 2 started.** Quantile grid + forecasts 1 (naive), 2 (plain vol), 4 (option raw) built at Stage 0.5; contract tests + honest stubs for forecasts 3, 5, 6 and B on `main` (PR #11). Scoring landed: quantile-form CRPS, tail-weighted score, volatility-scaled skill, block-bootstrap CI (PR #13). Still "not built": `event_aware` (needs an event calendar), `option_corrected` (needs a bias-correction fitter + chain history), blend combination method (awaits proving experiment 5), walk-forward harness, lookahead tests (row 2d).

Repo docs for agents: `docs/agents/` skills config (PR #19); `GLOSSARY.md` + `docs/adr/` (PR #21) — use glossary vocabulary in output.

- **Next:** walk-forward harness + lookahead tests (row 2d); a backend wired for live research claims; 10-K section splitting unblocks with `edgartools` on **2026-11-16**.

## Honesty reminders

- Scorecard pass lines change only with a dated changelog entry.
- The model never writes a number; code inserts figures from fact IDs.
- No lookahead; no scorecard number from a notebook.
- Ship code, never data.

## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues (`travisrafehambrick-max/foxcast`); skills use the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default canonical labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
