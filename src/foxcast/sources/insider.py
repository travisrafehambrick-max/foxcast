"""Form 4 insider trades as facts (Stage 1A.3; brief F1).

A Form 4 reports trades by a company's directors, officers and 10% owners. Each trade in
the non-derivative table becomes numbers (shares, price, shares owned after) and text
(owners, roles, transaction code, security, whether it was under a 10b5-1 plan), all
citing the filing. Not read yet, and stated by the dossier as unavailable: derivative
trades (options, units), holdings that were only listed, and footnotes.

A joint filing (several reporting owners for the same trades) lists every owner.

The submissions index points at a rendered view (``xslF345X06/ownership.xml``); the raw
XML sits in the same folder without that prefix.

Untrusted input:

- the payload must be strict UTF-8 and declare no other encoding, and any DOCTYPE or
  ENTITY declaration is refused before parsing, so entity-expansion and external-entity
  attacks have nothing to work with (a UTF-16 payload cannot slip past the screen);
- owner names and security titles are chosen by the filer and shown to the model, so a
  filing whose text is too long or holds hidden characters is refused as a whole;
- numbers must be finite.
"""

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, cast

import httpx
import pandas as pd
import pandera.pandas as pa

from foxcast.facts.ids import canonical_id
from foxcast.facts.record import (
    MAX_TEXT_CHARS,
    SEC_PUBLIC_LAG,
    Fact,
    TextFact,
    has_hidden_characters,
    text_fact,
)
from foxcast.sources.base import Fetched
from foxcast.sources.filing_text import SEC_ARCHIVE_HOST
from foxcast.sources.sec import ACCESSION_PATTERN, SEC_THROTTLE
from foxcast.sources.throttle import Throttle

RENDERED_PREFIX: Final = re.compile(r"^xsl[A-Za-z0-9]+/")
UNSAFE_XML: Final = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
DECLARED_ENCODING: Final = re.compile(r"""^\s*<\?xml[^>]*encoding\s*=\s*["']([^"']+)["']""")
UTF8_NAMES: Final = frozenset({"utf-8", "utf8", "us-ascii", "ascii"})
BOM: Final = chr(0xFEFF)  # byte-order mark
# SEC Form 4 transaction codes (General Instructions, item 8).
TRANSACTION_CODES: Final = frozenset("PSVADFIMCEHOXGLWZJKU")
ROLES: Final = (
    ("isDirector", "director"),
    ("isOfficer", "officer"),
    ("isTenPercentOwner", "ten percent owner"),
    ("isOther", "other"),
)
COLUMNS: Final = (
    "issuer_cik",
    "index",
    "owner_ciks",
    "owner_name",
    "roles",
    "security",
    "transaction_date",
    "code",
    "acquired_disposed",
    "shares",
    "price",
    "owned_after",
    "ten_b5_one",
)
NUMBER_FIELDS: Final = (("shares", "shares"), ("price", "USD/shares"), ("owned_after", "shares"))


def raw_ownership_document(primary_document: str) -> str:
    """The raw XML behind the index's rendered view."""
    return RENDERED_PREFIX.sub("", primary_document)


@dataclass(frozen=True)
class Form4Query:
    cik: int  # the issuer
    accession: str
    document: str = "ownership.xml"

    def __post_init__(self) -> None:
        if self.cik < 1:
            raise ValueError(f"CIK must be positive, got {self.cik}")
        if not re.fullmatch(ACCESSION_PATTERN, self.accession):
            raise ValueError(f"not an accession number: {self.accession!r}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.xml", self.document, re.ASCII):
            raise ValueError(f"not a plain XML document name: {self.document!r}")


def _safe_root(payload: bytes) -> ET.Element:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"Form 4 XML must be UTF-8: {error}") from error
    text = text.removeprefix(BOM)
    declared = DECLARED_ENCODING.match(text)
    if declared and declared.group(1).strip().lower() not in UTF8_NAMES:
        raise ValueError(f"Form 4 XML declares encoding {declared.group(1)!r}; only UTF-8")
    if UNSAFE_XML.search(text):
        raise ValueError("XML with a DOCTYPE or ENTITY declaration is refused")
    try:
        root = ET.fromstring(text)  # noqa: S314 (screened above: UTF-8, no DTD)
    except ET.ParseError as error:
        raise ValueError(f"malformed XML: {error}") from error
    if root.tag != "ownershipDocument":
        raise ValueError(f"not an ownership document: <{root.tag}>")
    return root


def _text(node: ET.Element | None, path: str) -> str:
    found = None if node is None else node.find(path)
    return (found.text or "").strip() if found is not None else ""


def _free_text(node: ET.Element | None, path: str) -> str:
    """Filer-chosen text, refused if a reader could not see all of it."""
    value = _text(node, path)
    if len(value) > MAX_TEXT_CHARS or has_hidden_characters(value):
        raise ValueError(f"{path}: text is too long or holds hidden characters")
    return value


def _number(node: ET.Element, path: str) -> float | None:
    text = _text(node, path)
    if not text:
        return None
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"{path}: not a finite number: {text!r}")
    return value


def _flag(node: ET.Element | None, path: str) -> bool:
    return _text(node, path).lower() in {"1", "true"}


def _owners(root: ET.Element) -> tuple[tuple[int, ...], str, tuple[str, ...]]:
    owners = root.findall("reportingOwner")
    if not owners:
        raise ValueError("a Form 4 names at least one reporting owner")
    ciks = tuple(int(_text(o, "reportingOwnerId/rptOwnerCik")) for o in owners)
    names = "; ".join(_free_text(o, "reportingOwnerId/rptOwnerName") for o in owners)
    roles = tuple(
        label
        for tag, label in ROLES
        if any(_flag(o.find("reportingOwnerRelationship"), tag) for o in owners)
    )
    return ciks, names, roles


def _rows(root: ET.Element) -> list[dict[str, Any]]:
    issuer_cik = int(_text(root, "issuer/issuerCik"))
    owner_ciks, owner_name, roles = _owners(root)
    ten_b5_one = _flag(root, "aff10b5One")
    rows = []
    trades = root.findall("nonDerivativeTable/nonDerivativeTransaction")
    for index, trade in enumerate(trades, start=1):
        amounts = "transactionAmounts"
        rows.append(
            {
                "issuer_cik": issuer_cik,
                "index": index,
                "owner_ciks": owner_ciks,
                "owner_name": owner_name,
                "roles": roles,
                "security": _free_text(trade, "securityTitle/value"),
                "transaction_date": date.fromisoformat(_text(trade, "transactionDate/value")),
                "code": _text(trade, "transactionCoding/transactionCode"),
                "acquired_disposed": _text(
                    trade, f"{amounts}/transactionAcquiredDisposedCode/value"
                ),
                "shares": _number(trade, f"{amounts}/transactionShares/value"),
                "price": _number(trade, f"{amounts}/transactionPricePerShare/value"),
                "owned_after": _number(
                    trade, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"
                ),
                "ten_b5_one": ten_b5_one,
            }
        )
    return rows


FORM4_CONTRACT: Final = pa.DataFrameSchema(
    {
        "issuer_cik": pa.Column(int, pa.Check.ge(1)),
        "index": pa.Column(int, pa.Check.ge(1)),
        "owner_ciks": pa.Column(object),
        "owner_name": pa.Column(str, pa.Check.str_length(min_value=1, max_value=MAX_TEXT_CHARS)),
        "roles": pa.Column(object),
        "security": pa.Column(str, pa.Check.str_length(min_value=1, max_value=MAX_TEXT_CHARS)),
        "transaction_date": pa.Column(object),
        "code": pa.Column(str, pa.Check.isin(sorted(TRANSACTION_CODES))),
        "acquired_disposed": pa.Column(str, pa.Check.isin(["A", "D"])),
        "shares": pa.Column(float, pa.Check.ge(0)),
        "price": pa.Column(float, pa.Check.ge(0), nullable=True),
        "owned_after": pa.Column(float, pa.Check.ge(0), nullable=True),
        "ten_b5_one": pa.Column(bool),
    },
    strict=True,
)


class SecForm4Provider:
    """The trades in one Form 4, from its raw XML on EDGAR."""

    name = "sec_form4"
    contract = FORM4_CONTRACT
    secret_params: frozenset[str] = frozenset()
    default_throttle: Throttle | None = SEC_THROTTLE
    allowed_content_types: frozenset[str] | None = frozenset({"text/xml", "application/xml"})
    max_bytes: int = 5_000_000

    def __init__(self, contact: str) -> None:
        if not contact.strip():
            raise ValueError("SEC requires a contact name and email in the User-Agent header")
        self._contact = contact

    def build_request(self, query: Form4Query) -> httpx.Request:
        folder = query.accession.replace("-", "")
        url = f"{SEC_ARCHIVE_HOST}/Archives/edgar/data/{query.cik}/{folder}/{query.document}"
        return httpx.Request("GET", url, headers={"User-Agent": self._contact})

    def shape(self, payload: bytes) -> pd.DataFrame:
        table = pd.DataFrame(_rows(_safe_root(payload)), columns=list(COLUMNS))
        return table.astype(
            {
                "issuer_cik": "int64",
                "index": "int64",
                "shares": "float64",
                "price": "float64",
                "owned_after": "float64",
                "ten_b5_one": "bool",
            }
        )

    def mismatch(self, query: Form4Query, table: pd.DataFrame) -> str | None:
        issuers = set(table["issuer_cik"])
        if issuers != {query.cik}:
            return f"Form 4 is for issuer {sorted(issuers)}, asked for {query.cik}"
        return None


@dataclass(frozen=True)
class _Trade:
    fetched: Fetched
    row: dict[str, Any]
    accession: str
    public_on: date

    @property
    def cik(self) -> int:
        return int(self.row["issuer_cik"])

    def field(self, name: str) -> str:
        return f"sec:form4:t{self.row['index']}:{name}"

    def number(self, name: str, unit: str, value: float) -> Fact:
        if not math.isfinite(value):
            raise ValueError(f"{self.field(name)}: not a finite number")
        end: date = self.row["transaction_date"]
        field = self.field(name)
        content = self.fetched.content_sha256
        return Fact(
            fact_id=canonical_id(
                content, str(self.cik), field, unit, "", end.isoformat(), self.accession
            ),
            cik=self.cik,
            field=field,
            unit=unit,
            period_start=None,
            period_end=end,
            value=float(value),
            public_on=self.public_on,
            fetched_at=self.fetched.fetched_at,
            source_url=self.fetched.url,
            content_sha256=content,
            accession=self.accession,
        )

    def text(self, name: str, value: str) -> TextFact:
        return text_fact(
            cik=self.cik,
            field=self.field(name),
            value=value,
            period_end=self.row["transaction_date"],
            public_on=self.public_on,
            fetched_at=self.fetched.fetched_at,
            source_url=self.fetched.url,
            content_sha256=self.fetched.content_sha256,
            accession=self.accession,
        )


def form4_facts(
    fetched: Fetched, *, accession: str, filed: date
) -> tuple[list[Fact], list[TextFact]]:
    """Numbers and text for every trade; public from the day after ``filed``."""
    numbers: list[Fact] = []
    texts: list[TextFact] = []
    for row in cast(list[dict[str, Any]], fetched.table.to_dict("records")):
        trade = _Trade(fetched, row, accession, filed + SEC_PUBLIC_LAG)
        for name, unit in NUMBER_FIELDS:
            if not pd.isna(row[name]):
                numbers.append(trade.number(name, unit, row[name]))
        values = {
            "owner": row["owner_name"],
            "role": ", ".join(row["roles"]) or "not stated",
            "code": row["code"],
            "acquired_disposed": row["acquired_disposed"],
            "security": row["security"],
            "10b5-1": "yes" if row["ten_b5_one"] else "no",
        }
        texts.extend(trade.text(name, value) for name, value in values.items())
    return numbers, texts
