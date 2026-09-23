"""The hold forgets turns that never ended, oldest first, and only past its capacity.

A turn whose ``after_agent_callback`` never ran (an invocation cancelled mid-flight) leaves its
entry behind; the hold is bounded so that such entries cannot grow for the life of the process.
``tests/test_turn_events.py`` covers what the hold keeps back from a person; this covers what it
lets go of.
"""

from __future__ import annotations

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService, Session
from google.genai import types

from verifiable_answer_agent.hold import DraftHold, HeldTurn

from .conftest import TEST_PRINCIPAL


def _context(invocation_id: str) -> CallbackContext:
    session = Session(id="synthetic-session", app_name="agent-tests", user_id=TEST_PRINCIPAL)
    return CallbackContext(
        InvocationContext(
            session_service=InMemorySessionService(), invocation_id=invocation_id, session=session
        )
    )


def _reply(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


def test_past_its_capacity_the_hold_forgets_the_oldest_turn() -> None:
    hold = DraftHold(capacity=2)
    for turn in ("turn-1", "turn-2", "turn-3"):
        context = _context(turn)
        hold.after_model(context, _reply(f"draft of {turn}"))
        # Each failure mark is bounded the same way as the text.
        hold.before_model(context, LlmRequest())
        hold.on_model_error(context, LlmRequest(), RuntimeError("scripted"))

    # The oldest turn is gone from every store: nothing of it is held, nothing marked.
    assert hold.take("turn-1") == HeldTurn(text="", tools_missing=False, model_failed=False)
    assert hold.take("turn-2") == HeldTurn(
        text="draft of turn-2", tools_missing=True, model_failed=True
    )
    assert hold.take("turn-3") == HeldTurn(
        text="draft of turn-3", tools_missing=True, model_failed=True
    )


def test_a_turn_touched_again_is_the_newest_not_the_first_evicted() -> None:
    hold = DraftHold(capacity=2)
    hold.after_model(_context("turn-1"), _reply("first draft"))
    hold.after_model(_context("turn-2"), _reply("draft of turn-2"))
    hold.after_model(_context("turn-1"), _reply("second draft"))
    hold.after_model(_context("turn-3"), _reply("draft of turn-3"))

    assert hold.take("turn-2").text == ""
    assert hold.take("turn-1").text == "second draft"
    assert hold.take("turn-3").text == "draft of turn-3"


def test_taking_a_turn_removes_it() -> None:
    hold = DraftHold(capacity=2)
    hold.after_model(_context("turn-1"), _reply("draft"))
    assert hold.take("turn-1").text == "draft"
    assert hold.take("turn-1").text == ""
