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
from typing import Any, Final, Protocol, TypeVar

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
    # Query parameters holding keys; masked in every stored or reported URL.
    secret_params: frozenset[str]
    # The source's fair-access limit; used unless the caller passes its own throttle.
    default_throttle: Throttle | None
    # Media types the response may have (None: not checked), and the largest body read.
    allowed_content_types: frozenset[str] | None
    max_bytes: int

    def build_request(self, query: QueryT_contra) -> httpx.Request: ...

    def shape(self, payload: bytes) -> pd.DataFrame: ...

    def mismatch(self, query: QueryT_contra, table: pd.DataFrame) -> str | None: ...


SHAPE_ERRORS: Final = (
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    OverflowError,
    json.JSONDecodeError,
)
CONTRACT_ERRORS: Final = (pa_errors.SchemaError, pa_errors.SchemaErrors, TypeError, ValueError)
REDACTED: Final = "REDACTED"


def redact_url(url: str, secret_params: frozenset[str]) -> str:
    """Mask secret query parameters, so keys never reach logs, the store or a dossier."""
    if not secret_params:
        return url
    parsed = httpx.URL(url)
    masked = [
        (key, REDACTED if key in secret_params else value)
        for key, value in parsed.params.multi_items()
    ]
    return str(parsed.copy_with(params=masked))


def _scrub(text: str, raw_url: str, safe_url: str) -> str:
    return text.replace(raw_url, safe_url) if raw_url != safe_url else text


DEFAULT_MAX_BYTES: Final = 50_000_000


def _refuse_response(
    provider: "Provider[Any]", response: httpx.Response, name: str, url: str
) -> FetchResult | None:
    """Status, media type and declared size, checked before any of the body is read."""
    if response.status_code == HTTPStatus.NOT_FOUND:
        return NoData(f"{name}: not found (HTTP 404)", url)
    if not HTTPStatus.OK <= response.status_code < HTTPStatus.MULTIPLE_CHOICES:
        return Unavailable(f"{name}: HTTP {response.status_code}", url)
    allowed = provider.allowed_content_types
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if allowed is not None and content_type not in allowed:
        return Unavailable(f"{name}: unexpected content type {content_type!r}", url)
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > provider.max_bytes:
        return Unavailable(f"{name}: response is larger than {provider.max_bytes} bytes", url)
    return None


def _read_capped(response: httpx.Response, cap: int) -> bytes | None:
    """The body, or None as soon as it grows past ``cap`` (after decompression)."""
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > cap:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


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
    name = provider.name
    try:
        request = provider.build_request(query)
    except (ValueError, httpx.InvalidURL) as exc:
        return Unavailable(f"{name}: could not build request: {exc}", "")
    raw_url = str(request.url)
    url = redact_url(raw_url, provider.secret_params)

    limiter = throttle or provider.default_throttle
    if limiter is not None:
        limiter.wait()
    try:
        response = client.send(request, stream=True)
    except httpx.HTTPError as exc:
        return Unavailable(_scrub(f"{name}: request failed: {exc}", raw_url, url), url)
    try:
        refusal = _refuse_response(provider, response, name, url)
        if refusal is not None:
            return refusal
        raw = _read_capped(response, provider.max_bytes)
    except httpx.HTTPError as exc:
        return Unavailable(_scrub(f"{name}: reading failed: {exc}", raw_url, url), url)
    finally:
        response.close()
    if raw is None:
        return Unavailable(f"{name}: response is larger than {provider.max_bytes} bytes", url)

    fetched_at = now()
    try:
        shaped = provider.shape(raw)
    except SHAPE_ERRORS as exc:
        return Unavailable(f"{name}: could not shape payload: {exc}", url)
    if shaped.empty:
        return NoData(f"{name}: the source returned no rows", url)
    try:
        table = provider.contract.validate(shaped, lazy=True)
    except CONTRACT_ERRORS as exc:
        return Unavailable(f"{name}: data contract failed: {exc}", url)
    problem = provider.mismatch(query, table)
    if problem is not None:
        return Unavailable(f"{name}: wrong subject: {problem}", url)
    return Fetched(
        table=table,
        raw=raw,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        url=url,
        fetched_at=fetched_at,
    )
