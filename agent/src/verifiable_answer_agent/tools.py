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

Cloud Run authenticates at the edge as well, and it does not accept the end user's OAuth token
for that: the first real Gemini Enterprise turn was refused 401 before the service saw it
(2026-09-22). Google's own connector sends two credentials — its service identity in
``X-Serverless-Authorization``, which Cloud Run consumes and strips, and the user's token in
``Authorization``, which passes through untouched — so this callback does the same, minting an
ID token for the service's own audience from the runtime's credentials. The edge proves the
caller is allowed to reach the service at all; the service still proves the user separately, and
entitlements are still the user's. Where no such credential can be minted (a laptop signed in as
a person), the header is left out and the edge decides on the ``Authorization`` token alone.

The same callback adds ``X-Query-Turn-Id``. ``begin_turn`` — the agent's
``before_agent_callback`` — generates one UUID per invocation and puts it in session state,
also under a ``temp:`` key, before any tool call; every request of that turn then carries it,
and it is the ``turnId`` of the turn's audit record. The query-tools contract makes the
service's ``turnId`` optional — "when the caller declared one" — so a context with no turn id
sends only the ``Authorization`` header rather than an invented value.
"""

from __future__ import annotations

import functools
import time
import urllib.parse
import uuid
from collections.abc import Callable
from typing import Any, Final, cast

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams
from google.genai import types

from .audit import ToolCallRecord, ToolOutcome
from .config import AgentConfig
from .contract import QueryToolName, ToolResult, validate_tool_output

__all__ = [
    "EDGE_AUTH_HEADER",
    "QUERY_TOOL_NAMES",
    "TURN_ID_HEADER",
    "TURN_ID_STATE_KEY",
    "USER_TOKEN_STATE_KEY",
    "InvalidTurnIdError",
    "MissingUserTokenError",
    "bearer_header_provider",
    "begin_turn",
    "build_query_toolset",
    "current_turn_id",
    "read_tool_result",
    "record_for",
]

USER_TOKEN_STATE_KEY: Final = "temp:query_service_bearer_token"
# Cloud Run reads this one, consumes it, and removes it before the service sees the request.
EDGE_AUTH_HEADER: Final = "X-Serverless-Authorization"
TURN_ID_STATE_KEY: Final = "temp:query_turn_id"
TURN_ID_HEADER: Final = "X-Query-Turn-Id"

QUERY_TOOL_NAMES: Final[list[str]] = [
    "find_product",
    "get_section",
    "get_provenance",
    "verify_quote",
]


class MissingUserTokenError(RuntimeError):
    """No end-user token in session state. The tool call fails; nothing falls back."""


class InvalidTurnIdError(RuntimeError):
    """Session state carries a turn id that is not a UUID. The tool call fails."""


def begin_turn(callback_context: CallbackContext) -> types.Content | None:
    """``before_agent_callback``: one fresh UUID per invocation, in state before any tool call.

    Returns ``None`` so the agent runs as normal. It runs on every invocation, so each turn
    gets a new id; the ``temp:`` prefix is the one ADK does not persist between invocations.
    """
    callback_context.state[TURN_ID_STATE_KEY] = str(uuid.uuid4())
    return None


def current_turn_id(context: ReadonlyContext | CallbackContext) -> str | None:
    """The turn id in session state, or ``None`` when no turn has begun.

    A value that is present but not a UUID raises rather than being sent: the contract types
    ``turnId`` as a UUID and the header is what the service records under that name.
    """
    value = context.state.get(TURN_ID_STATE_KEY)
    if value is None:
        return None
    if not isinstance(value, str) or not _is_uuid(value):
        raise InvalidTurnIdError("the turn id in session state is not a UUID string")
    return value


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except ValueError:
        return False


# One ID token per audience, reused until it is nearly expired: Cloud Run is called on every MCP
# request and minting is a network call. Held in module scope, never logged, never in state.
_edge_tokens: dict[str, tuple[str, float]] = {}
_EDGE_TOKEN_REFRESH_BEFORE_SECONDS: Final = 300.0
# A failed mint is remembered too, briefly: where no credential can mint one, finding that out
# waits on the metadata server (about 3.4 s off Google Cloud), and every MCP request of a turn
# would wait again. Short, so a runtime whose credential appears is not refused for long.
_edge_failures: dict[str, float] = {}
_EDGE_FAILURE_RETRY_AFTER_SECONDS: Final = 60.0


def service_audience(mcp_url: str) -> str:
    """The Cloud Run service's own URL, which is the audience its edge check expects."""
    parts = urllib.parse.urlsplit(mcp_url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def edge_auth_token(audience: str, now: Callable[[], float] = time.time) -> str | None:
    """An ID token for the runtime's own identity, or None where one cannot be minted.

    None is not a fallback to some other identity: it means this process holds no credential that
    can mint one, as on a laptop signed in as a person. The request then carries only the user's
    token and Cloud Run decides on that. A token is reused until five minutes before the hour it
    is held for; a failure is remembered for a minute, so a turn's requests do not each wait for
    the same answer.
    """
    cached = _edge_tokens.get(audience)
    if cached is not None and cached[1] - _EDGE_TOKEN_REFRESH_BEFORE_SECONDS > now():
        return cached[0]
    failed_until = _edge_failures.get(audience)
    if failed_until is not None and failed_until > now():
        return None
    token = _mint_edge_token(audience)
    if token is None:
        _edge_failures[audience] = now() + _EDGE_FAILURE_RETRY_AFTER_SECONDS
        return None
    _edge_failures.pop(audience, None)
    # An ID token's own expiry is a minute-level fact; a fixed hour is well inside it.
    _edge_tokens[audience] = (token, now() + 3600.0)
    return token


def _mint_edge_token(audience: str) -> str | None:
    """One attempt to mint an ID token for ``audience``, uncached; ``None`` if none can be."""
    try:
        import google.auth.transport.requests
        from google.auth import default as default_credentials
        from google.auth import exceptions as auth_exceptions
        from google.oauth2 import id_token as google_id_token
    except ImportError:  # pragma: no cover - google-auth is a runtime dependency
        return None
    request = google.auth.transport.requests.Request()
    try:
        fetch = cast(Callable[[Any, str], str], google_id_token.fetch_id_token)
        return fetch(request, audience)
    except auth_exceptions.GoogleAuthError, OSError:
        pass
    # No ID token from the environment: a service-account credential can still sign one.
    try:
        credentials, _ = default_credentials()
    except auth_exceptions.GoogleAuthError:
        return None
    signer = getattr(credentials, "with_target_audience", None)
    if signer is None:
        return None
    try:
        scoped = signer(audience)
        scoped.refresh(request)
    except auth_exceptions.GoogleAuthError, OSError:
        return None
    return cast(str, scoped.token)


def bearer_header_provider(context: ReadonlyContext, audience: str | None = None) -> dict[str, str]:
    """Per-request headers for the MCP session. Fails closed when the user's token is absent.

    ``X-Query-Turn-Id`` is added when ``begin_turn`` has run for this invocation; it is left out
    otherwise, which the service accepts. ``X-Serverless-Authorization`` is added when an
    audience is known and this runtime can mint an ID token for it; Cloud Run consumes it, so the
    service still sees only the user's ``Authorization``.
    """
    token = context.state.get(USER_TOKEN_STATE_KEY)
    if not isinstance(token, str) or not token.strip():
        raise MissingUserTokenError(
            "no end-user bearer token in session state; refusing to call the query service"
        )
    headers = {"Authorization": f"Bearer {token}"}
    turn_id = current_turn_id(context)
    if turn_id is not None:
        headers[TURN_ID_HEADER] = turn_id
    if audience is not None:
        edge_token = edge_auth_token(audience)
        if edge_token is not None:
            headers[EDGE_AUTH_HEADER] = f"Bearer {edge_token}"
    return headers


def build_query_toolset(
    config: AgentConfig, header_provider: Callable[[ReadonlyContext], dict[str, str]] | None = None
) -> McpToolset:
    """The four tools and nothing else: ``tool_filter`` closes the surface at this end too."""
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=config.query_service_url, timeout=config.timeout_seconds
        ),
        tool_filter=list(QUERY_TOOL_NAMES),
        header_provider=(
            header_provider
            if header_provider is not None
            else functools.partial(
                bearer_header_provider, audience=service_audience(config.query_service_url)
            )
        ),
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
