# CRPS Scoring Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the CRPS scoring primitives every Stage 2 scorecard row (2b/2c/2e/2f) is measured by: quantile-form CRPS on the shared grid, volatility-scaled skill, a tail-weighted variant, and a date-block bootstrap confidence interval.

**Architecture:** One new module `src/foxcast/scoring/crps.py` beside the existing `scoring/bounds.py`. Pure math on numpy/pandas-shaped inputs — no I/O, no fetching; the walk-forward harness calls it later.

**Tech Stack:** Python 3.12+, numpy, scipy (test anchors only), pytest.

**Spec:** `SCORECARD.md` rows 2b/2c/2e/2f — "volatility-scaled CRPS skill, so volatile stocks do not dominate; raw CRPS and a tail-weighted score are reported beside it (change BA)"; bootstrap "resampling whole dates"; "The effective sample size is reported, and the 99% result is labelled 'low power' (change BB)."

## Global Constraints

- Quantile forecasts live on `QUANTILE_LEVELS` from `foxcast.sim.grid` (101 levels, 0.005 + 1%–99% + 0.995) but `crps_from_quantiles` accepts *any* sorted levels/values pair — the grid is a caller detail, not a requirement.
- Deterministic: the bootstrap takes `seed`; same inputs → identical output.
- No lookahead, no fetching, no model code — pure functions over already-frozen results.
- Line length 100, ruff `E,F,W,I,B,UP,S,SIM,RUF`, mypy strict (`mypy src` is the repo gate), `Final` for constants.
- Tests in `tests/test_crps.py`, `@pytest.mark.unit`, `test_<thing>_<behavior>` naming.
- Branch: `devin-crps-scoring` cut from green `origin/main`.

## Review Focus

1. **Tail truncation.** The grid stops at 0.5%/99.5%; integrating as if Q were flat outside understates tail CRPS. Task 1 pins the flat-tail convention and a normal-distribution anchor tolerance that bounds the error.
2. **Zero/negative baseline CRPS.** A baseline score of exactly 0 makes the skill ratio blow up. Task 2 pins: `scaled_crps_skill` returns `nan` per-observation when the baseline is 0, and the aggregate mean skips `nan`.
3. **Correlated observations.** Bootstrap over individual (date, ticker) rows would overstate the sample; resampling whole dates preserves cross-sectional correlation. Task 3 pins it.
4. **Degenerate outcome inside/outside the grid range.** An outcome beyond the extreme quantiles must produce finite, sensible CRPS (linear-in-distance tails), not overflow or nan. Task 1 covers it.
5. **Few dates / low power.** With < 20 distinct dates the CI is nearly meaningless; Task 3 reports `n_dates` so the caller can apply the scorecard's "low power" label (labelling itself stays with the harness).

---

### Task 1: `crps_from_quantiles` + `tail_crps_from_quantiles`

**Files:**
- Create: `src/foxcast/scoring/crps.py`
- Test: `tests/test_crps.py`

**Interfaces:**
- Consumes: `numpy`; `Sequence` inputs.
- Produces:

```python
def crps_from_quantiles(values: Sequence[float], levels: Sequence[float], outcome: float) -> float:
    """CRPS of a quantile forecast against one outcome.

    2 * integral over levels of the pinball loss rho_a(q_a - y),
    trapezoidal between given levels, flat tails outside the grid
    (Q(a) = first/last value for a below/above the grid).
    """
```

- Validation `ValueError`: `levels`/`values` different lengths, fewer than 2 points, levels outside (0, 1), unsorted levels or values.

```python
def tail_crps_from_quantiles(
    values: Sequence[float], levels: Sequence[float], outcome: float, *, tail: float = 0.1
) -> float:
    """CRPS restricted to the outer ``tail`` bands (levels <= tail or >= 1 - tail),
    renormalised to a mean pinball loss over the covered range."""
```

- [ ] **Step 1: Write the failing tests**

Anchors:
- `test_crps_of_a_degenerate_forecast_is_the_absolute_error` — all `values = c`, `levels` = `QUANTILE_LEVELS`: `crps == abs(c - y)` for `c` above and below `y` (exact — the pinball integral of a constant forecast is linear).
- `test_crps_matches_the_normal_closed_form` — `values = norm.ppf(QUANTILE_LEVELS, loc=0.0, scale=sigma)`, `outcome = 0.0`: analytic `CRPS(N(0,σ²), 0) = σ·(2·φ(0) − 1/√π)` with `φ(0) = 1/√(2π)`; assert `approx(rel=0.01)` (flat tails underweight the outer half-percent each side).
- `test_crps_is_finite_for_an_outcome_beyond_the_grid` — outcome far past the 99.5% quantile → finite positive float, larger than a matched central outcome.
- `test_crps_rejects_bad_inputs` — mismatched lengths, one point, unsorted levels, level at 0 or 1 → `ValueError`.
- `test_tail_crps_only_counts_the_outer_bands` — two forecasts identical inside the 10–90% region but differing in the tails → equal `crps_from_quantiles`? No: differing tails → different full CRPS but their tail score ordering follows the tail difference; simpler pin: a forecast and its copy with the outer-band quantiles moved outward → `tail_crps` strictly larger, and `tail` keyword respected.
- `test_tail_crps_of_a_degenerate_forecast` — constant `c`, `c > y`: tail loss `(c−y)·mean(α)` over each covered band — pin the exact value: lower band mean α = 0.05, upper band mean α = 0.95, so per-band means `(c−y)·0.05` and `(c−y)·0.95`; combined renormalised mean over total covered range 0.2 = `(c−y)·0.5`. Assert `approx`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_crps.py -v --no-cov` → collection/import error (module missing).

- [ ] **Step 3: Implement `src/foxcast/scoring/crps.py`**

Pinball: `rho = np.where(q < y, (y - q) * (1 - a), (q - y) * a)` per node. Full CRPS: `2 * (analytic edge integrals for flat tails + trapezoid of rho over the given nodes)`. Edge integrals with constant `u = q_edge - y`: `∫_0^L u(a - 1{u<0}) da = u·L²/2 − u·L·1{u<0}`. Tail variant: select nodes in the bands plus the interpolated band-boundary points (interpolate `q` linearly at `tail` and `1 - tail`), trapezoid + edges as usual within `[lo, tail]` and `[1 - tail, hi]`, then divide by the total covered level range (`2 * tail`) and multiply by 2 to keep the same scale convention — document the normalisation in the docstring.

- [ ] **Step 4: Run tests to verify they pass; commit**

Run: `uv run pytest tests/test_crps.py -v --no-cov` → PASS.

```bash
git add src/foxcast/scoring/crps.py tests/test_crps.py
git commit -m "feat: quantile-form CRPS and tail-weighted CRPS (rows 2b-2f)"
```

---

### Task 2: `scaled_crps_skill`

**Files:**
- Modify: `src/foxcast/scoring/crps.py`
- Test: `tests/test_crps.py`

**Interfaces:**
- Produces:

```python
def scaled_crps_skill(model: float, baseline: float) -> float:
    """Per-observation volatility-scaled CRPS skill: 1 - model/baseline.

    nan when baseline == 0 (skill undefined; callers drop it from the mean).
    Positive = better than baseline, negative = worse, 0 = equal.
    """


def mean_skill(skills: Iterable[float]) -> float:
    """Mean of per-observation skills, ignoring nan."""
```

- [ ] **Step 1: Write the failing tests**

- `test_scaled_skill_is_one_minus_the_ratio` — `scaled_crps_skill(0.5, 1.0) == 0.5`; equal scores → 0.0; worse → negative.
- `test_scaled_skill_with_a_zero_baseline_is_nan` — `math.isnan(scaled_crps_skill(0.5, 0.0))`, and `mean_skill([0.5, nan, -0.25]) == 0.125`.

- [ ] **Step 2–4: fail, implement, pass; commit**

Two-line functions; `math.nan`. Commit: `feat: volatility-scaled CRPS skill (change BA)`.

---

### Task 3: date-block bootstrap `skill_summary`

**Files:**
- Modify: `src/foxcast/scoring/crps.py`
- Test: `tests/test_crps.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class ScoredForecast:
    day: str  # forecast day, ISO "YYYY-MM-DD"
    ticker: str
    model_crps: float
    baseline_crps: float


@dataclass(frozen=True)
class SkillSummary:
    skill: float  # mean per-obs scaled skill
    ci_low: float  # percentile bootstrap bounds
    ci_high: float
    n_obs: int  # scored forecasts
    n_dates: int  # distinct days — the effective sample unit
    mean_model_crps: float
    mean_baseline_crps: float
    tail_skill: float | None = None  # filled by a later variant; None here


DEFAULT_REPLICATES: Final = 10_000
DEFAULT_CONFIDENCE: Final = 0.95


def skill_summary(
    scores: Sequence[ScoredForecast],
    *,
    seed: int,
    replicates: int = DEFAULT_REPLICATES,
    confidence: float = DEFAULT_CONFIDENCE,
) -> SkillSummary:
    """Mean scaled skill with a block bootstrap CI over whole dates.

    Each replicate resamples the distinct days with replacement and pools every
    scored forecast on the sampled days — stocks move together, so the date is
    the effective sample unit (scorecard 2b; confidence adjusted per rule 8).
    """
```

- `ValueError` on empty `scores` or `confidence` outside (0, 1).

- [ ] **Step 1: Write the failing tests**

- `test_skill_summary_is_deterministic_from_the_seed` — same scores + seed → identical `SkillSummary`; different seed → same `skill` point but (usually) different CI.
- `test_bootstrap_resamples_whole_dates` — construct scores where one date holds many tickers with negative skill and the rest are positive; a *per-observation* bootstrap would down-weight that date — pin instead: monkeypatch `np.random.Generator.choice`? No — simpler honest pin: call a small internal `_resample_by_date(scores, rng)` helper that returns the pooled skill for one resample; assert a resample's pooled skill is always the mean of a union of whole days (testable property: pooled skill ∈ convex hull of per-day means — or directly check the helper returns skills built only from whole-day groups). Implement `_resample_by_date` as the unit under test.
- `test_skill_summary_reports_effective_sample` — N dates × K tickers → `n_dates == N`, `n_obs == N*K`.
- `test_skill_summary_rejects_empty` — `ValueError`.

- [ ] **Step 2–4: fail, implement, pass; commit**

Bootstrap: group indices by `day`; per replicate `rng.choice(n_dates, size=n_dates, replace=True)` → pooled per-obs skills → mean. Percentile CI: `np.quantile(replicate_skills, [α/2, 1−α/2])`.

Commit: `feat: date-block bootstrap skill summary (rows 2b-2f, rule 8)`.

- [ ] **Step 5: Full verification**

`uv run pytest --no-cov` (whole suite green), `uv run ruff check .`, `uv run mypy src`, plus `uv run pytest` once for the coverage gate. Then open the PR.

---

## Out of scope (flagged, not dropped)

- Conformal recalibration (change AX/W) — separate fitted piece, needs settled forecasts.
- The walk-forward harness that *produces* `ScoredForecast` rows — next task after this lands.
- The "low power" label — applied by the harness reading `n_dates`/`n_obs`, not by the scorer.
