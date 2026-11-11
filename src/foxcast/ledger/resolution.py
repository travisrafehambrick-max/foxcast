"""The written resolution rule every forecast record carries (brief changes P and AU).

It is fixed before the forecast is made. A stock that stops trading still resolves to a
price whenever one exists, so bankruptcies and takeovers stay in the record instead of
being voided away (which would flatter tail coverage).
"""

from types import MappingProxyType
from typing import Final

RESOLUTION_RULE_VERSION: Final = 1

RESOLUTION_RULE: Final = MappingProxyType(
    {
        "version": str(RESOLUTION_RULE_VERSION),
        "return": (
            "Split- and dividend-adjusted return from the first market close after the freeze "
            "to the close at the horizon."
        ),
        "delisting": "Resolves to the last traded price before delisting.",
        "takeover": "Resolves to the takeover consideration per share (cash plus stock value).",
        "halt": "Resolves at the first close after trading resumes, or by the delisting rule.",
        "void": "Void only when no price exists; the void rate is reported beside every score.",
    }
)
