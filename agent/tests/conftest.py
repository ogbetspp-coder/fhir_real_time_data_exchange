"""Shared fixtures. Nothing here reaches a network, a credential, or a model."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService, Session

from verifiable_answer_agent.config import AgentConfig

from .fake_query_service import FakeQueryService, running_query_service

TEST_PRINCIPAL = "urn:reviewer:synthetic-01"
TEST_TOKEN = "synthetic-end-user-token"


@pytest.fixture(autouse=True)
def _no_edge_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test mints Cloud Run's edge token from the runtime's credentials.

    Looking for one on a machine that holds none waits on the GCE metadata server — about 3.4 s
    per MCP request, most of the suite's run time before 2026-09-22 — and on a machine signed in
    to Google Cloud it would be a real network call. The edge header and the minting itself have
    their own tests (``test_tools_edge_auth.py``), which patch what they need explicitly.
    """
    from verifiable_answer_agent import tools

    monkeypatch.setattr(tools, "edge_auth_token", lambda _audience: None)


@pytest.fixture
def query_service() -> Iterator[FakeQueryService]:
    with running_query_service() as service:
        yield service


def invocation_context(
    *, token: str | None = TEST_TOKEN, turn_id: object = None
) -> InvocationContext:
    """A minimal ADK invocation context, with or without the token and a turn id in state.

    ``turn_id`` is put in state as given — a test can plant a value that is not a UUID.
    """
    from verifiable_answer_agent.tools import TURN_ID_STATE_KEY, USER_TOKEN_STATE_KEY

    session = Session(id="synthetic-session", app_name="agent-tests", user_id=TEST_PRINCIPAL)
    if token is not None:
        session.state[USER_TOKEN_STATE_KEY] = token
    if turn_id is not None:
        session.state[TURN_ID_STATE_KEY] = turn_id
    return InvocationContext(
        session_service=InMemorySessionService(),
        invocation_id="synthetic-invocation",
        session=session,
    )


def config_for(url: str) -> AgentConfig:
    return AgentConfig(
        query_service_url=url,
        model="gemini-3.5-flash",
        service_version="test",
        timeout_seconds=20.0,
    )
