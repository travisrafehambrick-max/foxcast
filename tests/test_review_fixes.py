"""Regression tests for the Stage 0 code review findings (2026-11-11)."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from foxcast.conformance import ReferenceBackend, Task, load_tasks
from foxcast.config import load_settings, write_default_config
from foxcast.doctor import Status, check_sec_reachable, run_checks
from foxcast.sources.base import Fetched, NoData, Unavailable, redact_url, run_provider
from foxcast.sources.sec import ConceptQuery, SecConceptProvider, first_reported, latest_reported
from foxcast.sources.tickers import SecurityKey, TickerTable, parse_sec_ticker_file

CONTACT = "Jane Doe jane@example.com"
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "sec_companyconcept_revenues.json").read_text()
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FOXCAST_SEC_CONTACT", "FOXCAST_FRED_API_KEY", "FOXCAST_WATCHLIST_SIZE"):
        monkeypatch.delenv(name, raising=False)


def sec_result(payload: object, query: ConceptQuery | None = None) -> object:
    body = json.dumps(payload).encode()
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    )
    q = query or ConceptQuery(cik=1234567, tag="Revenues")
    return run_provider(SecConceptProvider(CONTACT), q, client, throttle=None)


def with_items(items: list[dict[str, object]]) -> dict[str, object]:
    return {**FIXTURE, "units": {"USD": items}}


BASE_ITEM = {"end": "2023-12-31", "val": 1.0, "fy": 2023, "fp": "FY", "form": "10-K"}


# C1: the key can never come from the config file, under any spelling, and is never echoed.
@pytest.mark.unit
@pytest.mark.parametrize(
    "spelling", ["fred_api_key", "FRED_API_KEY", "Fred_Api_Key", "FOXCAST_FRED_API_KEY"]
)
def test_config_file_key_rejected_in_any_spelling(tmp_path: Path, spelling: str) -> None:
    config = tmp_path / "foxcast.toml"
    config.write_text(f'{spelling} = "SECRETVALUE123"\n')
    with pytest.raises(ValueError, match="environment") as caught:
        load_settings(config)
    assert "SECRETVALUE123" not in str(caught.value)


@pytest.mark.unit
def test_doctor_never_echoes_a_rejected_value(tmp_path: Path) -> None:
    config = tmp_path / "foxcast.toml"
    config.write_text('unknown_field = "SECRETVALUE123"\n')
    results = run_checks(
        config, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    )
    config_row = results[1]
    assert config_row.status is Status.FAIL
    assert "SECRETVALUE123" not in config_row.detail


# H1: a bad environment variable is a FAIL row, not a crash.
@pytest.mark.unit
def test_doctor_reports_bad_env_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOXCAST_WATCHLIST_SIZE", "0")
    results = run_checks(
        tmp_path / "missing.toml",
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )
    assert results[1].status is Status.FAIL
    assert "watchlist_size" in results[1].detail


@pytest.mark.unit
def test_doctor_treats_a_redirect_as_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOXCAST_SEC_CONTACT", CONTACT)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(301)))
    assert check_sec_reachable(load_settings(tmp_path / "x.toml"), client).status is Status.FAIL


# H2 and M1: malformed payloads are Unavailable, empty ones are NoData.
@pytest.mark.unit
def test_non_object_units_is_unavailable() -> None:
    assert isinstance(sec_result({**FIXTURE, "units": []}), Unavailable)


@pytest.mark.unit
def test_empty_units_is_no_data() -> None:
    assert isinstance(sec_result({**FIXTURE, "units": {}}), NoData)


@pytest.mark.unit
def test_nan_value_breaks_the_contract() -> None:
    item = {
        **BASE_ITEM,
        "start": "2023-01-01",
        "accn": "0001234567-24-000010",
        "filed": "2024-02-15",
    }
    payload = json.loads(json.dumps(with_items([item])).replace('"val": 1.0', '"val": NaN'))
    assert isinstance(sec_result(payload), Unavailable)


# M2: inputs are validated before any URL is built, and the payload must match the query.
@pytest.mark.unit
@pytest.mark.parametrize(
    ("cik", "tag", "taxonomy"),
    [
        (0, "Revenues", "us-gaap"),
        (-1, "Revenues", "us-gaap"),
        (1, "../../x?y=", "us-gaap"),
        (1, "Revenues", "a/b"),
    ],
)
def test_bad_query_is_rejected(cik: int, tag: str, taxonomy: str) -> None:
    with pytest.raises(ValueError):
        ConceptQuery(cik=cik, tag=tag, taxonomy=taxonomy)


@pytest.mark.unit
def test_payload_for_another_company_is_unavailable() -> None:
    result = sec_result(FIXTURE, ConceptQuery(cik=7654321, tag="Revenues"))
    assert isinstance(result, Unavailable)
    assert "wrong subject" in result.reason


# M3: secret query parameters never reach a stored or reported URL.
@pytest.mark.unit
def test_redact_url_masks_secret_params() -> None:
    url = "https://api.example.test/series?series_id=GDP&api_key=SECRET"
    redacted = redact_url(url, frozenset({"api_key"}))
    assert "SECRET" not in redacted
    assert "series_id=GDP" in redacted


@pytest.mark.unit
def test_runner_redacts_url_in_results_and_errors() -> None:
    from tests.test_sources_base import FakeProvider

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed {request.url}", request=request)

    class KeyedProvider(FakeProvider):
        def build_request(self, query: str) -> httpx.Request:
            return httpx.Request("GET", f"https://example.test/{query}?api_key=SECRET")

    result = run_provider(KeyedProvider(), "q", httpx.Client(transport=httpx.MockTransport(boom)))
    assert isinstance(result, Unavailable)
    assert "SECRET" not in result.url
    assert "SECRET" not in result.reason


# M4: a filing dated D is public from D + 1.
@pytest.mark.unit
def test_as_of_boundary_excludes_same_day_filings() -> None:
    result = sec_result(FIXTURE)
    assert isinstance(result, Fetched)
    assert latest_reported(result.table, as_of=date(2024, 2, 15)).empty
    assert len(latest_reported(result.table, as_of=date(2024, 2, 16))) == 1


# M5: on the same filing day, the original comes before its amendment.
@pytest.mark.unit
def test_same_day_amendment_sorts_after_the_original() -> None:
    original = {
        **BASE_ITEM,
        "start": "2023-01-01",
        "val": 10.0,
        "accn": "0009999999-24-000001",
        "filed": "2024-02-15",
    }
    amended = {
        **BASE_ITEM,
        "start": "2023-01-01",
        "val": 9.0,
        "form": "10-K/A",
        "accn": "0000000001-24-000001",
        "filed": "2024-02-15",
    }
    result = sec_result(with_items([original, amended]))
    assert isinstance(result, Fetched)
    assert first_reported(result.table)["value"].item() == 10.0
    assert latest_reported(result.table)["value"].item() == 9.0


# M6: frame-level integrity checks.
@pytest.mark.unit
def test_filed_before_period_end_breaks_the_contract() -> None:
    item = {
        **BASE_ITEM,
        "start": "2023-01-01",
        "accn": "0001234567-24-000010",
        "filed": "2023-06-01",
    }
    assert isinstance(sec_result(with_items([item])), Unavailable)


@pytest.mark.unit
def test_instant_fact_without_start_dedups_correctly() -> None:
    one = {**BASE_ITEM, "val": 1.0, "accn": "0001234567-24-000010", "filed": "2024-02-15"}
    two = {
        **BASE_ITEM,
        "val": 2.0,
        "form": "10-K/A",
        "accn": "0001234567-24-000011",
        "filed": "2024-03-15",
    }
    result = sec_result(with_items([one, two]))
    assert isinstance(result, Fetched)
    assert first_reported(result.table)["value"].item() == 1.0
    assert latest_reported(result.table)["value"].item() == 2.0


# M7: the ticker table resists bad snapshots and keeps identity across gaps.
@pytest.mark.unit
def test_large_shrink_is_refused_unless_allowed() -> None:
    big = {f"T{i}": i + 1 for i in range(50)}
    table = TickerTable().observe(big, as_of=date(2026, 1, 5))
    with pytest.raises(ValueError, match="refusing"):
        table.observe({}, as_of=date(2026, 1, 6))
    assert (
        table.observe({}, as_of=date(2026, 1, 6), allow_large_shrink=True).lookup(
            "T1", date(2026, 1, 7)
        )
        is None
    )


@pytest.mark.unit
def test_returning_ticker_keeps_its_share_class() -> None:
    table = (
        TickerTable()
        .observe({"OLD": 333}, as_of=date(2026, 1, 5))
        .observe({"NEW": 333}, as_of=date(2026, 2, 2))
        .observe({}, as_of=date(2026, 3, 2))
        .observe({"NEW": 333}, as_of=date(2026, 4, 1))
    )
    assert table.lookup("NEW", date(2026, 4, 2)) == SecurityKey(cik=333, share_class="OLD")


@pytest.mark.unit
def test_duplicate_ticker_in_sec_file_is_an_error() -> None:
    payload = b'{"0": {"cik_str": 1, "ticker": "ABC", "title": "A"}, "1": {"cik_str": 2, "ticker": "abc", "title": "B"}}'
    with pytest.raises(ValueError, match="ABC"):
        parse_sec_ticker_file(payload)


# Low: conformance tasks and fixtures; config writing.
@pytest.mark.unit
def test_task_without_expectations_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "t.toml").write_text(
        'id = "x"\ndescription = "d"\nkind = "k"\n[input]\na = 1\n[expect]\n'
    )
    with pytest.raises(ValueError, match="expects"):
        load_tasks(tmp_path)


@pytest.mark.unit
def test_fixture_path_cannot_escape(tmp_path: Path) -> None:
    task = Task(
        id="x",
        description="d",
        kind="sec_concept",
        fixture="../secret.json",
        input={"cik": 1, "tag": "Revenues", "mode": "first_reported", "period_end": "2023-12-31"},
        expect={"value": 1.0},
    )
    with pytest.raises(ValueError, match="outside"):
        ReferenceBackend(tmp_path / "fixtures").solve(task)


@pytest.mark.unit
def test_data_dir_with_quotes_round_trips(tmp_path: Path) -> None:
    config = tmp_path / "foxcast.toml"
    odd = tmp_path / 'we"ird'
    write_default_config(config, data_dir=odd)
    assert load_settings(config).data_dir == odd
