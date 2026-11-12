"""The provider runner: unavailable versus no data, content hashing, data contracts."""

import hashlib
from datetime import UTC, datetime

import httpx
import pandas as pd
import pandera.pandas as pa
import pytest

from foxcast.sources.base import Fetched, NoData, Unavailable, run_provider
from foxcast.sources.throttle import Throttle

FIXED_NOW = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)
CONTRACT = pa.DataFrameSchema({"value": pa.Column(int, pa.Check.ge(0))})


class FakeProvider:
    name = "fake"
    contract = CONTRACT
    secret_params: frozenset[str] = frozenset({"api_key"})
    default_throttle = None
    allowed_content_types: frozenset[str] | None = None
    max_bytes = 1_000_000

    def mismatch(self, query: str, table: pd.DataFrame) -> str | None:
        return "wrong thing" if query == "wrong" else None

    def build_request(self, query: str) -> httpx.Request:
        return httpx.Request("GET", f"https://example.test/{query}")

    def shape(self, payload: bytes) -> pd.DataFrame:
        return pd.DataFrame({"value": [int(part) for part in payload.decode().split(",")]})


def client_for(status: int, body: bytes = b"") -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(status, content=body))
    )


def run(client: httpx.Client, query: str = "q") -> Fetched | Unavailable | NoData:
    return run_provider(FakeProvider(), query, client, now=lambda: FIXED_NOW)


@pytest.mark.unit
def test_success_returns_shaped_table_with_hash_and_fetch_time() -> None:
    result = run(client_for(200, b"1,2,3"))
    assert isinstance(result, Fetched)
    assert list(result.table["value"]) == [1, 2, 3]
    assert result.content_sha256 == hashlib.sha256(b"1,2,3").hexdigest()
    assert result.raw == b"1,2,3"
    assert result.fetched_at == FIXED_NOW
    assert result.url == "https://example.test/q"


@pytest.mark.unit
def test_not_found_is_no_data_not_unavailable() -> None:
    assert isinstance(run(client_for(404)), NoData)


@pytest.mark.unit
@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_errors_are_unavailable_with_reason(status: int) -> None:
    result = run(client_for(status))
    assert isinstance(result, Unavailable)
    assert str(status) in result.reason


@pytest.mark.unit
def test_network_failure_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    result = run(httpx.Client(transport=httpx.MockTransport(handler)))
    assert isinstance(result, Unavailable)
    assert "down" in result.reason


@pytest.mark.unit
def test_contract_breach_is_unavailable_not_silently_passed() -> None:
    result = run(client_for(200, b"1,-5"))
    assert isinstance(result, Unavailable)
    assert "contract" in result.reason


@pytest.mark.unit
def test_unparseable_payload_is_unavailable() -> None:
    result = run(client_for(200, b"not,numbers"))
    assert isinstance(result, Unavailable)
    assert "shape" in result.reason


@pytest.mark.unit
def test_throttle_spaces_calls() -> None:
    clock = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    throttle = Throttle(max_per_second=10, clock=lambda: clock[0], sleep=sleep)
    throttle.wait()
    throttle.wait()
    clock[0] += 0.5
    throttle.wait()
    assert sleeps == [pytest.approx(0.1)]
