"""The fixed "research, not investment advice" notice.

Shown on the README and on every dossier and forecast (brief 2.1, section 19). Keep the
wording in one place so every output carries the same text.
"""

from typing import Final

NOTICE_HEADLINE: Final = "Research, not investment advice."

NOTICE_TEXT: Final = (
    "foxcast produces research dossiers and probability forecasts for educational and "
    "research purposes only. Nothing it outputs is financial, investment or trading advice, "
    "or a recommendation to buy or sell any security. It places no orders."
)
