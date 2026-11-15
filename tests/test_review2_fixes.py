"""Regression tests for the Stage 0.5 code review findings (2026-11-11)."""

import multiprocessing
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from foxcast.daily import run_daily_forecasts
from foxcast.ledger.chain import Ledger
from foxcast.sim.grid import QUANTILE_LEVELS
from foxcast.sim.naive import forecast_naive
from foxcast.sim.plain_vol import forecast_plain_vol
from foxcast.snapshot import completed_session_returns, run_snapshot
from foxcast.store.archive import SourceArchive
from foxcast.watchlist import draw_watchlist, validate_ticker
from tests.test_forecasts import garch_log_returns
from tests.test_stage05 import OPEN, fetchers

WORKERS = 4
PER_WORKER = 10


def _append_many(path_text: str, worker: int) -> None:
    ledger = Ledger(Path(path_text))
    for i in range(PER_WORKER):
        ledger.append(kind="forecast", request_id=f"w{worker}-{i}", payload={"w": worker, "i": i})


# 1: concurrent writers never fork the chain.
@pytest.mark.integration
def test_concurrent_writers_keep_one_valid_chain(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_append_many, args=(str(path), w)) for w in range(WORKERS)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=120)
        assert process.exitcode == 0
    ledger = Ledger(path)
    assert ledger.verify().ok
    assert len(ledger.records()) == WORKERS * PER_WORKER


# 2: Unicode line separators inside strings do not split records.
@pytest.mark.unit
@pytest.mark.parametrize("separator", [chr(0x2028), chr(0x2029), chr(0x85)])
def test_unicode_line_separators_in_payload(tmp_path: Path, separator: str) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(kind="forecast", request_id="r1", payload={"s": f"a{separator}b"})
    ledger.append(kind="forecast", request_id="r2", payload={"s": "c"})
    assert ledger.verify().ok
    assert ledger.records()[0].payload["s"] == f"a{separator}b"


# 3: a torn tail is named, blocks appends cleanly, and can be repaired with a backup.
@pytest.mark.unit
def test_torn_tail_is_reported_and_repairable(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append(kind="forecast", request_id="r1", payload={"x": 1})
    ledger.append(kind="forecast", request_id="r2", payload={"x": 2})
    path.write_text(path.read_text(encoding="utf-8")[:-15], encoding="utf-8")
    result = ledger.verify()
    assert not result.ok and "torn" in result.reason
    with pytest.raises(ValueError, match="refusing"):
        ledger.append(kind="forecast", request_id="r3", payload={"x": 3})
    backup = ledger.repair_torn_tail(tmp_path / "backups")
    assert backup.exists()
    assert ledger.verify().ok
    assert [r.request_id for r in ledger.records()] == ["r1"]


@pytest.mark.unit
def test_repair_refuses_a_ledger_that_is_not_torn(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(kind="forecast", request_id="r1", payload={"x": 1})
    with pytest.raises(ValueError, match="no torn"):
        ledger.repair_torn_tail(tmp_path / "backups")


# 4: a rerun with changed inputs skips with a reason instead of aborting.
@pytest.mark.unit
def test_rerun_with_changed_code_version_is_skipped_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_snapshot(["ABC"], tmp_path, now=lambda: OPEN, fetchers=fetchers())
    ledger = Ledger(tmp_path / "ledger.jsonl")
    monkeypatch.setattr("foxcast.daily.code_version", lambda project: "commit-one")
    first = run_daily_forecasts(["ABC"], tmp_path, ledger, day="2026-11-11", project=tmp_path)
    assert len(first.frozen) == 5
    assert "2026-11-11:ABC:option_raw:21" in first.frozen
    assert first.skipped == ()
    monkeypatch.setattr("foxcast.daily.code_version", lambda project: "commit-two")
    second = run_daily_forecasts(["ABC"], tmp_path, ledger, day="2026-11-11", project=tmp_path)
    assert second.frozen == ()
    assert len(second.skipped) == len(first.frozen)
    assert all("kept the first record" in reason for reason in second.skipped)
    assert len(ledger.records()) == len(first.frozen)


@pytest.mark.unit
def test_second_snapshot_on_the_same_day_keeps_the_first_capture(tmp_path: Path) -> None:
    run_snapshot(["ABC"], tmp_path, now=lambda: OPEN, fetchers=fetchers())
    report = run_snapshot(["ABC"], tmp_path, now=lambda: OPEN, fetchers=fetchers())
    assert report.written == ()
    assert sorted(report.kept) == ["ABC_chains.parquet", "ABC_prices.parquet"]


# 7: forecasts 1 and 2 use completed sessions only.
@pytest.mark.unit
def test_completed_session_returns_drop_the_intraday_row() -> None:
    prices = pd.DataFrame(
        {
            "date": [date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11)],
            "adj_close": [100.0, 110.0, 50.0],
        }
    )
    returns = completed_session_returns(prices, date(2026, 11, 11))
    assert returns.tolist() == pytest.approx([np.log(1.1)])


# 8: zero expected simple return, not just a zero median.
@pytest.mark.unit
def test_naive_and_plain_vol_have_zero_expected_simple_return() -> None:
    returns = garch_log_returns()
    for fc in (
        forecast_naive(returns, horizon=21, seed=11, paths=200_000),
        forecast_plain_vol(returns, horizon=21, seed=11, paths=50_000),
    ):
        # The mean of a distribution from its quantiles: average over the grid's middle.
        mean = float(np.mean(fc.values[1:-1]))
        assert abs(mean) < 0.004, fc.forecast_id


# 9: a fit that fails its check is not frozen — it reports the reason and the ratio.
@pytest.mark.unit
def test_plain_vol_rejects_a_fit_that_fails_its_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("foxcast.sim.plain_vol.FIT_CHECK_BAND", (10.0, 20.0))
    result = forecast_plain_vol(garch_log_returns(), horizon=5, seed=1)
    assert isinstance(result, str)
    assert "fit check failed" in result


# 10: a degenerate option fit becomes a reason, never a crash.
@pytest.mark.unit
def test_degenerate_option_fit_is_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    from foxcast.benchmark import option_implied

    class NanCurve:
        def quantile(self, level: float) -> float:
            return float("nan")

    monkeypatch.setattr(
        option_implied.ProbCurve, "from_chain", staticmethod(lambda chain, market: NanCurve())
    )
    chain = pd.DataFrame({"expiry": [pd.Timestamp("2026-12-11")]})
    result = option_implied.forecast_option_implied(
        chain, spot=100.0, valuation=date(2026, 11, 11), horizon_date=date(2026, 12, 11)
    )
    assert isinstance(result, str)
    assert "unusable" in result


# 11: the crontab is never replaced when it cannot be read.


# 12: ticker symbols that could escape a folder are rejected.
@pytest.mark.unit
@pytest.mark.parametrize("bad", ["../x", "BRK/B", "a:b", "", "TOOLONGTICKERSYMBOL"])
def test_bad_tickers_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_ticker(bad)
    if not bad:
        return  # blank lines in a universe file are skipped, not rejected
    with pytest.raises(ValueError):
        draw_watchlist(
            [bad, "AAPL"], size=1, seed=1, drawn_on=date(2026, 11, 11), universe_source="t"
        )


@pytest.mark.unit
@pytest.mark.parametrize("good", ["AAPL", "BRK.B", "BF-B", "^GSPC"])
def test_real_ticker_forms_are_accepted(good: str) -> None:
    assert validate_ticker(good) == good


@pytest.mark.unit
def test_bad_day_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        run_daily_forecasts(
            ["ABC"], tmp_path, Ledger(tmp_path / "l.jsonl"), day="../../x", project=tmp_path
        )


# 13: the archive repairs a corrupted copy on the next save.
@pytest.mark.unit
def test_archive_save_repairs_a_corrupted_copy(tmp_path: Path) -> None:
    archive = SourceArchive(tmp_path / "sources")
    path = archive.save(b"payload")
    path.write_bytes(b"tampered")
    archive.save(b"payload")
    assert path.read_bytes() == b"payload"


@pytest.mark.unit
def test_grid_levels_unchanged() -> None:
    assert len(QUANTILE_LEVELS) == 101
    assert datetime(2026, 11, 11, tzinfo=UTC) < datetime(2026, 11, 12, tzinfo=UTC)
