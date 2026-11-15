# foxcast

**Sourced stock dossiers and return forecasts that have to prove themselves in public.**

[![CI](https://github.com/travisrafehambrick-max/foxcast/actions/workflows/ci.yml/badge.svg)](https://github.com/travisrafehambrick-max/foxcast/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)](pyproject.toml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
![Scorecard](https://img.shields.io/badge/scorecard-not%20yet%20measured-lightgrey)

> **Research, not investment advice.** foxcast produces research dossiers and probability forecasts for educational and research purposes only. Nothing it outputs is financial, investment or trading advice, or a recommendation to buy or sell any security. It places no orders.

## What it does

foxcast builds research dossiers for US stocks and forecasts short-horizon returns — then publishes how good those forecasts actually are.

- **Sourced dossiers** — every claim in a dossier traces to a fact ID and the document it came from
- **Frozen forecasts** — 101-quantile distributions of 5- and 21-day returns, sealed in a tamper-evident ledger before the outcome is known
- **Public scoring** — settled forecasts are measured against naive, volatility and option-implied baselines; the results live in [`SCORECARD.md`](SCORECARD.md)

The stages build trust in order: **Stage 1** proves every claim can be traced to a source. **Stage 2** proves the error bars are honest. **Stage 3** tests whether a research layer's picks add skill.

## Status

Early development. Working today:

- On-demand snapshots of prices, option chains and analyst views, with numbers-only forecasts frozen in the ledger
- Point-in-time dossiers (`foxcast dossier`) built from the local fact store, no model — the research-claims section is on `main` but stays "unavailable" until a backend runs it
- Quantile-form CRPS, volatility-scaled skill and bootstrap intervals — plus a gate runner that freezes 20-ticker dossier sets with content hashes

No scorecard row has been measured yet, so no forecast has a validated track record. The scorecard was committed before any code, and every result is reported as pass, fail or not yet measurable.

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
- `FOXCAST_FRED_API_KEY` — optional; without it, macro data is skipped

```bash
uv run foxcast watchlist --universe constituents.csv --seed 1 --sector "Energy"
uv run foxcast follow ABCD        # optional: watch a hand-picked ticker (not scored)
uv run foxcast run                # on demand, during market hours
uv run foxcast ingest MSFT --as-of 2025-01-15    # fill the fact store from SEC
uv run foxcast dossier MSFT --as-of 2025-01-15   # build the sourced dossier
uv run foxcast verify-ledger --ledger ~/.foxcast/data/ledger.jsonl
```

`constituents.csv` is a list of S&P 500 members with `Symbol`, `GICS Sector` and `GICS Sub-Industry` columns — the Wikipedia constituents table exported as CSV works. The draw fixes the pool before anyone sees which tickers come out, and records the universe and seed so it can be reproduced. You supply the file; foxcast does not ship one.

Everything foxcast writes lives under `~/.foxcast/`: config, snapshots, the ledger, per-ticker fact stores and dossiers.

## Data

foxcast ships code, never data. You fetch your own data under each source's terms. All sources are free: Yahoo Finance (prices, option chains, analyst views), SEC EDGAR (filings — needs your contact string), FRED (macro, optional free key).

## Documentation

| File | What it is |
|------|------------|
| [`SCORECARD.md`](SCORECARD.md) | The contract: what the project must prove and how it is measured |
| [`GLOSSARY.md`](GLOSSARY.md) | The project's vocabulary, settled |
| [`docs/MODELS.md`](docs/MODELS.md) | The forecast inventory and each model's status |
| [`docs/adr/`](docs/adr/) | Decisions worth remembering, and why |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Dev setup and test commands |
| [`AGENTS.md`](AGENTS.md) | How work is split across coding agents |

## Licence

Apache-2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
