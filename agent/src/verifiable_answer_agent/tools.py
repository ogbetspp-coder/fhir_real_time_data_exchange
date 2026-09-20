"""The query service's four tools, over streamable HTTP, as the end user.

Entitlements are per principal, so the identity that reaches the query service must be the end
user's. ADK's ``McpToolset`` takes a ``header_provider`` callback that it invokes with a
``ReadonlyContext`` for every MCP request, and the callback reads the user's bearer token out
of session state. There is no fallback: if the token is absent, the callback raises and the
tool call fails. A default credential here would be the agent asking on its own authority, and
the design note says plainly that the delegated-trust fallback is not needed and is not built.

The token lives under a ``temp:`` key, which ADK does not persist beyond the invocation. It is
never read into a variable that outlives the callback, never put in a log line, and never
carried into the audit record.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Final, cast

from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

from .audit import ToolCallRecord, ToolOutcome
from .config import AgentConfig
from .contract import QueryToolName, ToolResult, validate_tool_output

__all__ = [
    "QUERY_TOOL_NAMES",
    "USER_TOKEN_STATE_KEY",
    "MissingUserTokenError",
    "bearer_header_provider",
    "build_query_toolset",
    "read_tool_result",
    "record_for",
]

USER_TOKEN_STATE_KEY: Final = "temp:query_service_bearer_token"

QUERY_TOOL_NAMES: Final[list[str]] = [
    "find_product",
    "get_section",
    "get_provenance",
    "verify_quote",
]


class MissingUserTokenError(RuntimeError):
    """No end-user token in session state. The tool call fails; nothing falls back."""


def bearer_header_provider(context: ReadonlyContext) -> dict[str, str]:
    """Per-request headers for the MCP session. Fails closed when the token is absent."""
    token = context.state.get(USER_TOKEN_STATE_KEY)
    if not isinstance(token, str) or not token.strip():
        raise MissingUserTokenError(
            "no end-user bearer token in session state; refusing to call the query service"
        )
    return {"Authorization": f"Bearer {token}"}


def build_query_toolset(
    config: AgentConfig, header_provider: Callable[[ReadonlyContext], dict[str, str]] | None = None
) -> McpToolset:
    """The four tools and nothing else: ``tool_filter`` closes the surface at this end too."""
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=config.query_service_url, timeout=config.timeout_seconds
        ),
        tool_filter=list(QUERY_TOOL_NAMES),
        header_provider=header_provider if header_provider is not None else bearer_header_provider,
    )


def read_tool_result(tool: QueryToolName, raw: object) -> ToolResult:
    """Unwrap what ADK returns from an MCP call, then validate it against the contract.

    ``McpTool.run_async`` returns the serialised ``CallToolResult`` — ``content``,
    ``structuredContent``, ``isError`` — not the tool's own output. The structured content is
    the payload the contract describes; anything else, including an error result, is
    unavailable.
    """
    if not isinstance(raw, dict):
        return ToolResult(tool=tool, value=None, reason="transport-error")
    payload = cast(dict[str, Any], raw)
    if payload.get("isError"):
        return ToolResult(tool=tool, value=None, reason="tool-error")
    if "structuredContent" not in payload:
        return ToolResult(tool=tool, value=None, reason="not-an-object")
    return validate_tool_output(tool, payload["structuredContent"])


def record_for(tool: QueryToolName, result: ToolResult, started: float) -> ToolCallRecord:
    """The audit line for one call: outcome, duration, a count. No arguments, no content."""
    outcome: ToolOutcome = "ok" if result.available else (result.reason or "transport-error")
    value = result.value
    count = 0
    if value is not None:
        products = value.get("products")
        count = len(products) if isinstance(products, list) else 1
    return ToolCallRecord(
        tool=tool,
        outcome=outcome,
        duration_ms=max(0, round((time.monotonic() - started) * 1000)),
        result_count=count,
    )
