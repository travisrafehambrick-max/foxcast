"""How a claims backend talks to the Stage 1A.7 tool server.

:class:`ToolCaller` is the transport seam. The in-process adapter only forwards to
:meth:`foxcast.mcp.server.ToolServer.call`, so it exercises the same validation and
permission path as a call over the wire. A later stdio or API adapter implements the
same Protocol without changing :class:`~foxcast.llm.claims.ClaimsBackend` code.
"""

from collections.abc import Mapping
from typing import Any, Protocol

from foxcast.mcp.server import ToolServer


class ToolCaller(Protocol):
    """One way to invoke a foxcast research tool and receive its envelope."""

    def call(self, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]: ...


class InProcessToolCaller:
    """Drive a :class:`ToolServer` in-process through its public ``call`` entry point."""

    def __init__(self, server: ToolServer) -> None:
        self._server = server

    def call(self, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self._server.call(tool, dict(arguments))
