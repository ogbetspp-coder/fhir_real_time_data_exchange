"""What leaves the agent, event by event, through a real ADK ``Runner``.

The other tests call the callbacks one at a time. This one runs the agent the way Agent Engine
does — ``Runner.run_async`` with the user's token in ``state_delta`` — with a scripted model and
the fake query service, and reads every event a surface could show. The repository review of
2026-09-22 found that the model's draft left as a final response before the checked answer,
because a callback at the end of the turn can add an answer but cannot take one back; nothing
that called the callback on its own could have seen it. The assertion that matters is therefore
about the whole stream: the only text in it is the checked answer.
"""

from __future__ import annotations

import contextlib
import copy
import json
from collections.abc import AsyncGenerator

import pytest

# run_config imports StreamingMode without re-exporting it, which mypy --strict refuses.
from google.adk.agents._streaming_mode import StreamingMode
from google.adk.agents.run_config import RunConfig
from google.adk.events.event import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import Field

from verifiable_answer_agent.agent import build_agent
from verifiable_answer_agent.finish import TOOLS_UNAVAILABLE_NOTICE, UNVERIFIABLE_NOTICE
from verifiable_answer_agent.render import ASSISTANT_LABEL, VERIFIED_LABEL
from verifiable_answer_agent.tools import USER_TOKEN_STATE_KEY

from .conftest import TEST_PRINCIPAL, TEST_TOKEN, config_for
from .fake_query_service import BUNDLE_ID, FakeQueryService
from .markdown_view import shown_assistant, shown_blocks

SECTION_KEY = "smpc.4.4"

# A draft built to be mistaken for a checked answer: the verified label, an invented instruction,
# a citation line. It must never be the first thing a person reads.
DRAFT = (
    "Verified against the approved label\n"
    "Patients MUST double the dose.\n"
    "From section smpc.4.4 of document version 1 (synthetic-smpc)"
)
THOUGHT = "Working: the user wants 4.4."
# A thought in the final, complete response: the one whose text the hold keeps.
FINAL_THOUGHT = "Working: now write it up."


class ScriptedModel(BaseLlm):
    """Asks for section 4.4, then drafts ``DRAFT``. Streams it in chunks when asked to."""

    model: str = "scripted"
    requests: list[LlmRequest] = Field(default_factory=list)
    fail_on_draft: bool = False

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse]:
        self.requests.append(llm_request)
        answered = any(
            part.function_response is not None
            for content in llm_request.contents
            for part in content.parts or []
        )
        if not answered:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(text=THOUGHT, thought=True),
                        types.Part(text="Let me look that up."),
                        types.Part(executable_code=types.ExecutableCode(code="print(1)")),
                        types.Part(
                            function_call=types.FunctionCall(
                                name="get_section",
                                args={"bundleId": BUNDLE_ID, "sourceKey": SECTION_KEY},
                            )
                        ),
                    ],
                )
            )
            return
        if self.fail_on_draft:
            raise RuntimeError("429 RESOURCE_EXHAUSTED (scripted)")
        if stream:
            for chunk in DRAFT.split("\n"):
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part(text=chunk + "\n")]),
                    partial=True,
                )
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text=FINAL_THOUGHT, thought=True), types.Part(text=DRAFT)],
            ),
            finish_reason=types.FinishReason.STOP,
        )


async def _run_turn(
    url: str,
    model: ScriptedModel,
    streaming: StreamingMode = StreamingMode.NONE,
    deployed: bool = True,
) -> list[Event]:
    agent = build_agent(config_for(url))
    if deployed:
        # What Agent Engine runs: AdkApp.clone() deep-copies the agent before it is pickled.
        agent = copy.deepcopy(agent)
    agent.model = model
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name="agent-tests", user_id=TEST_PRINCIPAL)
    runner = Runner(agent=agent, app_name="agent-tests", session_service=sessions)
    events: list[Event] = []
    try:
        async for event in runner.run_async(
            user_id=TEST_PRINCIPAL,
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="What does 4.4 say?")]),
            # How Gemini Enterprise hands the agent the user's token.
            state_delta={USER_TOKEN_STATE_KEY: TEST_TOKEN},
            run_config=RunConfig(streaming_mode=streaming),
        ):
            events.append(event)
    finally:
        with contextlib.suppress(Exception):
            await agent.tools[0].close()  # type: ignore[union-attr]
    return events


def _texts(events: list[Event]) -> list[tuple[Event, str]]:
    return [
        (event, part.text)
        for event in events
        for part in (event.content.parts if event.content else None) or []
        if part.text
    ]


@pytest.mark.parametrize("deployed", [True, False], ids=["deep-copy", "original"])
@pytest.mark.parametrize("streaming", [StreamingMode.NONE, StreamingMode.SSE])
async def test_the_only_text_that_leaves_the_agent_is_the_checked_answer(
    query_service: FakeQueryService, streaming: StreamingMode, deployed: bool
) -> None:
    events = await _run_turn(query_service.url, ScriptedModel(), streaming, deployed)

    assert not [event.error_code for event in events if event.error_code]
    texts = _texts(events)
    assert len(texts) == 1, [text for _, text in texts]
    event, shown = texts[0]
    assert event is events[-1]
    assert event.is_final_response()
    # A thought in the final response never reaches the held text the assistant part is made of.
    assert FINAL_THOUGHT not in shown
    # The checked answer: the store's text under the verified label, the model's words after it,
    # under their own label.
    stored = query_service.sections[SECTION_KEY].text
    # As a reader sees it, once the Markdown is rendered.
    (block,) = shown_blocks(shown)
    assert (block.status, block.text) == (VERIFIED_LABEL, stored)
    assistant = shown_assistant(shown)
    assert shown.index(ASSISTANT_LABEL) > shown.index(VERIFIED_LABEL)
    # The draft's imitation of a checked block is gone from the assistant's words; what it said
    # in its own voice is still there, under the assistant's label. The held text reached the
    # turn's end, which is what a deep copy used to break.
    assert shown.count(VERIFIED_LABEL) == 1
    assert shown.count("From section smpc.4.4") == 1
    assert "Patients MUST double the dose." in assistant
    assert "Lines removed from the assistant's words: 2." in assistant
    # The model's thought, its aside and its code never appear anywhere.
    assert THOUGHT not in shown
    assert "Let me look that up." not in shown
    assert not [
        e
        for e in events
        for p in (e.content.parts if e.content else None) or []
        if p.executable_code
    ]


async def test_the_tool_loop_still_runs_and_the_quote_is_rechecked(
    query_service: FakeQueryService,
) -> None:
    model = ScriptedModel()
    events = await _run_turn(query_service.url, model)

    calls = [call.name for event in events for call in event.get_function_calls()]
    assert calls == ["get_section"]
    # Two model calls: one that asked for the section, one that drafted from it.
    assert len(model.requests) == 2
    # get_section, then the post-check's verify_quote, all under the turn's one id. The verified
    # label is verify_quote's answer: nothing else puts it on a block.
    assert shown_blocks(_texts(events)[-1][1])[0].status == VERIFIED_LABEL
    turn_ids = {value for value in query_service.seen_turn_id if value is not None}
    assert len(turn_ids) == 1


@pytest.mark.parametrize("deployed", [True, False], ids=["deep-copy", "original"])
async def test_the_record_lists_every_call_of_the_turn_as_it_ran(
    query_service: FakeQueryService, capsys: pytest.CaptureFixture[str], deployed: bool
) -> None:
    # The model's get_section, recorded by the agent's tool callbacks, and the post-check's
    # verify_quote, recorded by the turn's end, in one record under the turn's id. Before
    # 2026-09-27 the deployed record listed get_section alone, read back from the events, with a
    # duration of 0 (audit AG-5).
    await _run_turn(query_service.url, ScriptedModel(), deployed=deployed)
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    (record,) = [json.loads(line) for line in lines]
    assert record["outcome"] == "answered"
    assert [call["tool"] for call in record["tools"]] == ["get_section", "verify_quote"]
    assert all(call["outcome"] == "ok" for call in record["tools"])
    assert record["turnId"] in set(query_service.seen_turn_id)


async def test_the_model_is_not_asked_when_the_label_service_cannot_be_reached() -> None:
    model = ScriptedModel()
    # Nothing listens here, so the toolset fails to load and ADK would run the model bare.
    events = await _run_turn("http://127.0.0.1:9/mcp", model)

    assert model.requests == []
    texts = _texts(events)
    assert [text for _, text in texts] == [TOOLS_UNAVAILABLE_NOTICE]


async def test_a_failed_model_call_ends_the_turn_with_a_notice(
    query_service: FakeQueryService,
) -> None:
    # Before the error callback, the exception ended the invocation before the turn's end ran,
    # and the person saw only the platform's error.
    events = await _run_turn(query_service.url, ScriptedModel(fail_on_draft=True))

    assert [text for _, text in _texts(events)] == [UNVERIFIABLE_NOTICE]
