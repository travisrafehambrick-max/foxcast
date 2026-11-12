# foxcast scorecard

This file is committed before any code (honesty rule 1). A pass line changes only with a dated entry in the change log at the bottom saying why. Copied from the approved brief on 2026-11-11.

## What success looks like

**In one sentence:** a stranger installs it for free, gets a dossier in which every fact can be traced, gets a forecast with honest error bars, and can read a scorecard that tells the truth about how good those forecasts have been.

**Two kinds of success, kept apart.** Trust, calibration and usability are in our control, so they have hard pass lines. Beating the market is not in our control, so success there is a *true, checkable answer*, whichever way it comes out.

**Not success:** a persuasive report · a price target · a high hit rate from agents "predicting" history the model has already read · the number of agents or features · GitHub stars.

### The scorecard

Each stage must be useful on its own and must pass before the next one starts. Nothing here is judged by the model that did the work.

| # | Claim we want to be able to make | Measured by | Pass line | Measurable |
|---|---|---|---|---|
| **1. Trust the dossier** | | | | |
| 1a | No fact without a source | Code check at build time | 0 uncited factual claims; the run fails otherwise | Stage 1 |
| 1b | The model never writes a number | Code check: every figure is inserted from a fetched fact ID | 100% | Stage 1 |
| 1c | Sources say what we say they say | Citation checker finds the figure or quote in the content-hashed copy of each source saved at fetch time, on a fixed 20-ticker set | ≥ 98% verified; the rest withheld, not shown. Live re-fetch rate reported separately | Stage 1 |
| 1d | Nothing is silently missing | Every section is either filled or marked "unavailable: reason" | 0 silent gaps | Stage 1 |
| 1e | It holds up to a person | You spot-check 100 claims drawn by a seeded sampler, 20 from each of 5 fixed audit tickers: AAPL, NVDA, MSFT, GOOGL, AMZN. Each check asks whether the claim uses the *right* fact: right metric, period and unit, which code writes from the fact's own record. Method, rubric and seed are under "How rows 1c and 1e are measured" | The upper 95% bound on the error rate is below 5%; and it told you at least one true thing you did not know (change AV) | Stage 1 |
| 1f | The bear case is real | Red-team section | ≥ 3 falsifiable claims, each with a source and a "what would prove this wrong" | Stage 1 |
| **2. Trust the error bars** (numbers only, no language model, so history is fair game) | | | | |
| 2a | The simulator is correct | Tests against closed-form results, including `arch`'s exact multi-step variance for the volatility-model path (change BH) | All pass | Stage 2 |
| 2b | Intervals mean what they say | Walk-forward test on non-overlapping 21-day windows, **every** S&P 500 member as of each date, delisted stocks included and resolved to their last traded price or takeover consideration (change AU). Tuned on 2014–2022 only; the final test is run once on 2023–2026, together with rows 2c, 2e and 2f in a single recorded run (change AS). The effective sample size is reported, and the 99% result is labelled "low power" (change BB). Coverage is reported **before** and after conformal recalibration, and the pass line applies to the figure before (change AX). Coverage is also reported for calm, normal and stressed markets, with each market state defined only from what was known on the forecast day. Interval width and calibration charts are reported beside coverage. | On the final test: coverage of the 50%, 80%, 95% and 99% intervals is statistically consistent with those figures (intervals from resampling whole dates); the 80% and 50% intervals land inside 75–85% and 45–55% | Stage 2 |
| 2c | It beats simpler forecasts | CRPS against two baselines on the same test: constant volatility (forecast 1) and the plain volatility model with no event handling (forecast 2). Scored by volatility-scaled CRPS skill, so volatile stocks do not dominate; raw CRPS and a tail-weighted score are reported beside it (change BA). Every compared forecast gets the same recalibration (change AX) | Better than both on volatility-scaled skill, with a bootstrap 95% interval that excludes zero. If it does not beat forecast 2, event handling is cut back to scheduled earnings only | Stage 2 |
| 2d | No peeking | Lookahead tests: no input dated after the forecast time, plus one test for each fitted piece listed in honesty rule 12 (change AT) | All pass | Stage 2 |
| 2e | Where the history-based forecast stands against the options market | Volatility-scaled CRPS skill (raw CRPS beside it, change BA) of the event-aware forecast against three option-based ones, 21-day horizon: raw, corrected, and the simple implied-volatility benchmark. Tuned on 2019–2022 chains; the final figure comes from the single 2023–2026 run (change AS) | Published as measured, with the smallest detectable difference. Expected on large, actively traded stocks: a loss to the corrected one. A win is re-checked by the red team before it is believed | Stage 2 |
| 2f | Blending helps | Volatility-scaled CRPS skill of the blend (forecast 6) against each of its two ingredients, all three recalibrated the same way; tuned on data up to 2022, final figure from the single 2023–2026 run (changes AS, AX, BA) | The blend is no worse than the better ingredient. If it is worse, the better ingredient becomes the headline | Stage 2 |
| **3. Know whether research adds skill** (live forecasts only) | | | | |
| 3a | The record cannot be edited or backdated | Hash-chain check on the ledger; each day's digest is pushed to the public repo with an independent timestamp. Every record states its resolution rule, written before the forecast: what it resolves to on delisting, takeover or a trading halt: the last traded price or the takeover consideration, and "void" only when no price exists, with the void rate reported beside every score (change AU). Ledger records, not just digests, are backed up and published at settlement (change BH) | Chain verifies on every run; an outsider can verify the timestamps and re-score the published records with a standard tool | Core ledger in Stage 0.5; public digest and timestamps in Stage 3 (change AZ) |
| 3b | Whether research changes forecast accuracy | Each live forecast is made twice with the same random seed, event-aware and event-aware plus research, and both are scored | A verdict with a confidence interval and, beside it, the smallest effect the test could have detected. A null result is worded "no detectable effect". Reported after ≥ 500 settled forecasts over ≥ 6 non-overlapping windows; "not enough data" before that. Expected to stay inconclusive for years; row 3d is the main test of the research layer | ~6 months live, then ongoing |
| 3c | Where the forecasts stand against the options market, live | Volatility-scaled CRPS skill (raw CRPS beside it, change BA) of the headline and research forecasts against the raw, corrected and simple option-based ones, same forecasts, 21-day horizon only (the 5-day horizon is scored against forecasts 1–3). Also reported: volatility-scaled skill, interval width, a tail-weighted score, calibration charts, and results split by calm, normal and stressed markets defined from what was known on the forecast day | Published as measured, with the smallest detectable difference. A win is re-checked by the red team before it is believed | ~6 months live |
| 3d | The model's event probabilities add information (main test of the research layer) | On every forecast the model gives a probability for every event class and a direction call for scheduled events. Brier score on resolved calls, per class, against the best simple statistical or market-implied probability. Each event class has a written resolution rule; "void" only when the outcome cannot be determined, with the void rate reported (change AU). The event-class list and the calm, normal and stressed thresholds are fixed before the power simulation (change BG) | A verdict per class with a confidence interval, once the number of resolved calls reaches a figure fixed by a power simulation before Stage 3; "not enough data" before that | Weeks to months after Stage 3 starts |
| **4. Anyone can use it** | | | | |
| 4a | It installs | Fresh machines in CI: Windows, macOS, Linux | Clone to first dossier in under 10 minutes | Stage 4 |
| 4b | Any model works | Conformance suite on three backends: a cloud API key, a subscription agent through MCP, a local model | All three pass, or the failing one is labelled "experimental" in the docs. The subscription agent passes by a live local run plus a replayed tool-call transcript in CI | Stage 4 |
| 4c | It costs what we say | Token and time cost printed on every run. For subscription agents: tool calls, result sizes and time, plus token cost where the agent exposes it | Measured and published; budget set after the first 20 runs | Stage 1 on |
| 4d | A stranger succeeds | Three people outside the project install and run it unaided | 3 of 3 | Before public launch |

### Rules that keep the evaluation honest

1. **Written first.** This scorecard is committed to the repo before any code. A pass line changes only with a dated note saying why.
2. **Three answers only.** Every release reports each row as *pass*, *fail* or *not yet measurable*. Never blank, never "mostly".
3. **Live or it does not count.** Skill claims about the research layer use only forecasts made after the model's training cutoff and frozen in the ledger.
4. **Failures are reported with their output**, in the same scorecard as the passes.
5. **Count the tries.** Every back-test configuration that is run is counted, and the count is printed beside every back-test result. *(Change O.)*
6. **One final test, on the record.** The single final run of each historical test (row 2b's 2023–2026 test, for example) is written to the ledger when it runs, so a second attempt would show. *(Change O.)*
7. **A gap between fitting and testing.** At least one forecast horizon separates the last day used for fitting from the first day used for testing. *(Change O.)*
8. **A higher bar when many verdicts are read together.** When several rows or event classes are judged at once, the confidence level is adjusted for the number of comparisons. *(Change O.)*
9. **Read each live verdict once.** Each live row is read at a count of settled forecasts fixed in advance, not checked repeatedly until it looks good. *(Change O.)* Daily operational alarms (a data-contract breach, input drift, a failed settle) are separate and run every day; they report on the machinery, never on a verdict. *(Change AH.)*
10. **Tries are counted by code.** Every back-test run is written to the run registry, and rule 5's count is read from it. Each proving experiment's hypothesis, measure and decision rule are committed before it runs. *(Change AG.)*
11. **No scorecard number comes from a notebook.** Notebooks are for exploring; every number that counts is produced by a `foxcast` command. *(Change AQ.)*
12. **Everything fitted is refit from the past only.** At each forecast date, every fitted piece is re-estimated from data available before that date: event base rates and effect sizes, factor betas, the option-implied bias correction, the blend, sector and peer labels (taken as of that date, not today's), and the conformal record (which uses only outcomes already settled, so it lags by one horizon). *(Change AT.)*
13. **The final period is touched once.** No historical row, proving experiment or design choice looks at 2023–2026. Rows 2b, 2c, 2e and 2f are scored on it together, in a single run recorded in the ledger. *(Change AS.)*

### What would make us change course

- Row 2b fails: the simulator is wrong. Fix it before adding any agents.
- Row 1c cannot reach 98% on a given model: that model is labelled unfit for dossiers.
- Row 2c: the event-aware forecast does not beat the plain volatility model. Event handling is cut back to scheduled earnings only.
- Row 3d reaches its required count and the model's probabilities are no better than the simple baseline: the research forecast stays experimental and out of the headline, and the research layer is kept for the dossier only.
- Yahoo fails on more than 1 in 10 fetches across the last 20 runs: the free-only data plan comes back to you for a decision.

### Two honest limits

- **Time.** A 21-day forecast takes a month to settle, and a fair verdict on return forecasts (rows 3b, 3c) takes about six months of live forecasts; row 3d, the main test of the research layer, can report sooner. Two things shorten the wait: forecasts are also made at 5 trading days, which yields evidence about four times faster, and row 2e answers the numbers-only question from history at Stage 2.
- **Sample.** Stocks move together, so 500 forecasts are worth fewer than 500 independent trials. The confidence intervals are computed by resampling whole time windows to account for that.

### How rows 1c and 1e are measured

Written on 2026-11-11, before any dossier was built (honesty rule 1). The same definitions are in `scorecard.toml`; every rule below appears there word for word, and a test keeps the two files in agreement.

#### The Stage 1A gate set

Rows 1a, 1b, 1c, 1d and 4c are judged on these 20 tickers: AIG, ALL, AON, AXON, DVN, EOG, FIS, FLEX, LMT, NTAP, NXPI, OKE, PYPL, T, TDG, TFC, TPL, TROW, VRSN, WRB.

They were drawn with seed `2026111101` from the declared universe, the same 208 members the watchlist was drawn from (`sp500_constituents_2026-11-05.csv`, member-list SHA-256 `b4c4f6b90e6fa7b94fd7c32e5678d1b9dfb1bf686211ae458cda9ec58da8b0cf`), using the watchlist's own draw code. No ticker was added or removed by hand. As drawn, it shares no ticker with the watchlist or the audit tickers.

Each dossier set is built on one frozen as-of date, chosen before its first dossier is built and recorded with the run.

#### Row 1c: sources say what we say they say

- **What is counted.** Every claim the model submits that cites at least one fact or passage ID, whatever type the model gives it, counted once per claim, per model, pooled over the 20 gate dossiers. Claims refused by the numbers rule stay in the count as not verified.
- **Verified.** A claim is verified when every number, passage and text value rendered in it matches its fact, every fact matches the content-hashed source copy saved at fetch time, and the claim's own words agree with its cited facts. One failed check makes the whole claim unverified. The rate is verified claims divided by counted claims, with the per-ticker rates beside it.
- **Numbers, fixed in advance.** Code renders every figure to at least three significant figures, or all its digits if the source value has fewer. The figure shown is read back after normalising currency signs, thousands separators, bracketed negatives, percent signs and scale words, and must equal the source value rounded to the precision shown: the difference may be at most half a unit in the last digit shown. Company, metric, period and unit must match exactly. A derived value is recomputed from its archived inputs and held to the same rule.
- **Passages, fixed in advance.** The passage shown must equal the archived span exactly after Unicode NFKC normalisation, collapsing runs of white space to one space, and making curly quotes and dashes plain. Case and every other character must match, and the span's SHA-256 must equal the archived hash. There is no partial or fuzzy matching, and no elision.
- **Text values.** A text value (an auditor, an executive, a segment name, an 8-K item type, a date) must equal its source value exactly after the passage normalisation.
- **The claim's own words.** Code reads the claim's own words with fixed patterns: periods (Q1 to Q4, H1 and H2, FY or fiscal followed by a year, and four-digit years), companies (a registered name in SEC's company list, or a ticker written in capitals as a whole word; a ticker that is also an English word, such as T or ALL, counts only inside parentheses or after a $ sign), scale and unit words (thousand, million, billion, percent, basis points, currency names and signs), and metric names from a fixed vocabulary mapped to XBRL concepts. Matching is case-sensitive and on whole words. Each item is checked on its own: every period, company, unit and metric the words name must be carried by at least one cited fact, so a named period that no cited fact has fails the claim even when its other items match. The patterns and the vocabulary are committed before the first gate dossier is built, and the run refuses to start unless their hash matches the one recorded in the run record.
- **Planted false claims.** Code builds deliberately false claims by taking verified claims from the gate run and changing one thing in each, 25 per category, drawn with seed `2026111103`. The categories are: altered number (a figure changed), altered quote (a word of a passage changed), wrong period, wrong unit (thousands shown as millions, or a ratio as a percent), wrong company and wrong metric. Each change is larger than the matching tolerance: a figure moves by at least 5%, and a unit by a factor of 100 or 1,000. A planted wrong period, company or metric swaps the cited fact for a genuine one that differs in that respect while the claim's words stay the same, and is drawn only from claims whose words name that thing. If fewer than 25 claims are eligible for a category, all eligible claims are used and the count is reported. Every planted claim goes through the same checker. The false-accept rate, per category and overall, is reported beside row 1c with its one-sided Clopper-Pearson 95% upper bound. Any accepted planted claim is a checker bug and is fixed before the 1A gate is read.

#### Row 1e: it holds up to a person

- **Audit tickers:** AAPL, NVDA, MSFT, GOOGL, AMZN. The five largest US companies by market value on 2026-11-11, as chosen in the approved Stage 1 plan. They replace "5 tickers you know well". Their dossiers are built the same way as the gate set's. They are outside the declared universe, so the audit describes large, heavily covered companies only.
- **The sample.** 20 claims per ticker, 100 in all, with seed `2026111102`. A fixed sample, not a sequential one, drawn after the dossiers are frozen and their hashes recorded with the marks. One Python random.Random generator, seeded with the sampler seed, takes the tickers in the order listed and, for each, draws sample(sorted claim IDs, 20) from the claims shown in that dossier, all claim types. If a dossier shows fewer than 20 claims, all of them are taken and the shortfall is not topped up. The number of withheld claims in each audit dossier is reported beside row 1e.
- **The bound.** The one-sided Clopper-Pearson 95% upper bound on the error rate, computed on the number of claims actually marked. The pass line, a bound below 5%, then allows **at most one wrong claim out of 100**. Zero wrong out of 100 gives a bound of 2.95%, one wrong gives 4.66%, and two wrong give 6.16%, which fails. Fewer than 59 marked claims cannot pass at all, even with none wrong.
- **The rubric.** Each claim is marked right or wrong against the dossier and its archived sources:
  1. A fact claim is right only if each cited fact is the right company, metric, period and unit for what the sentence says.
  2. The sentence's own words (direction, comparison, size words such as "doubled") must agree with the values shown.
  3. An estimate or forecast claim is right if it is labelled as one and its cited facts are the ones it says it rests on.
  4. An opinion claim is wrong if its cited facts do not support it.
  5. A claim the marker cannot judge is marked wrong.
- **"Told you something new."** While marking, the marker may flag a claim as new to them. This half of the pass line passes if at least one flagged claim is also marked right. The flags are saved with the marks.

---


## Change log

- **2026-11-11** First commit. Rows and rules as approved in the brief on 2026-11-11, including amendments A–N and changes O–V, AF–AR and AS–BH.
- **2026-11-11** Definitions, no pass line changed. (1) Every live record carries `sample`: `random` for the scored watchlist, `followed` for tickers a person chose to watch. Followed tickers are excluded from every row. (2) The scored watchlist is a seeded random draw from a universe declared before the draw and recorded with the watchlist (definition, size and hash); the first universe is S&P 500 members in the GICS sectors Communication Services, Energy, Financials and Information Technology, plus the Aerospace & Defense sub-industry. Live results therefore describe those sectors only.
- **2026-11-11** Data is pulled on demand, not by a daily scheduled job (owner's decision; to be revisited once foxcast is more complete). No pass line changed. (1) Every run covers the whole watchlist, never a chosen few, so the scored sample is not shaped by when someone chose to look. (2) The number of runs and their dates are reported beside every live row. (3) The Yahoo change-course trigger is counted over the last 20 runs instead of a month of daily snapshots. (4) The option-implied bias correction (forecast 5) is fitted on DoltHub's chain history, since our own snapshots now have gaps.
- **2026-11-11** Stage 1A measures for rows 1c and 1e, written before any dossier was built (approved Stage 1 plan, task 1A.1). No pass line changed, but two measures are made stricter on the record. (1) Row 1c counts every claim that cites a fact or passage, whatever type the model gives it (the plan said fact-type claims only, which would let a claim escape the count by being labelled an opinion). It fixes the matching rules for numbers, passages, text and the claim's own words, a minimum rendered precision of three significant figures, and the planted false claims with the false-accept rate reported beside the row. (2) Row 1e's measure now names five fixed audit tickers (AAPL, NVDA, MSFT, GOOGL, AMZN) in place of "5 tickers you know well", and uses a fixed sample of 100, dropping the sequential-sample option. With 100 claims the unchanged pass line, a 95% upper bound below 5%, allows at most one wrong claim. The bound method, the rubric, the generator, the seed and the per-ticker split are recorded. (3) The 20-ticker gate set for Stage 1A is drawn with a recorded seed. Details are under "How rows 1c and 1e are measured".
