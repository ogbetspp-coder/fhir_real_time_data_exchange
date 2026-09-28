"""The turn's answer is checked before anyone sees it, against a real MCP server.

The first live Gemini Enterprise turn (2026-09-22) showed the model's own text: the query
service's audit record has find_product and get_section and no verify_quote. These tests run the
agent's after_agent_callback against the fake service and assert what a reader depends on — the
quotation shown is the store's, it was re-checked, and when it cannot be checked nothing is
shown as label content.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from dataclasses import dataclass, replace
from typing import Any, cast

import pytest
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from verifiable_answer_agent import finish
from verifiable_answer_agent.audit import (
    NIL_TURN_ID,
    WITHHELD_PRINCIPAL,
    ToolCallRecord,
    principal_fields,
)
from verifiable_answer_agent.config import AgentConfig
from verifiable_answer_agent.contract import (
    VERIFY_QUOTE_MAX_UTF16,
    EcmaDraft202012Validator,
    load_agent_turn_schema,
    utf16_length,
)
from verifiable_answer_agent.finish import (
    TOOLS_UNAVAILABLE_NOTICE,
    UNVERIFIABLE_NOTICE,
    build_finish_turn,
    draft_from_events,
)
from verifiable_answer_agent.hold import DraftHold
from verifiable_answer_agent.postcheck import MAX_CONCURRENT_CHECKS
from verifiable_answer_agent.render import VERIFIED_LABEL
from verifiable_answer_agent.tools import ToolCallLog, build_query_toolset

from .conftest import TEST_PRINCIPAL, config_for, invocation_context
from .fake_query_service import (
    BUNDLE_ID,
    LONG_SECTION_KEY,
    LONG_SECTION_OLD_CUTS,
    VERSION_ID,
    FakeQueryService,
    long_section,
)
from .markdown_view import shown_blocks

SECTION_KEY = "smpc.4.4"
TURN_ID = "0f6d1a2e-3b4c-4d5e-8f60-718293a4b5c6"
KEY = b"synthetic-deployment-key-32-bytes"


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


def _events(
    service: FakeQueryService, invocation_id: str, payload: dict[str, Any] | None = None
) -> list[_Event]:
    section = payload if payload is not None else _section_payload(service)
    return [
        _Event(
            invocation_id=invocation_id,
            content=_Content(
                parts=[
                    _Part(function_response=_Response("get_section", _as_event_response(section)))
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


async def _run(
    service: FakeQueryService,
    events: list[_Event],
    model_text: str,
    *,
    user_id: str = TEST_PRINCIPAL,
) -> str:
    context = invocation_context(turn_id=TURN_ID, user_id=user_id)
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


def _records(capsys: Any) -> list[dict[str, Any]]:
    """Every audit line the turn wrote to standard output, parsed."""
    lines = capsys.readouterr().out.strip().splitlines()
    return [json.loads(line) for line in lines if line.startswith("{")]


def _validates(record: dict[str, Any]) -> bool:
    return not list(EcmaDraft202012Validator(load_agent_turn_schema()).iter_errors(record))


async def _finish(
    config: AgentConfig, context: InvocationContext, hold: DraftHold, log: ToolCallLog | None = None
) -> str:
    toolset = build_query_toolset(config)
    try:
        content = await build_finish_turn(config, toolset, hold, log)(CallbackContext(context))
    finally:
        with contextlib.suppress(Exception):
            await toolset.close()
    assert isinstance(content, types.Content)
    return (content.parts or [])[0].text or ""


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
    (record,) = _records(capsys)
    assert record["turnId"] == TURN_ID
    assert record["principal"] == TEST_PRINCIPAL
    assert record["outcome"] == "answered"
    assert _validates(record)


async def test_an_e_mail_user_id_still_gets_its_checked_answer_and_a_record(
    query_service: FakeQueryService, capsys: Any
) -> None:
    # Gemini Enterprise supplies the user's e-mail address as the session user id. The contract's
    # principal refuses one, and until 2026-09-27 the refusal was raised inside the same guard as
    # the post-check: verify_quote matched, the reader was told "could not be verified", and no
    # audit line was written. Every live turn would have ended that way (audit AG-1).
    shown = await _run(
        query_service,
        _events(query_service, "synthetic-invocation"),
        "Here is what it says.",
        user_id="alice@example.com",
    )
    assert [block.status for block in shown_blocks(shown)] == [VERIFIED_LABEL]
    assert shown != UNVERIFIABLE_NOTICE
    (record,) = _records(capsys)
    assert _validates(record)
    assert record["outcome"] == "answered"
    assert record["principal"] == WITHHELD_PRINCIPAL
    assert "principalDigest" not in record  # no key configured
    assert "alice" not in json.dumps(record)
    assert record["spansVerified"] == 1


async def test_with_a_key_the_e_mail_user_is_recorded_as_a_keyed_digest(
    query_service: FakeQueryService, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Read from the runtime's environment when the turn ends, as Agent Engine supplies a secret.
    monkeypatch.setenv("AGENT_PRINCIPAL_DIGEST_KEY", KEY.decode())
    context = invocation_context(turn_id=TURN_ID, user_id="alice@example.com")
    context.session.events = _events(query_service, "synthetic-invocation")  # type: ignore[assignment]
    await _finish(config_for(query_service.url), context, _held(context, "Here is what it says."))
    (record,) = _records(capsys)
    assert _validates(record)
    assert record["principal"] == WITHHELD_PRINCIPAL
    assert record["principalDigest"] == principal_fields("alice@example.com", KEY)[1]


async def test_a_record_that_cannot_be_built_never_hides_the_checked_answer(
    query_service: FakeQueryService, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The record is built apart from the answer: its failure costs the full record, never the
    # answer, and leaves a minimal record naming what went wrong.
    def refuse(**_: Any) -> Any:
        raise ValueError("the full record could not be built")

    monkeypatch.setattr(finish, "turn_record", refuse)
    shown = await _run(
        query_service, _events(query_service, "synthetic-invocation"), "Here is what it says."
    )
    assert [block.status for block in shown_blocks(shown)] == [VERIFIED_LABEL]
    (record,) = _records(capsys)
    assert _validates(record)
    assert record["outcome"] == "answered"
    assert record["errorClass"] == "ValueError"
    # The fallback still carries what the reader was shown as verified (review of PR #129).
    assert record["spansVerified"] == 1


async def test_every_verify_quote_call_is_in_the_record_with_its_duration(
    query_service: FakeQueryService, capsys: Any
) -> None:
    # Before 2026-09-27 the deployed record listed get_section alone: the post-check's calls
    # went over the wire and were never recorded, and every duration was 0 (audit AG-5).
    section = long_section()
    query_service.sections[LONG_SECTION_KEY] = section
    await _run(
        query_service,
        _events(query_service, "synthetic-invocation", section.payload),
        "Here is what it says.",
    )
    (record,) = _records(capsys)
    verify_calls = [call for call in record["tools"] if call["tool"] == "verify_quote"]
    assert len(verify_calls) == len(query_service.seen_quotes) >= 3
    assert all(call["outcome"] == "ok" and call["resultCount"] == 1 for call in verify_calls)
    assert any(call["durationMs"] > 0 for call in verify_calls)
    # tools/list does not grow with the chunks (audit AG-10): the MCP client lists once for
    # itself per call in flight at most, and the post-check looks the tool up once.
    assert query_service.seen_methods.count("tools/list") <= 1 + MAX_CONCURRENT_CHECKS


async def test_concurrent_calls_still_look_verify_quote_up_once() -> None:
    # The post-check runs four calls at once; without a lock each call that arrived while the
    # first lookup was in flight looked the tool up again (review of PR #129, L6).
    lookups = 0

    class _Tool:
        name = "verify_quote"

        async def run_async(self, *, args: dict[str, Any], tool_context: Any) -> dict[str, Any]:
            del args, tool_context
            await asyncio.sleep(0.001)
            return {"isError": True, "content": []}

    class _Toolset:
        async def get_tools(self, _context: Any) -> list[Any]:
            nonlocal lookups
            lookups += 1
            await asyncio.sleep(0.02)  # a tools/list round trip
            return [_Tool()]

    through = finish._VerifyQuoteThrough(
        cast(Any, _Toolset()), cast(Any, None), ToolCallLog(), "synthetic-invocation"
    )
    async with asyncio.TaskGroup() as group:
        for index in range(8):
            group.create_task(through("synthetic-smpc", "1", "smpc.4.4", f"chunk {index}"))
    assert lookups == 1


async def test_the_post_check_looks_verify_quote_up_once_per_turn() -> None:
    # Before 2026-09-27 every chunk asked the toolset for its tools first, and each ask was a
    # tools/list round trip: 18 requests for 8 verify_quote calls (audit AG-10).
    lookups = 0

    class _Tool:
        name = "verify_quote"

        async def run_async(self, *, args: dict[str, Any], tool_context: Any) -> dict[str, Any]:
            del tool_context
            return {"isError": True, "content": [], "quote": args["quote"]}

    class _Toolset:
        async def get_tools(self, _context: Any) -> list[Any]:
            nonlocal lookups
            lookups += 1
            return [_Tool()]

    log = ToolCallLog()
    through = finish._VerifyQuoteThrough(
        cast(Any, _Toolset()), cast(Any, None), log, "synthetic-invocation"
    )
    for index in range(8):
        result = await through("synthetic-smpc", "1", "smpc.4.4", f"chunk {index}")
        assert result.reason == "tool-error"
    assert lookups == 1
    assert [call.outcome for call in log.take("synthetic-invocation")] == ["tool-error"] * 8


async def test_a_verify_quote_call_that_raises_is_recorded_before_it_ends_the_turn() -> None:
    class _Tool:
        name = "verify_quote"

        async def run_async(self, *, args: dict[str, Any], tool_context: Any) -> dict[str, Any]:
            del args, tool_context
            raise ConnectionError("the service went away")

    class _Toolset:
        async def get_tools(self, _context: Any) -> list[Any]:
            return [_Tool()]

    class _Empty:
        async def get_tools(self, _context: Any) -> list[Any]:
            return []

    log = ToolCallLog()
    through = finish._VerifyQuoteThrough(
        cast(Any, _Toolset()), cast(Any, None), log, "synthetic-invocation"
    )
    with pytest.raises(ConnectionError):
        await through("synthetic-smpc", "1", "smpc.4.4", "a chunk")
    missing = finish._VerifyQuoteThrough(
        cast(Any, _Empty()), cast(Any, None), log, "synthetic-invocation"
    )
    assert (await missing("synthetic-smpc", "1", "smpc.4.4", "a chunk")).reason == (
        "transport-error"
    )
    assert [call.outcome for call in log.take("synthetic-invocation")] == [
        "transport-error",
        "transport-error",
    ]


async def test_a_quotation_the_store_does_not_confirm_is_flagged_where_it_is_read(
    query_service: FakeQueryService, capsys: Any
) -> None:
    # The store returns a section whose text is not what it will confirm: verify_quote answers
    # no-match, and the reader is told so on the block itself.
    query_service.corrupt_section = SECTION_KEY
    shown = await _run(
        query_service, _events(query_service, "synthetic-invocation"), "Here is what it says."
    )
    assert "no-match" in shown or "not verified" in shown.lower()
    (record,) = _records(capsys)
    assert record["flags"] == ["no-match"]
    assert [call["resultCount"] for call in record["tools"]] == [0]


async def test_nothing_is_shown_as_label_content_when_the_check_cannot_run(
    query_service: FakeQueryService, capsys: Any
) -> None:
    # The service is gone before the post-check can ask it anything: nothing listens on the port,
    # so the connection is refused at once. (An unrouted address such as 127.0.0.2 is not
    # refused on macOS; it waits for the timeout.)
    unreachable = replace(config_for(_closed_port_url()), timeout_seconds=2.0)
    context = invocation_context(turn_id=TURN_ID)
    context.session.events = _events(  # type: ignore[assignment]
        query_service, "synthetic-invocation"
    )
    shown = await _finish(unreachable, context, _held(context, "Here is what it says."))
    assert shown == UNVERIFIABLE_NOTICE
    # The failure leaves a record, and it says which way the turn ended (audit AG-6).
    (record,) = _records(capsys)
    assert _validates(record)
    assert record["outcome"] == "internal-error"
    assert record["errorClass"]
    assert record["spansVerified"] == 0


def _closed_port_url() -> str:
    """A loopback URL on a port that was free a moment ago and has nothing listening on it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    return f"http://127.0.0.1:{port}/mcp"


async def test_a_verbatim_block_over_the_quote_bound_is_verified_end_to_end(
    query_service: FakeQueryService,
) -> None:
    # The section is longer than two verify_quote windows, and a cut at the last space of each
    # window would fall inside "1 000 000" and just after "≥": before 2026-09-22 the splitter
    # made exactly those cuts, the service refused both pieces, and the label's own text was
    # shown as not verified.
    section = long_section()
    text = section.text
    assert utf16_length(text) > 2 * VERIFY_QUOTE_MAX_UTF16
    first_cut = text.rfind(" ", 0, VERIFY_QUOTE_MAX_UTF16)
    second_cut = first_cut + 1 + text[first_cut + 1 :].rfind(" ", 0, VERIFY_QUOTE_MAX_UTF16)
    assert (first_cut, second_cut) == LONG_SECTION_OLD_CUTS
    query_service.sections[LONG_SECTION_KEY] = section

    shown = await _run(
        query_service,
        _events(query_service, "synthetic-invocation", section.payload),
        "Here is what it says.",
    )

    # The chunks are checked a few at a time, so they reach the service in any order.
    quotes = sorted((quote for quote, _ in query_service.seen_quotes), key=text.index)
    assert len(quotes) >= 3
    assert [result for _, result in query_service.seen_quotes] == ["match"] * len(quotes)
    # Every chunk was within the bound, and together they are the whole block.
    assert all(utf16_length(quote) <= VERIFY_QUOTE_MAX_UTF16 for quote in quotes)
    assert " ".join(quotes) == text
    (block,) = shown_blocks(shown)
    assert (block.status, block.text) == (VERIFIED_LABEL, text)
    assert "not verified" not in shown.lower()
    assert "no-match" not in shown


async def test_a_turn_id_that_is_not_a_uuid_shows_no_draft(
    query_service: FakeQueryService, capsys: Any
) -> None:
    # current_turn_id raises on this; before 2026-09-22 it raised outside the guard and the
    # model's draft was left as the only answer.
    context = invocation_context(turn_id="not-a-uuid")
    context.session.events = _events(query_service, "synthetic-invocation")  # type: ignore[assignment]
    shown = await _finish(
        config_for(query_service.url), context, _held(context, "Here is what it says.")
    )
    assert shown == UNVERIFIABLE_NOTICE
    (record,) = _records(capsys)
    assert _validates(record)
    assert record["outcome"] == "turn-id-missing"
    assert record["turnId"] == NIL_TURN_ID
    assert record["errorClass"] == "InvalidTurnIdError"


async def test_a_turn_the_model_was_refused_says_the_service_was_unreachable(
    query_service: FakeQueryService, capsys: Any
) -> None:
    context = invocation_context(turn_id=TURN_ID)
    hold = DraftHold()
    # A request with no tools in it: what ADK sends when the query toolset failed to load.
    assert hold.before_model(CallbackContext(context), LlmRequest()) is not None
    shown = await _finish(config_for(query_service.url), context, hold)
    assert shown == TOOLS_UNAVAILABLE_NOTICE
    (record,) = _records(capsys)
    assert _validates(record)
    assert (record["outcome"], record["turnId"]) == ("tools-unavailable", TURN_ID)


async def test_a_failed_model_call_leaves_a_record_saying_so(
    query_service: FakeQueryService, capsys: Any
) -> None:
    context = invocation_context(turn_id=TURN_ID)
    hold = DraftHold()
    hold.on_model_error(CallbackContext(context), LlmRequest(), RuntimeError("quota"))
    shown = await _finish(config_for(query_service.url), context, hold)
    assert shown == UNVERIFIABLE_NOTICE
    (record,) = _records(capsys)
    assert _validates(record)
    assert record["outcome"] == "model-failed"


async def test_the_models_own_tool_calls_are_recorded_before_the_post_checks(
    query_service: FakeQueryService, capsys: Any
) -> None:
    # What the agent's tool callbacks put in the log reaches the record, in order, ahead of the
    # post-check's own calls.
    context = invocation_context(turn_id=TURN_ID)
    context.session.events = _events(query_service, "synthetic-invocation")  # type: ignore[assignment]
    log = ToolCallLog()
    log.add(
        "synthetic-invocation",
        ToolCallRecord(tool="find_product", outcome="ok", duration_ms=5, result_count=1),
    )
    log.add(
        "synthetic-invocation",
        ToolCallRecord(tool="get_section", outcome="ok", duration_ms=7, result_count=1),
    )
    await _finish(config_for(query_service.url), context, _held(context, "Here."), log)
    (record,) = _records(capsys)
    assert [call["tool"] for call in record["tools"]] == [
        "find_product",
        "get_section",
        "verify_quote",
    ]
    assert log.take("synthetic-invocation") == ()


def test_only_this_turns_results_are_answered_from(query_service: FakeQueryService) -> None:
    events = _events(query_service, "another-invocation")
    sections, products = draft_from_events(events, "synthetic-invocation")
    assert sections == []
    assert products == []
