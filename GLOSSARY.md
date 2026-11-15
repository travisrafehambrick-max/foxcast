# foxcast

Sourced stock dossiers and frozen return forecasts, measured in public. This file is the
canonical vocabulary: what the words mean here, and which near-synonyms are deliberately
not used.

## The dossier

**Dossier**:
A point-in-time research document for one company, built as of a chosen date from the fact
store and source archive. Nineteen fixed sections; every figure in it is inserted by code
from a fact or passage ID.
_Avoid_: report, summary

**Claim**:
A sentence the model submits for a dossier section, citing the fact or passage IDs behind
it. Its type is the model's own label — `fact`, `estimate`, `opinion` or `forecast` — and
an estimate or forecast type must carry a probability.

**Counted claim**:
A submitted claim that cites at least one fact or passage ID. The denominator of the
verification rate, whatever type the model gave it — including claims the numbers rule
refused.

**Verified claim**:
A claim that survives the check: every figure matches its fact, every fact matches the
content-hashed source copy saved at fetch time, and the claim's own words agree with its
cited facts. Only verified claims are shown.

**Withheld claim**:
A claim that fails any check. It stays in the counted total but is never shown; its reason
is redacted in the report.
_Avoid_: rejected, failed

**Numbers rule**:
The model never writes a number. Every figure in a claim is a placeholder that code fills
from the cited fact ID, then reads back and compares.

**Checker**:
The code path that renders, reviews and verifies claims and dossier sections. The same
path backs `foxcast dossier` and `foxcast check-claims`.

**Planted claim**:
A deliberately false claim built by code from a verified gate-run claim — altered number,
altered quote, or wrong period, unit, company or metric — run through the same checker to
measure its false-accept rate.

**Filled / unavailable / gap**:
The three dossier completeness states (row 1d). A section is filled or marked unavailable
with its reason; a filled section may still list gaps — named items it could not fill,
each with a reason. Nothing is silently missing.

**Bear case**:
The falsifiable-counterargument section (row 1f): at least three falsifiable claims, each
with a source and what would prove it wrong. Written by the red team.

## Facts and sources

**Fact**:
One value with full provenance: the period it covers, when it became public, when foxcast
fetched it, and the content hash of the source copy it came from. A `f_` ID. Kinds:
number fact, text fact, derived fact.

**Passage**:
A verbatim excerpt of a filing stored as its own citable object, a `p_` ID. Verified only
when it matches the archived span exactly.

**Derived fact**:
A value code computes from other facts — a ratio, difference or growth — recording the
method, the comparison basis and the input fact IDs, so the model never does arithmetic.
Still a `f_` ID; traceable back to the fetched facts it came from.

**Fact store**:
The point-in-time store of facts and passages, queried through `as_of` views on
`public_on`.

**Source archive**:
The content-addressed store of raw fetched payloads, keyed by their hash, saved at fetch
time. Lives outside any git work tree. A citation is verified against this copy, never a
re-fetch.

**Ingest**:
Fetching a company's SEC record into the fact store and archive as of a date; anything
made public after that date is discarded.
_Avoid_: import, sync

**SEC record**:
The set of SEC artefacts ingest fetches: the submissions index, 10-K / 10-Q / 8-K filing
documents, Form 4 filings, and XBRL concept figures.

**Fiscal label**:
The fiscal year and period (Q1–Q4, H1, H2, FY) an XBRL fact carries from its source copy.
Rebuilt and checked with the value, never part of its ID.

## Time

**Period**:
The span a fact is about: `period_start` to `period_end`.

**Filed**:
The SEC filing date. Public availability lags it: a filing dated D is public from D + 1.

**public_on**:
The first day the public could have known a fact. Every point-in-time query filters on it.

**fetched_at**:
When foxcast fetched a fact. Provenance only, never a filter — what a query may see is
governed by `public_on`.

**As-of**:
The calendar date an operator passes to `--as-of`. The command sees only facts with
`public_on` on or before it.

**Point-in-time**:
The discipline that every dossier, claim check and forecast sees only what was knowable
on its as-of date.

**Lookahead**:
Using input that was not public on the forecast or as-of date. The bug class the
point-in-time discipline exists to prevent; row 2d tests for it.
_Avoid_: peeking (the scorecard's plain name for the same thing is fine in prose)

## Forecasts and the ledger

**Forecast**:
A producer of a return distribution — a 101-quantile distribution of 5- and 21-day
returns — sealed before the outcome is known. The numbered family 1–7. A claim's
`forecast` type is only the model's label for a forward-looking sentence, not this.
_Avoid_: model (that word names the language model), prediction

**Benchmark**:
A comparison forecast outside the numbered family — B, the simple implied-volatility
benchmark — and the named baselines rows compare skill against.

**Model**:
The language model or backend that reads facts and writes claims (row 4b: any model
works — cloud API, subscription agent, local).
_Avoid_: forecast

**Headline**:
The forecast presented as the answer: the blend (forecast 6), provided row 2f passes;
otherwise the better ingredient becomes the headline.

**Blend**:
Forecast 6: an equal-weight combination of the event-aware and corrected option-implied
forecasts. The combination method is fixed by proving experiment 5.

**Event**:
A scheduled corporate occurrence on the event calendar — confirmed earnings dates, plus
dividends and splits when the calendar source provides them. Not macro dates, not
unscheduled news.

**Event-aware**:
Forecast 3: the plain volatility model plus the event calendar and measured event
effects. The history-based forecast.

**Snapshot**:
The market-data capture at run time: prices, option chains, analyst views. Wall-clock;
its last price row may be intraday.

**Run**:
One execution of `foxcast run`: a snapshot of the whole watchlist followed by forecasts
frozen in the ledger. Specialisations: a **gate run** is judged against the gate set, a
**final run** is the single 2023–2026 test, a **live run** happens in live operation.

**Watchlist**:
The tickers a run covers: the seeded draw from the declared universe plus every followed
ticker.

**Declared universe**:
The S&P 500 member list (a fixed CSV and its hash) from which seeded draws are taken.
Fixes the pool before anyone sees which tickers come out.

**Gate set**:
The fixed 20 tickers rows 1a–1d and 4c are judged on; drawn with a recorded seed from the
declared universe, disjoint from the watchlist and audit tickers.

**Audit tickers**:
AAPL, NVDA, MSFT, GOOGL, AMZN — the five dossiers a person spot-checks for row 1e.
Outside the declared universe.

**Followed ticker**:
A hand-picked ticker added to the watchlist. Snapshotted and forecast but not scored.

**Ledger**:
The append-only, hash-chained record every forecast is frozen into — one JSON line per
record, each hash covering its contents and the previous record's hash. Tamper-evident;
public digests and independent timestamps arrive in Stage 3.

**Freeze**:
Sealing a forecast as a ledger record, with its resolution rule, before the outcome is
knowable. Repeat-safe by request ID; a rerun with different contents is refused.

**Settle**:
Writing a forecast's outcome once its horizon has elapsed, per the resolution rule.

**Resolve**:
What a settled forecast's outcome is: the last traded price, the takeover consideration,
or the first close after a halt.

**Void**:
The only remaining outcome: no price exists. Void is a reported rate beside every score,
never a deleted record — voiding delistings would flatter tail coverage.

**Resolution rule**:
The written rule a forecast record carries, fixed before the forecast is made: what it
resolves to on delisting, takeover or halt, and when it is void.

## Scoring and the contract

**Scorecard**:
`SCORECARD.md` — the contract of what must be proven and how, committed before any code.
Each row is reported pass, fail or not yet measurable; never blank, never "mostly".

**Horizon**:
The forecast length in trading days: 5 or 21.

**Quantile grid**:
The 101 fixed quantiles a forecast is expressed in; the form CRPS is scored over.

**CRPS / volatility-scaled skill**:
The scoring measure. Skill is scaled by volatility so volatile stocks do not dominate;
raw CRPS and a tail-weighted score are reported beside it.

**Coverage**:
How often the outcome lands inside a stated interval. Reported before and after conformal
recalibration; the pass line applies to the figure before.

**Conformal recalibration**:
The interval correction applied identically to every compared forecast, fitted only on
already-settled outcomes — so it lags one horizon.

**Market state**:
Calm, normal or stressed, defined only from what was known on the forecast day. The
thresholds are fixed before the row 3d power simulation.

**Live / historical**:
The two evidence regimes. Live forecasts are made after the model's training cutoff and
frozen in the ledger — the only kind that counts for skill claims about the research
layer. Historical back-tests are fair game for the numbers-only forecasts.

**Walk-forward**:
The historical test pattern: refit every fitted piece from data before each forecast
date, then forecast forward, with at least one horizon between fitting and testing.

**Proving experiment**:
A test whose hypothesis, measure and decision rule are committed before it runs; counted
in the run registry.

**Run registry**:
The counted record of every back-test run; honesty rule 5's "count the tries" reads from
it.

**Event class**:
A category of event the model gives a probability for (row 3d). The class list is
deliberately unfixed until the Stage 3 power simulation locks it.

**Research layer**:
The model plus the locked-down MCP tool surface through which it reads facts and
passages and submits claims and event probabilities. The dossier and forecast 7 are
products of it, not parts of it.

**Stage**:
The ordered trust-building phases (0.5, 1, 2, 3, 4). Each must pass its scorecard rows
before the next starts.
