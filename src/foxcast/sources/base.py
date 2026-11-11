"""The common provider interface and the runner every source goes through (brief F1).

A provider builds a request and shapes the raw payload into a table. The runner does the
rest, the same way for every source:

- a network failure or an error status becomes ``Unavailable`` with the reason;
- "not found" becomes ``NoData``, which is a different thing from "could not fetch";
- the raw payload is kept with its SHA-256 hash and fetch time (brief F2);
- the shaped table must pass the provider's data contract, or it becomes ``Unavailable``
  (brief change AI). Bad data is never passed on silently.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Protocol, TypeVar

import httpx
import pandas as pd
import pandera.errors as pa_errors
import pandera.pandas as pa

from foxcast.sources.throttle import Throttle

QueryT_contra = TypeVar("QueryT_contra", contravariant=True)


@dataclass(frozen=True, eq=False)
class Fetched:
    table: pd.DataFrame
    raw: bytes
    content_sha256: str
    url: str
    fetched_at: datetime


@dataclass(frozen=True)
class Unavailable:
    reason: str
    url: str


@dataclass(frozen=True)
class NoData:
    reason: str
    url: str


FetchResult = Fetched | Unavailable | NoData


class Provider(Protocol[QueryT_contra]):
    name: str
    contract: pa.DataFrameSchema

    def build_request(self, query: QueryT_contra) -> httpx.Request: ...

    def shape(self, payload: bytes) -> pd.DataFrame: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def run_provider[QueryT_contra](
    provider: Provider[QueryT_contra],
    query: QueryT_contra,
    client: httpx.Client,
    *,
    throttle: Throttle | None = None,
    now: Callable[[], datetime] = _utc_now,
) -> FetchResult:
    request = provider.build_request(query)
    url = str(request.url)
    if throttle is not None:
        throttle.wait()
    try:
        response = client.send(request)
    except httpx.HTTPError as exc:
        return Unavailable(f"{provider.name}: request failed: {exc}", url)
    if response.status_code == HTTPStatus.NOT_FOUND:
        return NoData(f"{provider.name}: not found (HTTP 404)", url)
    if response.status_code >= HTTPStatus.BAD_REQUEST:
        return Unavailable(f"{provider.name}: HTTP {response.status_code}", url)

    raw = response.content
    fetched_at = now()
    try:
        shaped = provider.shape(raw)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return Unavailable(f"{provider.name}: could not shape payload: {exc}", url)
    try:
        table = provider.contract.validate(shaped, lazy=True)
    except (pa_errors.SchemaError, pa_errors.SchemaErrors) as exc:
        return Unavailable(f"{provider.name}: data contract failed: {exc}", url)
    return Fetched(
        table=table,
        raw=raw,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        url=url,
        fetched_at=fetched_at,
    )
