# foxcast

> **Research, not investment advice.** foxcast produces research dossiers and probability forecasts for educational and research purposes only. Nothing it outputs is financial, investment or trading advice, or a recommendation to buy or sell any security. It places no orders.

foxcast builds a sourced research dossier for a US stock, in which every fact can be traced to where it came from, and a forecast distribution of its return over 5 and 21 trading days. Every forecast is frozen in a tamper-evident ledger and scored when its horizon closes, against simple baselines and the options market. A public scorecard reports how good the forecasts have been.

## Status

Early development. What works today is the live record: a daily snapshot of prices, option chains and analyst views, and numbers-only forecasts frozen in a tamper-evident ledger. There are no research dossiers yet, and none of the scorecard's rows has been measured, so no forecast here has a validated track record.

The scorecard in [`SCORECARD.md`](SCORECARD.md) was committed before any code. It states what the project must prove and how, and every result will be reported against it as pass, fail or not yet measurable.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12 or 3.13.

```
git clone https://github.com/travisrafehambrick-max/foxcast
cd foxcast
uv sync
uv run foxcast init                 # writes a starter config; keys go in environment variables
uv run foxcast doctor               # checks Python, config, keys, disk space and sources
uv run foxcast watchlist --universe constituents.csv --seed 1 --sector "Energy"
uv run foxcast follow ABCD          # optional: watch a hand-picked ticker (not scored)
uv run foxcast snapshot             # during market hours
uv run foxcast forecast             # freezes the numbers-only forecasts in the ledger
uv run foxcast verify-ledger --ledger ~/.foxcast/data/ledger.jsonl
```

Set `FOXCAST_SEC_CONTACT="Your Name you@example.com"` before using SEC data; SEC asks every client to identify itself.

## Data

foxcast ships code, never data. Each user fetches their own data under each source's own terms.

## Licence

Apache-2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
