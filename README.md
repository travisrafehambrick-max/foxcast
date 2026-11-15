# foxcast

> **Research, not investment advice.** foxcast produces research dossiers and probability forecasts for educational and research purposes only. Nothing it outputs is financial, investment or trading advice, or a recommendation to buy or sell any security. It places no orders.

foxcast builds a sourced research dossier for a US stock, in which every fact can be traced to where it came from, and a forecast distribution of its return over 5 and 21 trading days. Every forecast is frozen in a tamper-evident ledger and scored when its horizon closes, against simple baselines and the options market. A public scorecard reports how good the forecasts have been.

The work is staged so trust is earned in order: first a dossier where every claim traces to a source (Stage 1), then forecasts whose error bars prove honest when measured (Stage 2), then a research layer whose picks are themselves scored (Stage 3).

## Status

Early development. What works today:

- The live record: on-demand snapshots of prices, option chains and analyst views, with numbers-only forecasts frozen in a tamper-evident ledger.
- Point-in-time research dossiers (`foxcast dossier`): sourced sections built from the local fact store, without a model. The research-claims section arrives with the model backend in Stage 1B.
- The scoring foundation: quantile-form CRPS, volatility-scaled skill and bootstrap intervals. None of the scorecard's rows has been measured yet, so no forecast here has a validated track record.

The scorecard in [`SCORECARD.md`](SCORECARD.md) was committed before any code. It states what the project must prove and how, and every result will be reported against it as pass, fail or not yet measurable.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12 or 3.13. Tested on Linux, macOS and Windows.

```
git clone https://github.com/travisrafehambrick-max/foxcast
cd foxcast
uv sync
uv run foxcast init                 # writes ~/.foxcast/foxcast.toml; keys go in env vars
uv run foxcast doctor               # checks Python, config, keys, disk space and sources
uv run foxcast watchlist --universe constituents.csv --seed 1 --sector "Energy"
uv run foxcast follow ABCD          # optional: watch a hand-picked ticker (not scored)
uv run foxcast run                  # on demand, during market hours: pull today's data and
                                    # freeze forecasts for the whole watchlist
uv run foxcast ingest MSFT --as-of 2025-01-15    # fill the local fact store from SEC
uv run foxcast dossier MSFT --as-of 2025-01-15   # build the sourced dossier from it
uv run foxcast verify-ledger --ledger ~/.foxcast/data/ledger.jsonl
```

Two environment variables:

- `FOXCAST_SEC_CONTACT="Your Name you@example.com"` — required before any SEC data is pulled; SEC asks every client to identify itself. `doctor` fails without it.
- `FOXCAST_FRED_API_KEY` — optional; macro data is skipped without it.

`constituents.csv` is a list of S&P 500 members with `Symbol`, `GICS Sector` and `GICS Sub-Industry` columns — for example the constituents table in Wikipedia's List of S&P 500 companies, exported as CSV. The draw fixes the pool before anyone sees which tickers come out, and records the universe definition and seed so it can be reproduced. You supply the file; none is shipped.

Everything foxcast writes lives under `~/.foxcast/` — config, snapshots, the ledger, and per-ticker fact stores.

## Data

foxcast ships code, never data. Each user fetches their own data under each source's own terms. The sources it uses are free: Yahoo Finance for prices, option chains and analyst views; SEC EDGAR for filings and submissions (with your contact string); FRED for macro series (optional free key).

## Contributing and internals

`CONTRIBUTING.md` covers the development setup and test commands. [`SCORECARD.md`](SCORECARD.md) is the contract the project is measured against. [`AGENTS.md`](AGENTS.md) describes how work is split across coding agents — useful context for the layout even if you are not one.

## Licence

Apache-2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
