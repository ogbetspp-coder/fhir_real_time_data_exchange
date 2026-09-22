"""The turn's end: what the model drafted becomes a checked, rendered answer, or does not show.

``turn.answer_turn`` has always done the work — compose deterministically, re-check every
quotation through ``verify_quote``, render, record. Until 2026-09-22 nothing called it in the
deployed agent: the reply a person saw in Gemini Enterprise was the model's own text, and the
query service's audit record for the first live turn shows ``find_product`` and ``get_section``
and no ``verify_quote``. The answer happened to be right. That is not the claim this product
makes.

``build_finish_turn`` returns the agent's ``after_agent_callback``. It reads this invocation's
``get_section`` results and the model's own words out of the session's events, calls
``verify_quote`` for every chunk of every quoted block through the same MCP toolset the model
used — so the call carries the user's token and the turn id, and lands in the same audit trail —
and returns the rendered answer, which replaces what the model wrote.

Failure is not silent and never falls back to the model's text: if the check cannot run, the
person is told that the answer could not be verified, because an unverifiable answer that looks
like a verified one is the failure this product exists to prevent.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Final, cast

from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from .audit import ToolCallRecord, emit
from .config import AgentConfig
from .contract import QueryToolName, ToolResult
from .tools import current_turn_id, read_tool_result, record_for
from .turn import answer_turn

__all__ = ["UNVERIFIABLE_NOTICE", "build_finish_turn", "draft_from_events"]

UNVERIFIABLE_NOTICE: Final = (
    "This answer could not be verified against the approved label, so it is not shown. "
    "Nothing here is label content. Ask again, and if this repeats, report it: the query "
    "service records every call."
)

# The tools whose results this turn's answer is built from. find_product results are recorded in
# the audit trail but contribute no quotation: they carry no narrative.
_QUOTED_FROM: Final[QueryToolName] = "get_section"


def draft_from_events(
    events: Sequence[Any], invocation_id: str
) -> tuple[list[ToolResult], list[ToolCallRecord], str]:
    """This invocation's section results, its tool-call records, and the model's own words.

    Events of other invocations are ignored: a session holds every turn, and an answer must be
    built from the turn that is being answered. The model's words are the last text it produced,
    which is the draft it would otherwise have shown.
    """
    section_results: list[ToolResult] = []
    tool_calls: list[ToolCallRecord] = []
    assistant_text = ""
    for event in events:
        if getattr(event, "invocation_id", None) != invocation_id:
            continue
        content = getattr(event, "content", None)
        for part in getattr(content, "parts", None) or []:
            response = getattr(part, "function_response", None)
            if response is not None and response.name in ("find_product", "get_section"):
                tool = cast(QueryToolName, response.name)
                result = read_tool_result(tool, response.response)
                # The duration is not recoverable from an event, so the record carries none
                # rather than an invented one; the query service's own record has the timing.
                tool_calls.append(record_for(tool, result, time.monotonic()))
                if tool == _QUOTED_FROM:
                    section_results.append(result)
            text = getattr(part, "text", None)
            if text and getattr(event, "author", None) != "user":
                assistant_text = text
    return section_results, tool_calls, assistant_text


def _verify_quote_through(
    toolset: McpToolset, tool_context: ToolContext
) -> Callable[[str, str, str, str], Awaitable[ToolResult]]:
    """``verify_quote`` as the same user, over the same MCP session the model's calls used."""

    async def verify_quote(
        bundle_id: str, version_id: str, source_key: str, quote: str
    ) -> ToolResult:
        tools = await toolset.get_tools(tool_context)
        tool = next((t for t in tools if t.name == "verify_quote"), None)
        if tool is None:
            return ToolResult(tool="verify_quote", value=None, reason="transport-error")
        raw = await tool.run_async(
            args={
                "bundleId": bundle_id,
                "versionId": version_id,
                "sourceKey": source_key,
                "quote": quote,
            },
            tool_context=tool_context,
        )
        return read_tool_result("verify_quote", raw)

    return verify_quote


def build_finish_turn(
    config: AgentConfig, toolset: McpToolset
) -> Callable[[CallbackContext], Awaitable[types.Content | None]]:
    """The agent's ``after_agent_callback``, bound to the toolset the model called."""

    async def finish_turn(callback_context: CallbackContext) -> types.Content | None:
        invocation = callback_context.get_invocation_context()
        session = invocation.session
        section_results, tool_calls, assistant_text = draft_from_events(
            session.events, invocation.invocation_id
        )
        turn_id = current_turn_id(callback_context)
        if turn_id is None:
            # begin_turn runs before every invocation, so this cannot happen in the deployed
            # agent; if it ever does, the turn is unverifiable rather than unchecked.
            return types.Content(role="model", parts=[types.Part(text=UNVERIFIABLE_NOTICE)])

        try:
            result = await answer_turn(
                section_results=section_results,
                assistant_text=assistant_text,
                verify_quote=_verify_quote_through(toolset, ToolContext(invocation)),
                surface="text",
                principal=session.user_id,
                service_version=config.service_version,
                turn_id=turn_id,
                tool_calls=tool_calls,
            )
        except Exception:
            return types.Content(role="model", parts=[types.Part(text=UNVERIFIABLE_NOTICE)])

        emit(result.audit)
        rendered = result.rendered
        text = rendered if isinstance(rendered, str) else UNVERIFIABLE_NOTICE
        return types.Content(role="model", parts=[types.Part(text=text)])

    return finish_turn
