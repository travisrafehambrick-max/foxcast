# foxcast

**Sourced stock dossiers and return forecasts that have to prove themselves in public.**

[![CI](https://github.com/travisrafehambrick-max/foxcast/actions/workflows/ci.yml/badge.svg)](https://github.com/travisrafehambrick-max/foxcast/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)](pyproject.toml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
![Scorecard](https://img.shields.io/badge/scorecard-not%20yet%20measured-lightgrey)

> **Research, not investment advice.** foxcast produces research dossiers and probability forecasts for educational and research purposes only. Nothing it outputs is financial, investment or trading advice, or a recommendation to buy or sell any security. It places no orders.

## What it does

Every fact traced. Every forecast frozen. Every result measured.

- **Sourced dossiers** — point-in-time research dossiers for US stocks; every claim traces to a fact ID and the document it came from
- **Frozen forecasts** — 101-quantile distributions of 5- and 21-day returns, sealed in a tamper-evident ledger before the outcome is known
- **Public scoring** — settled forecasts measured against naive, volatility and option-implied baselines; the results live in [`SCORECARD.md`](SCORECARD.md)

The work is staged so trust is earned in order: **Stage 1** — a dossier where every claim traces to a source. **Stage 2** — forecasts whose error bars prove honest when measured. **Stage 3** — a research layer whose picks are themselves scored.

## Status

Early development. What works today:

- The live record: on-demand snapshots of prices, option chains and analyst views, with numbers-only forecasts frozen in the ledger
- Point-in-time dossiers (`foxcast dossier`) built from the local fact store, no model — the research-claims section arrives in Stage 1B
- The scoring foundation: quantile-form CRPS, volatility-scaled skill, bootstrap intervals — and a gate runner that freezes 20-ticker dossier sets with content hashes

No scorecard row has been measured yet, so no forecast here has a validated track record. The scorecard was committed before any code; every result will be reported as pass, fail or not yet measurable.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12 or 3.13. Tested on Linux, macOS and Windows.

```bash
git clone https://github.com/travisrafehambrick-max/foxcast
cd foxcast
uv sync
uv run foxcast init        # writes ~/.foxcast/foxcast.toml; keys go in env vars
uv run foxcast doctor      # checks Python, config, keys, disk space and sources
```

Two environment variables:

- `FOXCAST_SEC_CONTACT="Your Name you@example.com"` — required before any SEC data is pulled
- `FOXCAST_FRED_API_KEY` — optional; macro data is skipped without it

```bash
uv run foxcast watchlist --universe constituents.csv --seed 1 --sector "Energy"
uv run foxcast follow ABCD        # optional: watch a hand-picked ticker (not scored)
uv run foxcast run                # on demand, during market hours
uv run foxcast ingest MSFT --as-of 2025-01-15    # fill the fact store from SEC
uv run foxcast dossier MSFT --as-of 2025-01-15   # build the sourced dossier
uv run foxcast verify-ledger --ledger ~/.foxcast/data/ledger.jsonl
```

`constituents.csv` is a list of S&P 500 members with `Symbol`, `GICS Sector` and `GICS Sub-Industry` columns — for example the constituents table in Wikipedia's List of S&P 500 companies, exported as CSV. The draw fixes the pool before anyone sees which tickers come out, and records the universe definition and seed so it can be reproduced. You supply the file; none is shipped.

Everything foxcast writes lives under `~/.foxcast/` — config, snapshots, the ledger, per-ticker fact stores, and dossiers.

## Data

foxcast ships code, never data. Each user fetches their own data under each source's own terms. All sources are free: Yahoo Finance (prices, option chains, analyst views), SEC EDGAR (filings — with your contact string), FRED (macro, optional free key).

## Documentation

| File | What it is |
|------|------------|
| [`SCORECARD.md`](SCORECARD.md) | The contract: what the project must prove and how it is measured |
| [`docs/MODELS.md`](docs/MODELS.md) | The forecast inventory and each model's status |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Dev setup and test commands |
| [`AGENTS.md`](AGENTS.md) | How work is split across coding agents |

## Licence

Apache-2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
