"""The locked-down dossier folder ("the room"; Stage 1A.7, change T).

Dossiers are built by Claude Code started in a dedicated folder whose settings allow only
the foxcast tools. Hidden instructions in a filing then have nothing to act through: no
shell, no file reads or writes, no web, no subagents, no other MCP server. The room holds:

- ``.mcp.json``: the foxcast server, launched from the foxcast project with ``uv``;
- ``.claude/settings.json``: allow ``mcp__foxcast`` only, ``dontAsk`` mode (anything not
  allowed is refused, never prompted), every built-in tool denied by name, claude.ai
  connectors and hooks off;
- ``CLAUDE.md``: the workflow file, word for word. Its hash is the run's prompt hash;
- ``start.ps1`` and ``start.sh``: the launch command, which also loads only this folder's
  MCP config and settings (``--strict-mcp-config``, ``--setting-sources project,local``)
  and no built-in tools (``--tools ""``), so user-level servers and plugins stay out.

The folder settings are the first lock and the launch flags the second. Neither is
enforced against the operator, who can always start Claude Code another way. Managed
settings would be the only lock that cannot be overridden.
"""

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Final

from foxcast.mcp.server import workflow_text

DENIED_TOOLS: Final = (
    "Bash",
    "PowerShell",
    "Read",
    "Write",
    "Edit",
    "NotebookEdit",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Agent",
    "Task",
    "Skill",
)
INSTRUCTION_FILES: Final = ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md")
LAUNCH_FLAGS: Final = (
    "--strict-mcp-config",
    "--mcp-config",
    ".mcp.json",
    "--setting-sources",
    "project,local",
    "--permission-mode",
    "dontAsk",
    "--tools",
    '""',
)


# Windows PowerShell 5.1 drops an empty argument, which would leave --tools without its
# value; PowerShell 7.3 and later pass it when told to.
POWERSHELL_PREAMBLE: Final = """\
#Requires -Version 7.3
$ErrorActionPreference = 'Stop'
$PSNativeCommandArgumentPassing = 'Standard'
Set-Location -LiteralPath $PSScriptRoot
"""


def settings() -> dict[str, Any]:
    return {
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
        "permissions": {
            "defaultMode": "dontAsk",
            "disableBypassPermissionsMode": "disable",
            "allow": ["mcp__foxcast"],
            "deny": list(DENIED_TOOLS),
        },
        "enableAllProjectMcpServers": False,
        "enabledMcpjsonServers": ["foxcast"],
        "disableClaudeAiConnectors": True,
        "disableAllHooks": True,
    }


def mcp_config(
    project: Path, config: Path | None, as_of: str, companies: Mapping[str, int]
) -> dict[str, Any]:
    args = ["run", "--project", str(project), "--frozen", "python", "-m", "foxcast.mcp", "serve"]
    if config is not None:
        args += ["--config", str(config)]
    args += ["--as-of", as_of]
    for ticker, cik in sorted(companies.items()):
        args += ["--company", f"{ticker}:{cik}"]
    return {"mcpServers": {"foxcast": {"type": "stdio", "command": "uv", "args": args}}}


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _parent_instructions(folder: Path) -> list[Path]:
    """Instruction files above the room. The user's own memory file is left out: it loads
    wherever Claude Code starts, so moving the room would not help."""
    user_memory = Path.home().resolve() / ".claude" / "CLAUDE.md"
    return [
        parent / name
        for parent in folder.parents
        for name in INSTRUCTION_FILES
        if (parent / name).is_file() and (parent / name).resolve() != user_memory
    ]


def write_room(
    folder: Path,
    *,
    project: Path,
    as_of: str,
    companies: Mapping[str, int] | None = None,
    config: Path | None = None,
) -> list[Path]:
    """Write the room's files into ``folder``; return the paths written.

    The server is pinned to ``as_of`` and, if given, to ``companies`` (ticker to CIK), so
    neither the agent nor a filing can move the run to another date or company. The room
    may not sit inside the foxcast project or below any folder holding Claude Code
    instructions: Claude Code reads those from parent folders too, and they would reach
    the agent without being part of the prompt hash. (The user's own ``~/.claude``
    instructions still load; a gate run records that as a known limit.)
    """
    folder, project = folder.resolve(), project.resolve()
    date.fromisoformat(as_of)
    if _inside(folder, project):
        raise ValueError(f"the room {folder} must be outside the foxcast project {project}")
    found = _parent_instructions(folder)
    if found:
        raise ValueError(f"the room {folder} is below instruction files: {found}")
    (folder / ".claude").mkdir(parents=True, exist_ok=True)
    command = "claude " + " ".join(LAUNCH_FLAGS)
    files = {
        folder / ".mcp.json": json.dumps(
            mcp_config(project, config, as_of, companies or {}), indent=2
        )
        + "\n",
        folder / ".claude" / "settings.json": json.dumps(settings(), indent=2) + "\n",
        folder / "CLAUDE.md": workflow_text(),
        folder / "start.ps1": POWERSHELL_PREAMBLE + f"{command}\n",
        folder / "start.sh": f'#!/bin/sh\ncd "$(dirname "$0")" || exit 1\nexec {command}\n',
    }
    for path, text in files.items():
        path.write_text(text, encoding="utf-8", newline="\n")
    return list(files)
