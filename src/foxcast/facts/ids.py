"""Stable identifiers built from content, so the same inputs always give the same ID."""

import hashlib
from typing import Final

ID_LENGTH: Final = 16
SEPARATOR: Final = "\x1f"  # ASCII unit separator: cannot appear in the parts


def canonical_id(*parts: str) -> str:
    """A short, stable ID: the first 16 hex characters of SHA-256 over the parts."""
    if any(SEPARATOR in part for part in parts):
        raise ValueError("ID parts may not contain the unit separator character")
    digest = hashlib.sha256(SEPARATOR.join(parts).encode("utf-8")).hexdigest()
    return f"f_{digest[:ID_LENGTH]}"
