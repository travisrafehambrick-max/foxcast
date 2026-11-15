"""Every fitted piece the walk-forward refits from the past only (honesty rule 12).

Each piece rule 12 lists is here with the rule's own words, plus the fitted pieces of
forecasts that already exist. Row 2d needs one lookahead test per piece. A piece that is
not built has no test yet and shows up as skipped with the reason given here, so it is
never counted as a pass. A test keeps this list in step with the rule's wording.
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class FittedPiece:
    name: str
    rule_12: str | None  # the piece's words in honesty rule 12; None if the rule does not name it
    built: bool
    note: str


FITTED_PIECES: Final[tuple[FittedPiece, ...]] = (
    FittedPiece(
        "naive_volatility",
        None,
        built=True,
        note="forecast 1's volatility from the last 63 sessions before the forecast day",
    ),
    FittedPiece(
        "volatility_model",
        None,
        built=True,
        note="forecast 2's GJR-GARCH fit, refit at every forecast date",
    ),
    FittedPiece(
        "event_effects",
        "event base rates and effect sizes",
        built=False,
        note="not built: forecast 3 needs an event calendar",
    ),
    FittedPiece(
        "factor_betas",
        "factor betas",
        built=False,
        note="not built: no factor model yet",
    ),
    FittedPiece(
        "option_bias_correction",
        "the option-implied bias correction",
        built=False,
        note="not built: forecast 5 needs a bias-correction fitter and option-chain history",
    ),
    FittedPiece(
        "blend",
        "the blend",
        built=False,
        note="not built: the combination method awaits proving experiment 5",
    ),
    FittedPiece(
        "sector_peer_labels",
        "sector and peer labels",
        built=False,
        note="not built: universe.py reads today's constituents file, not labels as of a date",
    ),
    FittedPiece(
        "conformal_record",
        "the conformal record",
        built=False,
        note="not built: no recalibration fitter (change W) consumes the record yet; "
        "its past-only input, settle.settled_before, is unit-tested",
    ),
)
