"""Every tool's input, validated before a handler runs (Stage 1A.7).

Each model forbids unknown fields and uses strict types, so a string never stands in for a
number and a flag never stands in for a probability. IDs, tickers, accessions and dates
must match their exact patterns. These are structural limits only: a claim that is well
formed but breaks the numbers rule is *accepted* here and withheld by the renderer, so it
counts in row 1c's denominator instead of escaping it as an input error.
"""

from datetime import date
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from foxcast.claims.render import ClaimType

TICKER_PATTERN: Final = r"^[A-Z0-9][A-Z0-9.-]{0,9}$"
RUN_ID_PATTERN: Final = r"^[A-Z0-9][A-Z0-9.-]{0,9}-[0-9]{4}-[0-9]{2}-[0-9]{2}$"
FACT_ID_PATTERN: Final = r"^f_[0-9a-f]{16}$"
ACCESSION: Final = r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$"
FIELD_PREFIX: Final = r"^[A-Za-z0-9:_-]{0,120}$"
QUERY: Final = r"^[A-Za-z0-9 .,&'-]{3,100}$"
DATE: Final = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
MAX_CLAIMS_PER_CALL: Final = 25
MAX_CLAIM_TEXT_CHARS: Final = 2000  # the numbers rule's own limit is lower and withholds
MAX_CITES_PER_CLAIM: Final = 50  # the renderer's own limit is lower and withholds
MAX_CIK: Final = 9_999_999_999

Ticker = Annotated[str, StringConstraints(pattern=TICKER_PATTERN)]
RunId = Annotated[str, StringConstraints(pattern=RUN_ID_PATTERN)]
FactId = Annotated[str, StringConstraints(pattern=FACT_ID_PATTERN)]
IsoDate = Annotated[str, StringConstraints(pattern=DATE)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
# Windows treats these as devices, so a run folder may not start with one before a dot.
DEVICES: Final = ("CON", "PRN", "AUX", "NUL")
RESERVED_NAMES: Final = frozenset(
    {*DEVICES, *(f"{port}{i}" for port in ("COM", "LPT") for i in range(1, 10))}
)


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _Paged(_Input):
    run_id: RunId
    offset: int = Field(default=0, ge=0, le=1_000_000)


class ResearchGetContext(_Input):
    """Open a dossier run, or resume it if it exists."""

    ticker: Ticker
    cik: int = Field(ge=1, le=MAX_CIK)
    as_of: IsoDate
    model_name: Short = Field(description="The model you are, self-reported.")
    model_cutoff: Short = Field(description="Your stated training cutoff, self-reported.")
    holding: Literal["holds", "none", "not-declared"] = Field(
        default="not-declared", description="The operator's declaration, as the operator gave it."
    )

    @field_validator("as_of")
    @classmethod
    def _real_date(cls, value: str) -> str:
        date.fromisoformat(value)  # raises on 2026-02-30
        return value

    @field_validator("ticker")
    @classmethod
    def _not_a_device(cls, value: str) -> str:
        if value.split(".")[0] in RESERVED_NAMES:
            raise ValueError("this ticker cannot name a run folder")
        return value


class SecGetFacts(_Paged):
    """Numeric facts (XBRL figures, Form 4 amounts) and derived facts."""

    field_prefix: Annotated[str, StringConstraints(pattern=FIELD_PREFIX)] = "us-gaap:"
    limit: int = Field(default=50, ge=1, le=100)


class SecGetTextFacts(_Paged):
    """Text facts: 8-K item codes, Form 4 names and roles. Third-party text."""

    field_prefix: Annotated[str, StringConstraints(pattern=FIELD_PREFIX)] = "sec:"
    limit: int = Field(default=50, ge=1, le=100)


class SecListFilings(_Input):
    run_id: RunId


class SecReadPassages(_Paged):
    accession: Annotated[str, StringConstraints(pattern=ACCESSION)]
    limit: int = Field(default=8, ge=1, le=20)


class SecSearchPassages(_Paged):
    query: Annotated[str, StringConstraints(pattern=QUERY)] = Field(
        description="Words to find in the company's filing passages (any case)."
    )
    limit: int = Field(default=8, ge=1, le=20)


class FactsDerive(_Input):
    """Have code compute a growth, difference or ratio, so the claim can cite it."""

    run_id: RunId
    method: Literal["growth", "difference", "ratio"]
    a: FactId = Field(description="growth/difference: the earlier fact; ratio: the numerator")
    b: FactId = Field(description="growth/difference: the later fact; ratio: the denominator")


class ClaimInput(_Input):
    section: Annotated[str, StringConstraints(max_length=100)]
    type: ClaimType
    text: Annotated[str, StringConstraints(max_length=MAX_CLAIM_TEXT_CHARS)]
    cites: list[Annotated[str, StringConstraints(max_length=40)]] = Field(
        max_length=MAX_CITES_PER_CLAIM
    )
    probability: float | None = Field(default=None, allow_inf_nan=False)

    @field_validator("type", mode="before")
    @classmethod
    def _type_from_text(cls, value: object) -> object:
        # Strict mode does not turn JSON text into an enum; accept the exact value only.
        if isinstance(value, str) and value in {t.value for t in ClaimType}:
            return ClaimType(value)
        return value


class DossierSubmitClaims(_Input):
    run_id: RunId
    claims: list[ClaimInput] = Field(min_length=1, max_length=MAX_CLAIMS_PER_CALL)


class DossierRender(_Input):
    run_id: RunId


INPUTS: Final[dict[str, type[_Input]]] = {
    "research_get_context": ResearchGetContext,
    "sec_get_facts": SecGetFacts,
    "sec_get_text_facts": SecGetTextFacts,
    "sec_list_filings": SecListFilings,
    "sec_read_passages": SecReadPassages,
    "sec_search_passages": SecSearchPassages,
    "facts_derive": FactsDerive,
    "dossier_submit_claims": DossierSubmitClaims,
    "dossier_render": DossierRender,
}
