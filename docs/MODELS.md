# Model inventory

One entry per forecast in the family, plus the named benchmark. This follows the model-risk practice of keeping an inventory with each model's purpose, assumptions, limits, adjustments, evidence and monitoring (brief change AH; PRA SS1/23 principles 1 and 4). An entry is updated whenever the model changes, and the evidence column points only at scorecard rows, never at a notebook (honesty rule 11).

Status values: **not built**, **built, not validated**, **validated** (its scorecard rows pass), **retired**.

| # | Forecast | Status | Stage |
|---|---|---|---|
| 1 | Naive (constant volatility) | built, not validated | 0.5 |
| 2 | Plain volatility model | built, not validated | 0.5 |
| 3 | Event-aware | not built | 2 |
| 4 | Option-implied, raw | built, not validated | 0.5 |
| 5 | Option-implied, corrected | not built | 2 |
| 6 | Blend (the headline) | not built | 2 |
| 7 | Event-aware plus research | not built | 3 |
| B | Simple implied-volatility benchmark | not built | 2 |

---

## 1. Naive

- **Purpose:** the floor every other forecast must beat.
- **How it works:** returns drawn from a distribution with one volatility estimated from recent history, and zero expected excess return.
- **Assumptions:** volatility is constant over the horizon; no events.
- **Known limits:** ignores volatility clustering, earnings dates and fat tails.
- **Adjustments:** conformal recalibration (change W) is applied the same way as to every compared forecast (change AX).
- **Evidence:** rows 2a, 2c.
- **Monitoring:** coverage of the 80% interval on settled live forecasts.

## 2. Plain volatility model

- **Purpose:** shows whether event handling adds anything (row 2c's second baseline).
- **How it works:** an asymmetric, heavy-tailed volatility model fitted to all days; paths simulated by resampling standardised residuals.
- **Assumptions:** the fitted model's dynamics persist over the horizon.
- **Known limits:** no knowledge of scheduled events.
- **Adjustments:** as forecast 1.
- **Evidence:** rows 2a (including `arch`'s exact multi-step variance), 2b, 2c.
- **Monitoring:** as forecast 1, plus a fit check (simulated variance against history).

## 3. Event-aware

- **Purpose:** the history-based forecast.
- **How it works:** forecast 2 plus the event calendar and measured event effects (brief section 2.2).
- **Known limits:** listed in brief 2.2, "What this method will still get wrong".
- **Evidence:** rows 2b, 2c, 2e.

## 4. Option-implied, raw

- **Purpose:** the market's own view, unadjusted.
- **How it works:** the risk-neutral distribution from the option chain (Breeden-Litzenberger), via `oipd` behind our own interface. 21-day horizon only (amendment D).
- **Known limits:** reflects risk appetite as well as expectations; no early-exercise or dividend correction; thin free data.
- **Evidence:** rows 2e, 3c.

## 5. Option-implied, corrected

- **Purpose:** the best-evidenced single forecast where options trade actively.
- **How it works:** forecast 4 adjusted for its measured past bias, refitted from the past only (honesty rule 12).
- **Evidence:** rows 2e, 2f, 3c.

## 6. Blend (the headline)

- **Purpose:** the headline forecast, provided row 2f passes.
- **How it works:** an equal-weight combination of forecasts 3 and 5; the method (quantile averaging or mixing) is fixed by proving experiment 5 (change AD). At 5 days, and when the chain fails its liquidity contract, the headline is forecast 3 (change AW).
- **Evidence:** row 2f.

## 7. Event-aware plus research

- **Purpose:** tests whether the language model's judgment adds skill.
- **How it works:** forecast 3 with event probabilities from the research layer, blended with base rates on the odds scale.
- **Status note:** experimental until row 3d shows skill.
- **Evidence:** rows 3b, 3c, 3d.

## B. Simple implied-volatility benchmark

- **Purpose:** a benchmark outside the numbered family (change BG).
- **How it works:** a distribution built from a single implied-volatility number.
- **Evidence:** rows 2e, 3c.
