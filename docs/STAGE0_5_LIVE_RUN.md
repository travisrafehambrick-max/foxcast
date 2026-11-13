# Stage 0.5 live gate run

> **Research, not investment advice.** foxcast produces research dossiers and probability forecasts for educational and research purposes only. Nothing it outputs is financial, investment or trading advice, or a recommendation to buy or sell any security. It places no orders.

This guide describes how to run the Stage 0.5 live gate run.

Stage 0.5 establishes the live record. It captures an on-demand snapshot of market data and freezes numbers-only forecasts into the ledger. Stage 0.5 delivers the core hash-chain ledger; public digests and independent timestamps are Stage 3 (scorecard row 3a).

**Research dossiers are not part of the Stage 0.5 live run.** Stage 0.5 produces only the live record and numbers-only forecasts (models 1, 2, and 4; naive constant volatility, plain volatility model, and raw option-implied). For Stage 1A SEC ingestion (`foxcast ingest`), no-model point-in-time dossier assembly (`foxcast dossier`), and claim verification (`foxcast check-claims`), see [`STAGE1A.md`](STAGE1A.md). Language-model research step integration and research-layer evaluation (scorecard rows 1a–1f, 3b, and 3d) arrive in Stage 1A.7 and later stages.

## Prerequisites

- **uv:** [uv](https://docs.astral.sh/uv/) installed.
- **Python:** Python 3.12 or 3.13.
- **SEC contact:** Set `FOXCAST_SEC_CONTACT="Your Name you@example.com"` in your environment. The SEC requires every automated client to identify itself.
- **Keys in environment, not config:** Set any API keys in environment variables, never in `foxcast.toml` or committed files. Note that `FOXCAST_FRED_API_KEY` is optional for Stage 0.5 (`foxcast doctor` emits a `WARN` if unset; macro data remains unavailable).
- **Disk space:** At least 5 GB of free disk space for the local data directory.

## Command sequence

Run these commands in order from the repository root:

1. **Sync dependencies**
   ```bash
   uv sync
   ```
   *Why:* Installs project dependencies and CLI tools into the local virtual environment.

2. **Initialize configuration**
   ```bash
   uv run foxcast init
   ```
   *Why:* Writes a starter config file (`~/.foxcast/foxcast.toml`) with default storage paths. Keys go in environment variables, not here.

3. **Run diagnostics**
   ```bash
   uv run foxcast doctor
   ```
   *Why:* Pre-flight check that inspects Python version, config, keys, disk space, and SEC endpoint reachability before any run.

4. **Draw the scored watchlist**
   ```bash
   uv run foxcast watchlist --universe constituents.csv --seed 1 --sector "Energy"
   ```
   *Why:* Draws the scored watchlist at random from a declared universe with a recorded seed. The universe definition, member count, and seed are recorded so the draw is reproducible. Every live run covers the whole scored watchlist, never a chosen few.

5. **(Optional) Follow a hand-picked ticker**
   ```bash
   uv run foxcast follow ABCD
   ```
   *Why:* Watches a specific ticker of interest. Followed tickers are snapshotted and forecasted alongside the watchlist, but are tagged `sample: followed` and are **not scored** on the scorecard.

6. **Execute live run**
   ```bash
   uv run foxcast run
   ```
   *Why:* Executed on demand during New York market hours. Pulls today's prices, option chains, and analyst views, and freezes numbers-only forecasts for the whole watchlist into the tamper-evident ledger.

7. **Verify ledger integrity**
   ```bash
   uv run foxcast verify-ledger --ledger ~/.foxcast/data/ledger.jsonl
   ```
   *Why:* Verifies the ledger's SHA-256 hash chain to ensure no record has been edited, backdated, deleted, or reordered.

## Honesty rules

These rules from `CONTRIBUTING.md` and `SCORECARD.md` apply to this run:

- **Ship code, never data:** Do not commit fetched market data or anything under `data/`. Each user fetches their own data under each source's terms. Test fixtures must have keys scrubbed.
- **No scorecard numbers from notebooks:** Notebooks are for exploring only. Every figure that counts toward the scorecard must be produced by a `foxcast` CLI command.
- **Followed vs random/scored sample:** Every live record carries a sample tag: `random` for the scored watchlist drawn from the declared universe, or `followed` for hand-picked tickers. Followed tickers are excluded from every scorecard row so personal interest never shapes the scored sample.

## Stop conditions

Halt the run immediately if any of these conditions occur:

1. **`foxcast doctor` reports FAIL:** Any failing check (e.g., unsupported Python version, invalid config, missing or malformed `FOXCAST_SEC_CONTACT`, low disk space, or unreachable SEC endpoint) causes `doctor` to exit with an error. Do not proceed with a run until all checks pass. (A missing optional FRED key reports `WARN`, which does not fail `doctor`.)
2. **Market closed / run refused:** `foxcast run` requires the New York market to be open. If the market is closed, `MarketClosedError` is raised, nothing is captured or frozen, and the command exits with code 2. Do not bypass market-hours checks for live gate runs.
3. **Ledger verification fails:** If `foxcast verify-ledger` reports `Ledger broken`, the hash chain failed verification. Any edit, deletion, or reordering breaks the chain. If an interrupted write left a torn tail, `foxcast repair-ledger` can make a clean backup and trim the torn line; if the chain is otherwise broken, stop and investigate before proceeding.
