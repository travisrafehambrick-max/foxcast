# Stage 2 Sim Stubs + Contract Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create honest "not built" stubs for the four unbuilt Stage 2 forecasts (3 event-aware, 5 option-implied corrected, 6 blend, B simple implied-volatility benchmark) with contract tests pinning the interface every forecast in the family must satisfy.

**Architecture:** Every forecast returns `QuantileForecast | str` — a frozen forecast on the shared 101-level quantile grid, or a plain-language reason it is unavailable (the convention `benchmark/option_implied.py` already sets). Stubs own their input validation and return an honest unavailability reason today, so the freeze path, scorer and walk-forward harness can be built against stable signatures before the models land. The blend's fallback rules are decidable now and are implemented for real; only its combination method stays stubbed.

**Tech Stack:** Python 3.12+, numpy, pandas, pytest, `arch`, `oipd` (already dependencies).

**Spec:** `SCORECARD.md` (rows 2a–2f, honesty rules 12–13) and `docs/MODELS.md` (model inventory — status column, fallback rules for forecast 6, "not built" rows).

## Global Constraints

- **Quantile grid:** every forecast yields exactly 101 values on `QUANTILE_LEVELS` from `foxcast.sim.grid` (0.005, 1%–99%, 0.995), sorted ascending. `QuantileForecast` validates this itself.
- **Unavailability convention:** return a `str` reason, never raise for a legitimately-unavailable forecast and never fabricate a number. `ValueError` is only for invalid caller input (the `sim/` convention).
- **Determinism:** stochastic forecasts take `seed: int` and record it; reproducible from the seed.
- **Honesty rule 12:** no fitting inside these functions on anything but the inputs passed in; every fitted piece arrives as a parameter, already estimated from past-only data by the caller.
- **Forecast IDs:** existing `naive`, `plain_vol`, `option_raw`. New, pinned here: `event_aware`, `option_corrected`, `blend`, `iv_benchmark`.
- **Code style:** line length 100, ruff (`E,F,W,I,B,UP,S,SIM,RUF`), mypy strict, `Final` for module constants, one-line docstring summary then detail (match `sim/plain_vol.py`).
- **Tests:** live in `tests/`, marked `@pytest.mark.unit`, named `test_<thing>_<behavior>`, run with `uv run pytest`.
- **Branch:** work happens in this worktree on `devin-stage2-stubs`, cut from green `origin/main` per `AGENTS.md` protocol step 1.

## Review Focus

1. **Blend fallback mislabeled.** A fallback that froze under the ingredient's `forecast_id` would silently mix identities in the ledger and corrupt scoring. Task 5 pins: fallback results carry `forecast_id="blend"` with `detail["fallback"]` recording the reason.
2. **Stub-guard tests deleted wholesale.** The "not built" assertions are temporary guards, not the contract. They live in `tests/test_stage2_stubs.py`, separate from the durable contract tests in `tests/test_forecast_contracts.py`, so implementing a model deletes one stub-guard function, not shared machinery.
3. **Events schema commits data assembly too early.** The event calendar's full schema isn't decided by the spec; the stub requires only `event_date` and `kind` columns so downstream snapshot work isn't over-constrained.
4. **`OptionBiasCorrection` protocol is provisional.** The bias-correction method isn't in the spec (refit-from-past is, honesty rule 12). The protocol is a seam; changing it later means changing the stub file and its test, nothing else.
5. **Horizon semantics differ across the family.** Option forecasts are measured calendar-days-to-expiry but are *labelled* at the requested horizon (`option_raw` already does this in `detail`). Contract tests pin `horizon_days` carries the label, not the calendar gap.

---

### Task 1: Cut the branch and add the forecast contract helper

**Files:**
- Create: `tests/forecast_contract.py`
- Test: `tests/test_forecast_contracts.py` (created here, extended by later tasks)

**Interfaces:**
- Consumes: `foxcast.sim.grid.QuantileForecast`, `foxcast.sim.grid.QUANTILE_LEVELS`, existing builders `forecast_naive`, `forecast_plain_vol`, `forecast_option_implied`.
- Produces: `check_forecast_contract(result: QuantileForecast | str, *, forecast_id: str, horizon: int) -> None` — asserts the union type; when `result` is a `QuantileForecast`, asserts `forecast_id`, `horizon_days`, 101 values, ascending order. Used by every later task's tests.

- [ ] **Step 1: Create the work branch**

```bash
git fetch origin
git checkout -B devin-stage2-stubs origin/main
uv sync
```

- [ ] **Step 2: Write the first failing test**

`tests/test_forecast_contracts.py`, first test — the contract must already hold for the built forecasts (reuse the `gbm_log_returns` helper pattern from `tests/test_forecasts.py`, seed `20261111`):

```python
@pytest.mark.unit
def test_naive_satisfies_the_forecast_contract() -> None:
    returns = np.random.default_rng(1).normal(0.0, 0.02, size=2000)
    check_forecast_contract(
        forecast_naive(returns, horizon=21, seed=SEED), forecast_id="naive", horizon=21
    )
```

- [ ] **Step 3: Run test to verify it fails, then write the helper**

Run: `uv run pytest tests/test_forecast_contracts.py -v --no-cov`
Expected: FAIL on `from tests.forecast_contract import check_forecast_contract` (or `from forecast_contract import ...` — match the import style `tests/` already uses for shared helpers; check `conftest.py` first).

Then create `tests/forecast_contract.py` (plain helper module, no `test_` prefix):

```python
from foxcast.sim.grid import QUANTILE_LEVELS, QuantileForecast


def check_forecast_contract(
    result: QuantileForecast | str, *, forecast_id: str, horizon: int
) -> None:
    """Every forecast in the family returns a forecast on the grid or a reason string."""
    assert isinstance(result, (QuantileForecast, str))
    if isinstance(result, str):
        assert result.strip(), "unavailable reason must not be empty"
        return
    assert result.forecast_id == forecast_id
    assert result.horizon_days == horizon
    assert len(result.values) == len(QUANTILE_LEVELS) == 101
    assert list(result.values) == sorted(result.values)
```

Re-run: `uv run pytest tests/test_forecast_contracts.py -v --no-cov`
Expected: PASS.

- [ ] **Step 4: Extend coverage to the other built forecasts**

Add `test_plain_vol_satisfies_the_forecast_contract` (needs ≥500 returns — use the `garch_log_returns` simulator pattern from `tests/test_forecasts.py`) and `test_option_raw_satisfies_the_forecast_contract` (reuse `black_scholes_chain`, assert contract on the success path).

Run: `uv run pytest tests/test_forecast_contracts.py tests/test_forecasts.py -v --no-cov`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/forecast_contract.py tests/test_forecast_contracts.py
git commit -m "test: forecast-family contract helper pinned against built forecasts"
```

---

### Task 2: `event_aware` stub (forecast 3)

**Files:**
- Create: `src/foxcast/sim/event_aware.py`
- Test: `tests/test_stage2_stubs.py`, `tests/test_forecast_contracts.py`

**Interfaces:**
- Consumes: `numpy`, `pandas`, `foxcast.sim.grid.DEFAULT_PATHS`, `ARCHModelResult` (type only, for the optional pre-fit the caller may pass — mirrors `forecast_plain_vol`'s `fitted` parameter).
- Produces:

```python
REQUIRED_EVENT_COLUMNS: Final = ("event_date", "kind")
NOT_BUILT: Final = "forecast 3 (event-aware) is not built (Stage 2)"

def forecast_event_aware(
    log_returns: np.ndarray,
    events: pd.DataFrame,
    *,
    forecast_day: date,
    horizon: int,
    seed: int,
    paths: int = DEFAULT_PATHS,
    fitted: ARCHModelResult | None = None,
) -> QuantileForecast | str:
```

- `events` is the event calendar: one row per scheduled event with at least `event_date` and `kind`. `ValueError` when a required column is missing. Returns `NOT_BUILT` today.

- [ ] **Step 1: Write the failing tests**

`tests/test_stage2_stubs.py` — new file. **Import convention:** every stub module exports a `NOT_BUILT` constant, so import the modules (`from foxcast.sim import event_aware`) and qualify (`event_aware.NOT_BUILT`) rather than importing the bare name — three same-named constants would collide in one test file. The blocks below write `NOT_BUILT` for readability; qualify it in code.

```python
@pytest.mark.unit
def test_event_aware_reports_not_built() -> None:
    returns = garch_log_returns()
    events = pd.DataFrame({"event_date": [date(2026, 12, 1)], "kind": ["earnings"]})
    result = forecast_event_aware(
        returns, events, forecast_day=date(2026, 11, 14), horizon=21, seed=SEED
    )
    assert result == NOT_BUILT
```

```python
@pytest.mark.unit
def test_event_aware_rejects_an_event_calendar_without_required_columns() -> None:
    with pytest.raises(ValueError, match="event_date"):
        forecast_event_aware(
            garch_log_returns(),
            pd.DataFrame({"kind": ["earnings"]}),
            forecast_day=date(2026, 11, 14),
            horizon=21,
            seed=SEED,
        )
```

`tests/test_forecast_contracts.py`: `test_event_aware_satisfies_the_forecast_contract` — call with a valid frame, assert via `check_forecast_contract(..., forecast_id="event_aware", horizon=21)` (passes today on the `str` branch).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stage2_stubs.py tests/test_forecast_contracts.py -v --no-cov`
Expected: FAIL — `forecast_event_aware` does not exist.

- [ ] **Step 3: Implement `src/foxcast/sim/event_aware.py`**

Module docstring names it "Forecast 3, the event-aware model (brief 2.2; MODELS.md row 3) — stub". Validate `REQUIRED_EVENT_COLUMNS` against `events.columns` (`ValueError` listing the first missing column), then `return NOT_BUILT`. Keep the signature exactly as above — `forecast_day`, `horizon`, `seed`, `paths`, `fitted` are the real model's interface, unused today beyond validation (prefix nothing; the return is unconditional).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_stage2_stubs.py tests/test_forecast_contracts.py -v --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foxcast/sim/event_aware.py tests/test_stage2_stubs.py tests/test_forecast_contracts.py
git commit -m "feat: event-aware forecast stub with the real signature (forecast 3)"
```

---

### Task 3: `iv_benchmark` stub (forecast B)

**Files:**
- Create: `src/foxcast/benchmark/iv_benchmark.py`
- Test: `tests/test_stage2_stubs.py`, `tests/test_forecast_contracts.py`

**Interfaces:**
- Consumes: `pandas` chain frame (same columns the snapshot writes and `forecast_option_implied` reads: `strike`, `expiry`, `option_type`, `last_price`, `bid`, `ask`).
- Produces:

```python
NOT_BUILT: Final = "forecast B (simple implied-volatility benchmark) is not built (Stage 2)"

def forecast_iv_benchmark(
    chain: pd.DataFrame,
    *,
    spot: float,
    valuation: date,
    horizon_date: date,
    horizon_days: int | None = None,
) -> QuantileForecast | str:
```

- Mirrors `forecast_option_implied`'s signature minus `risk_free_rate` (a single-IV distribution needs no curve fit). Returns `NOT_BUILT` today; no input validation beyond types — which strike counts as at-the-money is the real model's decision.

- [ ] **Step 1: Write the failing test**

`tests/test_stage2_stubs.py`:

```python
@pytest.mark.unit
def test_iv_benchmark_reports_not_built() -> None:
    valuation = date(2026, 11, 14)
    chain = black_scholes_chain(100.0, 0.30, valuation, valuation + timedelta(days=30))
    result = forecast_iv_benchmark(
        chain,
        spot=100.0,
        valuation=valuation,
        horizon_date=valuation + timedelta(days=30),
        horizon_days=21,
    )
    assert result == NOT_BUILT
```

Add to `tests/test_forecast_contracts.py`: `test_iv_benchmark_satisfies_the_forecast_contract` (`forecast_id="iv_benchmark"`, `horizon=21`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stage2_stubs.py tests/test_forecast_contracts.py -v --no-cov`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `src/foxcast/benchmark/iv_benchmark.py`**

Docstring: "Forecast B, the simple implied-volatility benchmark (MODELS.md row B) — stub". Body: `return NOT_BUILT`.

- [ ] **Step 4: Run tests to verify they pass; commit**

Run: `uv run pytest tests/test_stage2_stubs.py tests/test_forecast_contracts.py -v --no-cov`
Expected: PASS.

```bash
git add src/foxcast/benchmark/iv_benchmark.py tests/test_stage2_stubs.py tests/test_forecast_contracts.py
git commit -m "feat: simple implied-volatility benchmark stub (forecast B)"
```

---

### Task 4: `option_corrected` stub (forecast 5)

**Files:**
- Create: `src/foxcast/benchmark/option_corrected.py`
- Test: `tests/test_stage2_stubs.py`, `tests/test_forecast_contracts.py`

**Interfaces:**
- Consumes: the same chain frame as `forecast_option_implied`, plus a caller-fitted correction object.
- Produces:

```python
class OptionBiasCorrection(Protocol):
    """A fitted past-bias adjustment for the raw option-implied quantiles (honesty rule 12).

    Provisional seam: the correction method is the real model's decision. Anything with
    this method satisfies the stub.
    """

    def adjust(
        self, values: Sequence[float], levels: Sequence[float]
    ) -> tuple[float, ...]: ...

NOT_BUILT: Final = "forecast 5 (option-implied, corrected) is not built (Stage 2)"

def forecast_option_corrected(
    chain: pd.DataFrame,
    *,
    spot: float,
    valuation: date,
    horizon_date: date,
    correction: OptionBiasCorrection,
    risk_free_rate: float = 0.0,
    horizon_days: int | None = None,
) -> QuantileForecast | str:
```

- Returns `NOT_BUILT` today. `correction` is a required keyword: a corrected forecast without a fitted correction is the raw forecast, and the signature should make that impossible to forget.

- [ ] **Step 1: Write the failing test**

`tests/test_stage2_stubs.py` — a trivial identity correction in the test satisfies the protocol:

```python
class _IdentityCorrection:
    def adjust(self, values, levels):
        return tuple(values)


@pytest.mark.unit
def test_option_corrected_reports_not_built() -> None:
    valuation = date(2026, 11, 14)
    chain = black_scholes_chain(100.0, 0.30, valuation, valuation + timedelta(days=30))
    result = forecast_option_corrected(
        chain,
        spot=100.0,
        valuation=valuation,
        horizon_date=valuation + timedelta(days=30),
        correction=_IdentityCorrection(),
        horizon_days=21,
    )
    assert result == NOT_BUILT
```

Add `test_option_corrected_satisfies_the_forecast_contract` (`forecast_id="option_corrected"`, `horizon=21`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stage2_stubs.py tests/test_forecast_contracts.py -v --no-cov`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `src/foxcast/benchmark/option_corrected.py`**

Docstring: "Forecast 5, option-implied corrected for its measured past bias (MODELS.md row 5) — stub". Define `OptionBiasCorrection` as `typing.Protocol`, then `return NOT_BUILT`.

- [ ] **Step 4: Run tests to verify they pass; commit**

Run: `uv run pytest tests/test_stage2_stubs.py tests/test_forecast_contracts.py -v --no-cov`
Expected: PASS.

```bash
git add src/foxcast/benchmark/option_corrected.py tests/test_stage2_stubs.py tests/test_forecast_contracts.py
git commit -m "feat: corrected option-implied forecast stub (forecast 5)"
```

---

### Task 5: `blend` — real fallback logic, stubbed combination (forecast 6)

**Files:**
- Create: `src/foxcast/sim/blend.py`
- Test: `tests/test_stage2_stubs.py`, `tests/test_forecast_contracts.py`

**Interfaces:**
- Consumes: `QuantileForecast | str` results — nothing else; the blend never rebuilds ingredients.
- Produces:

```python
FIVE_DAY_HORIZON: Final = 5
NOT_BUILT: Final = (
    "forecast 6 (blend) combination method is not fixed until proving experiment 5 (Stage 2)"
)

def forecast_blend(
    history: QuantileForecast | str,
    corrected: QuantileForecast | str,
    *,
    horizon_days: int,
) -> QuantileForecast | str:
```

- `history` is the forecast-3 result, `corrected` the forecast-5 result, for the same ticker and day. Behaviour, per MODELS.md row 6 / change AW:
  - `horizon_days == 5` → headline is forecast 3: pass `history` through, or its `str` reason if it is one.
  - `corrected` is a `str` (chain failed its liquidity contract) → headline is forecast 3: same passthrough.
  - `history` is a `str` at 21 days → `str`: `f"blend unavailable: history forecast unavailable: {history}"`.
  - both `QuantileForecast` at 21 days → `NOT_BUILT`.
- Passthrough relabels: a fresh `QuantileForecast` with `forecast_id="blend"`, the ingredient's `values`, `seed`, `paths`, and `detail` = `{**ingredient.detail, "blend_source": ingredient.forecast_id, "fallback": <reason>}` where reason is `"5-day horizon"` or the corrected forecast's `str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_stage2_stubs.py` — build two `QuantileForecast` fixtures via `forecast_naive` on `gbm_log_returns`, relabeled via `dataclasses.replace` to `forecast_id="event_aware"` / `"option_corrected"`. Build each at the horizon under test (a 5-day `history_fc` for the 5-day test, 21-day fixtures for the rest) so `horizon_days` is honest inside the passthrough:

```python
@pytest.mark.unit
def test_blend_at_five_days_is_the_history_forecast() -> None:
    result = forecast_blend(history_fc, corrected_fc, horizon_days=5)
    assert isinstance(result, QuantileForecast)
    assert result.forecast_id == "blend"
    assert result.values == history_fc.values
    assert result.detail["blend_source"] == "event_aware"
    assert result.detail["fallback"] == "5-day horizon"
```

```python
@pytest.mark.unit
def test_blend_falls_back_when_the_chain_is_unavailable() -> None:
    result = forecast_blend(history_fc, "no option expiry within 7 days", horizon_days=21)
    assert result.forecast_id == "blend"
    assert result.detail["blend_source"] == "event_aware"
    assert "no option expiry" in result.detail["fallback"]
```

```python
@pytest.mark.unit
def test_blend_is_unavailable_when_history_is() -> None:
    result = forecast_blend("insufficient history", corrected_fc, horizon_days=21)
    assert isinstance(result, str)
    assert "insufficient history" in result
```

```python
@pytest.mark.unit
def test_blend_of_two_forecasts_reports_not_built() -> None:
    assert forecast_blend(history_fc, corrected_fc, horizon_days=21) == NOT_BUILT
```

Add `test_blend_satisfies_the_forecast_contract` covering both a fallback path (`forecast_id="blend"`, `horizon=5`) and the `NOT_BUILT` string path (`horizon=21`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stage2_stubs.py tests/test_forecast_contracts.py -v --no-cov`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `src/foxcast/sim/blend.py`**

Docstring: "Forecast 6, the blend — the headline (MODELS.md row 6). Fallback rules implemented; the 21-day combination method awaits proving experiment 5." A private `_passthrough(ingredient: QuantileForecast, *, horizon: int, reason: str) -> QuantileForecast` does the relabel via `dataclasses.replace` plus rebuilt `detail`. The decision order is exactly the four bullets above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_stage2_stubs.py tests/test_forecast_contracts.py -v --no-cov`
Expected: PASS.

- [ ] **Step 5: Full verification**

Run: `uv run pytest --no-cov` and `uv run ruff check .` and `uv run mypy src tests`
Expected: whole suite green (coverage gate: run once without `--no-cov` — `uv run pytest` — and confirm `--cov-fail-under=80` still passes), no lint or type errors.

- [ ] **Step 6: Commit**

```bash
git add src/foxcast/sim/blend.py tests/test_stage2_stubs.py tests/test_forecast_contracts.py
git commit -m "feat: blend fallback rules with stubbed combination (forecast 6)"
```

---

## Self-review notes (resolved while writing)

- **Wiring into `daily.py` is deliberately out of scope.** The freeze path is reachable through `foxcast forecast`, but registering forecasts 3/5/6/B there needs an event-calendar snapshot we don't collect yet, and `cli/` + daily wiring sits closer to Cursor's lane. The stubs are pure functions; wiring is a follow-up PR. Flagged, not silently dropped.
- **`docs/MODELS.md` status column** still says "not built" — accurate for stubs, and the file is Cursor's lane. Leave untouched; the PR description should note the stubs exist so the status can be updated to e.g. "stubbed" by that lane.
- **Tests import helpers** (`gbm_log_returns`, `garch_log_returns`, `black_scholes_chain`) from `tests/test_forecasts.py`; importing test-module helpers across test files is acceptable here (no conftest move — keeps the diff small).
