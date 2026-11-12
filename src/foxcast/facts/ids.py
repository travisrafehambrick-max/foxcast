"""Stable identifiers built from content, so the same inputs always give the same ID."""

import hashlib
from typing import Final

ID_LENGTH: Final = 16
SEPARATOR: Final = "\x1f"  # ASCII unit separator: cannot appear in the parts


FACT_PREFIX: Final = "f"
PASSAGE_PREFIX: Final = "p"
PREFIXES: Final = frozenset({FACT_PREFIX, PASSAGE_PREFIX})


def canonical_id(*parts: str, prefix: str = FACT_PREFIX) -> str:
    """A short, stable ID: the first 16 hex characters of SHA-256 over the parts.

    ``f_`` IDs name facts (numbers, text and derived values); ``p_`` IDs name passages.
    """
    if prefix not in PREFIXES:
        raise ValueError(f"unknown ID prefix: {prefix!r}")
    if any(SEPARATOR in part for part in parts):
        raise ValueError("ID parts may not contain the unit separator character")
    digest = hashlib.sha256(SEPARATOR.join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:ID_LENGTH]}"
