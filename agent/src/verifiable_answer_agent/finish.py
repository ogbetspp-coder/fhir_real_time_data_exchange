"""The turn's end: what the model drafted becomes a checked, rendered answer, or does not show.

``turn.check_turn`` does the work — compose deterministically, re-check every quotation through
``verify_quote``, render. Until 2026-09-22 nothing called it in the deployed agent: the reply a
person saw in Gemini Enterprise was the model's own text, and the query service's audit record
for the first live turn shows ``find_product`` and ``get_section`` and no ``verify_quote``. The
answer happened to be right. That is not the claim this product makes.

``build_finish_turn`` returns the agent's ``after_agent_callback``. It reads this invocation's
``get_section`` and ``find_product`` results out of the session's events and the model's own
words out of ``hold.DraftHold`` — where they were kept back from the person as the model
produced them — calls ``verify_quote`` for every chunk of every quoted block through the same MCP
toolset the model used — so the call carries the user's token and the turn id, and lands in the
same audit trail — and returns the rendered answer: the turn's only text.

Failure is not silent and never falls back to the model's text: if the check cannot run, the
person is told that the answer could not be verified, because an unverifiable answer that looks
like a verified one is the failure this product exists to prevent. If the query tools never
loaded, the model was not called, and the person is told the label service could not be reached.

Every way out of a turn writes one audit record saying which way it was (``outcome``), and the
record is built apart from the answer: a record that cannot be built never turns a checked
answer into a notice (audit AG-1 — an e-mail user id did exactly that to every live turn), and
a minimal record is written in its place. Nothing else here may log, so the record is the only
trace a failed turn leaves (audit AG-6).
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Final, cast, final

from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from .audit import (
    NIL_TURN_ID,
    ToolCallRecord,
    TurnAuditRecord,
    TurnOutcome,
    emit,
    minimal_record,
    principal_fields,
    turn_record,
)
from .compose import product_facts
from .config import AgentConfig, principal_digest_key
from .contract import QueryToolName, ToolResult
from .hold import DraftHold
from .tools import ToolCallLog, current_turn_id, read_tool_result, record_for
from .turn import CheckedTurn, check_turn

__all__ = [
    "TOOLS_UNAVAILABLE_NOTICE",
    "UNVERIFIABLE_NOTICE",
    "TurnFinisher",
    "build_finish_turn",
    "draft_from_events",
]

UNVERIFIABLE_NOTICE: Final = (
    "This answer could not be verified against the approved label, so it is not shown. "
    "Nothing here is label content. Ask again, and if this repeats, report it: the query "
    "service records every call."
)

TOOLS_UNAVAILABLE_NOTICE: Final = (
    "The approved-label service could not be reached, so no answer is given: an answer from "
    "anywhere else could not be checked. Ask again, and if this repeats, report it."
)

# The tools whose results this turn's answer is read from: get_section's become the quoted
# blocks; find_product's name each block's product and language.
_READ_FROM: Final[tuple[QueryToolName, ...]] = ("find_product", "get_section")


def draft_from_events(
    events: Sequence[Any], invocation_id: str
) -> tuple[list[ToolResult], list[ToolResult]]:
    """This invocation's ``get_section`` results and its ``find_product`` results, in order.

    Events of other invocations are ignored: a session holds every turn, and an answer must be
    built from the turn that is being answered. The model's words are not read from here: they
    never became events (``hold.DraftHold``). Nor are the tool-call records: ``ToolCallLog``
    took those as the calls ran, with their durations.
    """
    sections: list[ToolResult] = []
    products: list[ToolResult] = []
    for event in events:
        if getattr(event, "invocation_id", None) != invocation_id:
            continue
        content = getattr(event, "content", None)
        for part in getattr(content, "parts", None) or []:
            response = getattr(part, "function_response", None)
            if response is None or response.name not in _READ_FROM:
                continue
            tool = cast(QueryToolName, response.name)
            result = read_tool_result(tool, response.response)
            (sections if tool == "get_section" else products).append(result)
    return sections, products


@final
class _VerifyQuoteThrough:
    """``verify_quote`` as the same user, over the same MCP session the model's calls used.

    The tool is looked up once per turn, not once per chunk: each lookup was a ``tools/list``
    round trip (audit AG-10). Every call is timed and added to the turn's ``ToolCallLog``, so the
    post-check's calls are in the audit record as the model's are (audit AG-5).
    """

    def __init__(
        self, toolset: McpToolset, tool_context: ToolContext, log: ToolCallLog, invocation_id: str
    ) -> None:
        self._toolset = toolset
        self._tool_context = tool_context
        self._log = log
        self._invocation_id = invocation_id
        self._tool: BaseTool | None = None
        self._looked_up = False

    async def __call__(
        self, bundle_id: str, version_id: str, source_key: str, quote: str
    ) -> ToolResult:
        started = time.monotonic()
        tool = await self._verify_quote_tool()
        if tool is None:
            result = ToolResult(tool="verify_quote", value=None, reason="transport-error")
        else:
            try:
                raw = await tool.run_async(
                    args={
                        "bundleId": bundle_id,
                        "versionId": version_id,
                        "sourceKey": source_key,
                        "quote": quote,
                    },
                    tool_context=self._tool_context,
                )
            except BaseException:
                self._log.add(
                    self._invocation_id,
                    record_for(
                        "verify_quote",
                        ToolResult(tool="verify_quote", value=None, reason="transport-error"),
                        started,
                    ),
                )
                raise
            result = read_tool_result("verify_quote", raw)
        self._log.add(self._invocation_id, record_for("verify_quote", result, started))
        return result

    async def _verify_quote_tool(self) -> BaseTool | None:
        if not self._looked_up:
            tools = await self._toolset.get_tools(self._tool_context)
            self._tool = next((t for t in tools if t.name == "verify_quote"), None)
            self._looked_up = True
        return self._tool


def build_finish_turn(
    config: AgentConfig, toolset: McpToolset, hold: DraftHold, log: ToolCallLog | None = None
) -> Callable[[CallbackContext], Awaitable[types.Content | None]]:
    """The agent's ``after_agent_callback``, bound to the toolset the model called.

    A bound method, not a closure, so a deep copy of the agent keeps it with the copy's own hold,
    log and toolset (``hold`` says why that matters). ``log`` is the one the agent's tool
    callbacks write to; without one, only the post-check's own calls are recorded.
    """
    return TurnFinisher(
        config, toolset, hold, log if log is not None else ToolCallLog()
    ).finish_turn


@final
class TurnFinisher:
    """What the turn's end needs: the configuration, the model's toolset, the hold and the log."""

    def __init__(
        self, config: AgentConfig, toolset: McpToolset, hold: DraftHold, log: ToolCallLog
    ) -> None:
        self.config = config
        self.toolset = toolset
        self.hold = hold
        self.log = log

    async def finish_turn(self, callback_context: CallbackContext) -> types.Content | None:
        """Ends the turn with the checked, rendered answer, or with a notice saying it cannot.

        The held draft is composed, post-checked through ``verify_quote`` and rendered as text.
        A model request without the query tools ends the turn with ``TOOLS_UNAVAILABLE_NOTICE``;
        a failed model call, a missing turn id or any failure while checking ends it with
        ``UNVERIFIABLE_NOTICE``. Whichever it is, one audit record says so.
        """
        started = time.monotonic()
        invocation = callback_context.get_invocation_context()
        held = self.hold.take(invocation.invocation_id)
        principal, digest = principal_fields(
            str(getattr(invocation.session, "user_id", "") or ""), principal_digest_key()
        )
        outcome: TurnOutcome
        error_class: str | None = None
        checked: CheckedTurn | None = None
        turn_id = NIL_TURN_ID
        try:
            found = current_turn_id(callback_context)
        except Exception as error:  # noqa: BLE001 - a turn id the service would refuse is none
            found, error_class = None, type(error).__name__
        if found is not None:
            turn_id = found
        if held.tools_missing:
            outcome, shown = "tools-unavailable", TOOLS_UNAVAILABLE_NOTICE
        elif held.model_failed:
            outcome, shown = "model-failed", UNVERIFIABLE_NOTICE
        elif found is None:
            # begin_turn runs before every invocation, so this cannot happen in the deployed
            # agent; if it ever does, the turn is unverifiable rather than unchecked.
            outcome, shown = "turn-id-missing", UNVERIFIABLE_NOTICE
        else:
            # Everything past this point either renders a checked answer or says it could not:
            # the model's text is already held back, so no exception here can leave it as the
            # answer.
            try:
                sections, products = draft_from_events(
                    invocation.session.events, invocation.invocation_id
                )
                checked = await check_turn(
                    section_results=sections,
                    assistant_text=held.text,
                    verify_quote=_VerifyQuoteThrough(
                        self.toolset, ToolContext(invocation), self.log, invocation.invocation_id
                    ),
                    surface="text",
                    products=product_facts(products),
                )
            except Exception as error:  # noqa: BLE001 - fail safe: a notice, never a draft
                outcome, shown, error_class = "internal-error", UNVERIFIABLE_NOTICE, _class(error)
            else:
                rendered = checked.rendered
                if isinstance(rendered, str):
                    outcome, shown = "answered", rendered
                else:
                    outcome, shown, checked = "internal-error", UNVERIFIABLE_NOTICE, None
        self._record(
            principal=principal,
            digest=digest,
            turn_id=turn_id,
            outcome=outcome,
            error_class=error_class,
            checked=checked,
            tools=self.log.take(invocation.invocation_id),
            duration_ms=max(0, round((time.monotonic() - started) * 1000)),
        )
        return _text(shown)

    def _record(
        self,
        *,
        principal: str,
        digest: str | None,
        turn_id: str,
        outcome: TurnOutcome,
        error_class: str | None,
        checked: CheckedTurn | None,
        tools: tuple[ToolCallRecord, ...],
        duration_ms: int,
    ) -> None:
        """Writes the turn's record; if the full one cannot be built or written, a minimal one.

        Never raises: the answer is already decided, and a record is not a reason to withdraw
        it. If even the minimal record cannot be written there is nothing left to tell, because
        nothing in this package may log anything else.
        """
        service_version = self.config.service_version
        record: TurnAuditRecord
        try:
            if checked is None:
                record = minimal_record(
                    service_version=service_version,
                    principal=principal,
                    principal_digest=digest,
                    turn_id=turn_id,
                    outcome=outcome,
                    tools=tools,
                    error_class=error_class,
                    duration_ms=duration_ms,
                )
            else:
                record = turn_record(
                    service_version=service_version,
                    principal=principal,
                    principal_digest=digest,
                    turn_id=turn_id,
                    answer=checked.answer,
                    tools=tools,
                    sections_dropped=checked.sections_dropped,
                    assistant_flags=checked.assistant_flags,
                    duration_ms=duration_ms,
                    outcome=outcome,
                )
            emit(record)
        except Exception as error:  # noqa: BLE001 - the record of last resort follows
            # Nothing else may log (see the docstring), so a failure here has nowhere to go.
            with contextlib.suppress(Exception):
                emit(
                    minimal_record(
                        service_version=service_version,
                        principal=principal,
                        principal_digest=digest,
                        turn_id=turn_id,
                        outcome=outcome,
                        tools=tools,
                        error_class=_class(error),
                        duration_ms=duration_ms,
                    )
                )


def _class(error: BaseException) -> str:
    """The class name of what went wrong: of the first leaf, for a task group's exceptions."""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return type(error).__name__


def _text(text: str) -> types.Content:
    return types.Content(role="model", parts=[types.Part(text=text)])
