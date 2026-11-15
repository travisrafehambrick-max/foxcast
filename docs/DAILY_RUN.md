# Daily forecast and snapshot run (`foxcast run` / `foxcast forecast`)

> **Research, not investment advice.** foxcast produces research dossiers and probability forecasts for educational and research purposes only. Nothing it outputs is financial, investment or trading advice, or a recommendation to buy or sell any security. It places no orders.

This operator note covers the day-to-day execution path for `foxcast run`, `foxcast snapshot`, and `foxcast forecast`, including same-day rerun semantics, partial-failure handling, and ledger maintenance. For initial setup and watchlist creation, see [`STAGE0_5_LIVE_RUN.md`](STAGE0_5_LIVE_RUN.md).

## Commands

### 1. Combined daily path (`foxcast run`)

```bash
uv run foxcast run
```

- **When to run:** On demand while the New York Stock Exchange (`XNYS`) is open.
- **What it does:**
  1. Captures today's Yahoo Finance prices, option chains, and analyst views for **every** ticker on the watchlist (`random` scored draw plus `followed` tickers) under `<data_dir>/snapshots/<YYYY-MM-DD>/`.
  2. Appends a cadence and health entry to `<data_dir>/snapshots/health.jsonl`.
  3. Freezes the Stage 0.5 numbers-only quantile forecasts into `<data_dir>/ledger.jsonl` for every captured ticker:
     - `naive` (63-day constant volatility, zero expected simple return) at `5` and `21` trading days.
     - `plain_vol` (GJR-GARCH(1,1,1) with Student-t residuals) at `5` and `21` trading days.
     - `option_raw` (raw option-implied risk-neutral distribution via `oipd` at the expiry nearest `21` trading days, within 7 calendar days).

### 2. Separate snapshot and forecast steps (`foxcast snapshot` / `foxcast forecast`)

Use the split commands when you need to inspect a captured snapshot or re-run forecasting for a specific New York date without re-polling Yahoo Finance:

```bash
# Capture today's market snapshot (requires XNYS open unless --force is passed for testing)
uv run foxcast snapshot

# Freeze numbers-only forecasts for today's New York date (or an explicit --day YYYY-MM-DD)
uv run foxcast forecast --numbers-only --day 2026-11-11
```

- Forecasts `naive` and `plain_vol` use **completed sessions only** (price rows dated strictly before `--day`), excluding the intraday snapshot row so variance matches the labelled trading-day horizon.
- Forecast `option_raw` uses the snapshot's current `close` as spot so the option chain quotes and underlying price come from the same capture.

### 3. Ledger verification and torn-tail repair

```bash
uv run foxcast verify-ledger --ledger ~/.foxcast/data/ledger.jsonl
```

If an interrupted process or power loss leaves an incomplete final line (a torn tail), trim it after creating a `.bak` copy:

```bash
uv run foxcast repair-ledger --ledger ~/.foxcast/data/ledger.jsonl
```

`repair-ledger` refuses to modify a ledger that is healthy or broken in the middle of the chain.

## Same-day rerun semantics

- **Snapshots keep the first capture:** Running `foxcast snapshot` or `foxcast run` a second time on the same New York day never overwrites Parquet tables already written that day (`kept from an earlier run today`).
- **Deterministic request IDs:** Each forecast record is keyed by `<day>:<ticker>:<forecast_id>:<horizon_days>` (for example, `2026-11-11:ABC:option_raw:21`).
- **Idempotent when unchanged:** Re-running `foxcast forecast` on the same day with identical inputs and `code_version` returns the existing record without duplicating ledger lines.
- **Conflict-skip when changed:** If a same-day rerun produces a different payload for an existing request ID (for example, after a git commit changes `code_version`), the ledger keeps the first frozen record and logs the request under `skipped` (`...; kept the first record`) rather than aborting the run.

## Exit codes and stop conditions

| Exit code | Condition | Operator action |
|-----------|-----------|-----------------|
| `0` | Snapshot and forecast freeze completed without alarm | Verify ledger with `foxcast verify-ledger`. |
| `1` | Snapshot failure rate exceeded 10% (`report.alarm`), invalid `--day` / config, or broken ledger | In `foxcast run`, forecasts are still frozen for every ticker that was captured before exiting `1`. Inspect `<data_dir>/snapshots/health.jsonl` and provider errors. |
| `2` | Market closed (`XNYS is closed; nothing captured`) | Wait for regular New York market hours. Do not use `--force` for live scored runs. |
