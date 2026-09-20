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


@pytest.fixture
def query_service() -> Iterator[FakeQueryService]:
    with running_query_service() as service:
        yield service


def invocation_context(*, token: str | None = TEST_TOKEN) -> InvocationContext:
    """A minimal ADK invocation context, with or without the end user's token in state."""
    from verifiable_answer_agent.tools import USER_TOKEN_STATE_KEY

    session = Session(id="synthetic-session", app_name="agent-tests", user_id=TEST_PRINCIPAL)
    if token is not None:
        session.state[USER_TOKEN_STATE_KEY] = token
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
