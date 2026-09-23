"""The turn's answer is checked before anyone sees it, against a real MCP server.

The first live Gemini Enterprise turn (2026-09-22) showed the model's own text: the query
service's audit record has find_product and get_section and no verify_quote. These tests run the
agent's after_agent_callback against the fake service and assert what a reader depends on — the
quotation shown is the store's, it was re-checked, and when it cannot be checked nothing is
shown as label content.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from verifiable_answer_agent.finish import (
    TOOLS_UNAVAILABLE_NOTICE,
    UNVERIFIABLE_NOTICE,
    build_finish_turn,
    draft_from_events,
)
from verifiable_answer_agent.hold import DraftHold
from verifiable_answer_agent.tools import build_query_toolset

from .conftest import TEST_PRINCIPAL, config_for, invocation_context
from .fake_query_service import BUNDLE_ID, VERSION_ID, FakeQueryService

SECTION_KEY = "smpc.4.4"
TURN_ID = "0f6d1a2e-3b4c-4d5e-8f60-718293a4b5c6"


@dataclass
class _Response:
    name: str
    response: dict[str, Any]


@dataclass
class _Part:
    function_response: _Response | None = None
    text: str | None = None


@dataclass
class _Content:
    parts: list[_Part]


@dataclass
class _Event:
    invocation_id: str
    content: _Content
    author: str = "agent"


def _section_payload(service: FakeQueryService) -> dict[str, Any]:
    return dict(service.sections[SECTION_KEY].payload)


def _as_event_response(payload: dict[str, Any]) -> dict[str, Any]:
    """What ADK puts in a function_response for an MCP tool: the serialised CallToolResult."""
    return {"content": [], "structuredContent": payload, "isError": False}


def _events(service: FakeQueryService, invocation_id: str) -> list[_Event]:
    return [
        _Event(
            invocation_id=invocation_id,
            content=_Content(
                parts=[
                    _Part(
                        function_response=_Response(
                            "get_section", _as_event_response(_section_payload(service))
                        )
                    )
                ]
            ),
        )
    ]


def _held(context: InvocationContext, model_text: str) -> DraftHold:
    """A hold that took the model's reply the way the agent's after_model_callback does."""
    hold = DraftHold()
    response = LlmResponse(content=types.Content(role="model", parts=[types.Part(text=model_text)]))
    hold.after_model(CallbackContext(context), response)
    return hold


async def _run(service: FakeQueryService, events: list[_Event], model_text: str) -> str:
    context = invocation_context(turn_id=TURN_ID)
    context.session.events = events  # type: ignore[assignment]
    toolset = build_query_toolset(config_for(service.url))
    finish = build_finish_turn(config_for(service.url), toolset, _held(context, model_text))
    try:
        content = await finish(CallbackContext(context))
    finally:
        with contextlib.suppress(Exception):
            await toolset.close()
    assert isinstance(content, types.Content)
    part = (content.parts or [])[0]
    return part.text or ""


async def test_the_answer_shown_is_the_stores_text_with_its_citation(
    query_service: FakeQueryService, capsys: Any
) -> None:
    payload = _section_payload(query_service)
    shown = await _run(
        query_service, _events(query_service, "synthetic-invocation"), "Here is what it says."
    )

    # The store's own words, and the four fields a reader needs to recompute the hash.
    assert payload["text"].split("\n")[0][:40] in shown
    assert BUNDLE_ID in shown
    assert VERSION_ID in shown
    assert SECTION_KEY in shown
    assert payload["narrativeDivSha256"] in shown
    # The model's words are still shown, labelled as the model's, never as label content.
    assert "Here is what it says." in shown

    # verify_quote actually ran: the fake saw the turn id on every request of this turn.
    assert TURN_ID in query_service.seen_turn_id

    # One audit record, naming the principal and the turn, with no narrative in it.
    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert record["turnId"] == TURN_ID
    assert record["principal"] == TEST_PRINCIPAL


async def test_a_quotation_the_store_does_not_confirm_is_flagged_where_it_is_read(
    query_service: FakeQueryService,
) -> None:
    # The store returns a section whose text is not what it will confirm: verify_quote answers
    # no-match, and the reader is told so on the block itself.
    query_service.corrupt_section = SECTION_KEY
    shown = await _run(
        query_service, _events(query_service, "synthetic-invocation"), "Here is what it says."
    )
    assert "no-match" in shown or "not verified" in shown.lower()


async def test_nothing_is_shown_as_label_content_when_the_check_cannot_run(
    query_service: FakeQueryService,
) -> None:
    # The service is gone before the post-check can ask it anything.
    service_url = query_service.url
    toolset = build_query_toolset(config_for(service_url.replace("http://", "http://127.0.0.2:1/")))
    context = invocation_context(turn_id=TURN_ID)
    finish = build_finish_turn(
        config_for(service_url), toolset, _held(context, "Here is what it says.")
    )
    context.session.events = _events(  # type: ignore[assignment]
        query_service, "synthetic-invocation"
    )
    content = await finish(CallbackContext(context))
    assert isinstance(content, types.Content)
    assert ((content.parts or [])[0].text or "") == UNVERIFIABLE_NOTICE


async def test_a_turn_id_that_is_not_a_uuid_shows_no_draft(query_service: FakeQueryService) -> None:
    # current_turn_id raises on this; before 2026-09-22 it raised outside the guard and the
    # model's draft was left as the only answer.
    context = invocation_context(turn_id="not-a-uuid")
    context.session.events = _events(query_service, "synthetic-invocation")  # type: ignore[assignment]
    toolset = build_query_toolset(config_for(query_service.url))
    finish = build_finish_turn(
        config_for(query_service.url), toolset, _held(context, "Here is what it says.")
    )
    try:
        content = await finish(CallbackContext(context))
    finally:
        with contextlib.suppress(Exception):
            await toolset.close()
    assert isinstance(content, types.Content)
    assert ((content.parts or [])[0].text or "") == UNVERIFIABLE_NOTICE


async def test_a_turn_the_model_was_refused_says_the_service_was_unreachable(
    query_service: FakeQueryService,
) -> None:
    context = invocation_context(turn_id=TURN_ID)
    hold = DraftHold()
    # A request with no tools in it: what ADK sends when the query toolset failed to load.
    assert hold.before_model(CallbackContext(context), LlmRequest()) is not None
    toolset = build_query_toolset(config_for(query_service.url))
    content = await build_finish_turn(config_for(query_service.url), toolset, hold)(
        CallbackContext(context)
    )
    assert isinstance(content, types.Content)
    assert ((content.parts or [])[0].text or "") == TOOLS_UNAVAILABLE_NOTICE


def test_only_this_turns_results_are_answered_from(query_service: FakeQueryService) -> None:
    events = _events(query_service, "another-invocation")
    sections, calls = draft_from_events(events, "synthetic-invocation")
    assert sections == []
    assert calls == []
