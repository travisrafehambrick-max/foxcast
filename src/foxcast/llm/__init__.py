"""The Stage 1B research-claims backend seam.

Backends drive the locked-down Stage 1A.7 MCP tool server through a
:class:`~foxcast.llm.tools.ToolCaller`. The reference backend is a fixture agent with
no external model.
"""

from foxcast.llm.claims import (
    ClaimsBackend,
    ClaimsRequest,
    ClaimsRunResult,
    LoggedCall,
    ReferenceClaimsBackend,
)
from foxcast.llm.tools import InProcessToolCaller, ToolCaller

__all__ = [
    "ClaimsBackend",
    "ClaimsRequest",
    "ClaimsRunResult",
    "InProcessToolCaller",
    "LoggedCall",
    "ReferenceClaimsBackend",
    "ToolCaller",
]
