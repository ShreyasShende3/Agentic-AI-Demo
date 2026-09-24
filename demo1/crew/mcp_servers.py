"""MCP server wiring, with least-privilege scoping built in two layers:

1. Directory scope (a real boundary): the invoices-reading agent is only
   ever pointed at data/invoices/, the decisions-reading agent only at
   data/decisions/. Neither server connection is ever handed a write
   tool at all - see pipeline.py for why decisions.json is written
   directly by Python instead of by any agent.

2. Tool-list scope (defense in depth, and the more honest lesson): the
   official filesystem server exposes read AND write tools on every
   directory it's given - there is no server-side per-directory read-only
   flag outside of a Docker `ro` bind mount. So every agent here is only
   ever handed the read_* tool objects out of what the server discovers;
   nothing stops us from handing out write_file too except our own care
   in this file. Worth saying out loud in the talk: an MCP server's own
   tool list is not automatically an access-control boundary - the caller
   has to enforce that. The same principle applies to the memory server
   below: the Insights chat agent only ever gets the read-only
   `read_graph` tool out of it, never create_entities/add_observations.

All servers here are official, free, MIT-licensed reference
implementations (@modelcontextprotocol/server-filesystem,
@modelcontextprotocol/server-memory), run locally via npx - no API keys,
no network dependency beyond the one-time npx download.
"""

from __future__ import annotations

from mcp import StdioServerParameters

from . import config

_cmd, _prefix_args = config.NPX_COMMAND


def _npx_params(pkg_args: list[str], env: dict | None = None) -> StdioServerParameters:
    return StdioServerParameters(
        command=_cmd,
        args=[*_prefix_args, "-y", *pkg_args],
        env=env,
    )


def read_only_invoices_server() -> StdioServerParameters:
    config.INVOICES_DIR.mkdir(parents=True, exist_ok=True)
    return _npx_params(["@modelcontextprotocol/server-filesystem", str(config.INVOICES_DIR)])


def decisions_server() -> StdioServerParameters:
    """Rooted at data/decisions/, used read-only by the Audit Reporter.
    No agent ever gets a write tool from this server - the one file it
    ever contains (decisions.json) is written directly by pipeline.py,
    outside any agent's control, only after a human approves the draft."""
    config.DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    return _npx_params(["@modelcontextprotocol/server-filesystem", str(config.DECISIONS_DIR)])


def memory_server() -> StdioServerParameters:
    config.MEMORY_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    import os

    return _npx_params(
        ["@modelcontextprotocol/server-memory"],
        env={**os.environ, "MEMORY_FILE_PATH": str(config.MEMORY_FILE_PATH)},
    )


# read_file/read_text_file are deliberately excluded: crewai-tools' MCP
# adapter sends their unset optional `head`/`tail` args through as explicit
# JSON null, and the official filesystem server's schema rejects null
# (only accepts a number or an omitted field) - a real adapter/server
# interoperability rough edge, not something fixable from this side.
# read_multiple_files takes a single required `paths` array with no
# optional fields, so it doesn't hit this and is used for every read.
READ_ONLY_TOOL_NAMES = {"list_allowed_directories", "list_directory", "read_multiple_files", "get_file_info"}
READ_DECISIONS_TOOL_NAMES = {"list_allowed_directories", "read_multiple_files"}
READ_MEMORY_TOOL_NAMES = {"read_graph"}


def filter_tools(mcp_tools, allowed_names: set[str]) -> list:
    """Hand an agent only the subset of a connected server's discovered
    tools that its role actually needs - the enforcement point described
    in this module's docstring, point 2."""
    return [t for t in mcp_tools if t.name in allowed_names]
