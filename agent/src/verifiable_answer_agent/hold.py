"""The model's words never leave the agent on their own: they are held for the post-check.

ADK runs ``after_agent_callback`` after the agent has already yielded every event the model
produced, and appends what the callback returns as one more event
(``google/adk/agents/base_agent.py``, ``run_async``). A callback can add an answer; it cannot
take back one already sent. Until this module existed, the model's draft reached Gemini
Enterprise as a final response, and the checked answer followed it — so a person could read the
unchecked text first, and with streaming, word by word. The repository review of 2026-09-22
reproduced it with a real ``Runner``.

So the draft is stopped where it is produced. ``after_model`` runs on every model response,
partial or final, before ADK turns it into an event: it keeps function calls — with their
thought signatures, so the tool loop runs exactly as before — and nothing else. Text, thoughts,
and any other kind of part a model could add later are not forwarded; that is an allowlist, not
a list of what to remove. The text of the last complete response is held here, keyed by the
invocation, and ``finish.build_finish_turn`` takes it as the assistant's words of the checked
answer. The only text event of a turn is then the rendered, checked one.

``before_model`` closes the other way a draft could be unchecked. When the query toolset fails
to load (no user token, the service down, a 401 while listing tools), ADK logs the failure and
runs the model with no tools at all (``google/adk/agents/llm_agent.py``), and the model answers
from its own memory. Here the model is not called unless all four query tools are in the
request; the turn is marked, and the person is told the label service could not be reached.

``on_model_error`` covers the third: a model call that raises (a quota refusal, an outage) would
otherwise end the invocation before the turn's end runs, and the person would see nothing but a
platform error. The failure is marked, the flow ends cleanly, and the person is told.

The hold is process memory, not session state: nothing in it is persisted, and a turn's entry is
removed when the turn is answered. The model's text is never logged.

Agent Engine deploys a deep copy of the agent (``AdkApp.clone``). The callbacks are therefore
bound methods of this object and of ``finish.TurnFinisher``, never closures: a deep copy copies
a bound method's object — once, shared through the copy's memo — while it keeps a closure
pointing at the original. With closures, the deployed callbacks wrote to one hold and the turn's
end read another (review of 2026-09-22; ``tests/test_turn_events.py`` runs a deep copy).
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Final, final

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from .tools import QUERY_TOOL_NAMES

__all__ = ["DraftHold", "HeldTurn"]

# Turns whose after_agent_callback never ran (an invocation cancelled mid-flight) would otherwise
# stay here for the life of the process. Far more than one runtime instance serves at once.
_CAPACITY: Final = 256


@final
@dataclass(frozen=True, slots=True)
class HeldTurn:
    """What the model produced in one turn that a person must not see unchecked."""

    text: str
    tools_missing: bool
    model_failed: bool


@final
class DraftHold:
    """The agent's ``before_model_callback`` and ``after_model_callback``, and what they held."""

    def __init__(self, capacity: int = _CAPACITY) -> None:
        self._capacity = capacity
        self._texts: OrderedDict[str, str] = OrderedDict()
        self._tools_missing: OrderedDict[str, None] = OrderedDict()
        self._model_failed: OrderedDict[str, None] = OrderedDict()

    def before_model(
        self, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        """Refuse to call the model without the query tools. Returns a response with no text."""
        if all(name in llm_request.tools_dict for name in QUERY_TOOL_NAMES):
            return None
        self._remember(self._tools_missing, callback_context.invocation_id, None)
        # No content and no finish reason: ADK yields no event for it and ends the model loop,
        # and the turn's after_agent_callback says why nothing was answered.
        return LlmResponse()

    def after_model(
        self, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> LlmResponse | None:
        """Take every text part out of the response; hold the text of a complete one."""
        content = llm_response.content
        parts = list(content.parts or []) if content is not None else []
        kept = [part for part in parts if part.function_call is not None]
        if len(kept) == len(parts):
            return None
        if not llm_response.partial:
            # Thoughts are the model's working, not its answer, and are not shown either way.
            text = "".join(
                part.text or "" for part in parts if part.function_call is None and not part.thought
            )
            if text:
                self._remember(self._texts, callback_context.invocation_id, text)
        if kept:
            role = content.role if content is not None else "model"
            return llm_response.model_copy(update={"content": types.Content(role=role, parts=kept)})
        # A response that was only text becomes one with nothing in it. The finish reason goes
        # too: ADK treats a complete STOP response with no parts as an error to surface.
        return llm_response.model_copy(update={"content": None, "finish_reason": None})

    def on_model_error(
        self, callback_context: CallbackContext, llm_request: LlmRequest, error: Exception
    ) -> LlmResponse | None:
        """A failed model call ends the model loop quietly; the turn's end says it failed."""
        del llm_request, error  # The error's text can carry request content; it is not kept.
        self._remember(self._model_failed, callback_context.invocation_id, None)
        return LlmResponse()

    def take(self, invocation_id: str) -> HeldTurn:
        """This turn's held text and what went wrong, if anything, removed from the hold."""
        text = self._texts.pop(invocation_id, "")
        missing = invocation_id in self._tools_missing
        failed = invocation_id in self._model_failed
        self._tools_missing.pop(invocation_id, None)
        self._model_failed.pop(invocation_id, None)
        return HeldTurn(text=text, tools_missing=missing, model_failed=failed)

    def _remember[V](self, store: OrderedDict[str, V], key: str, value: V) -> None:
        store[key] = value
        store.move_to_end(key)
        while len(store) > self._capacity:
            store.popitem(last=False)
