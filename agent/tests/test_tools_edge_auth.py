"""Cloud Run's edge check, and the user's identity, are two separate credentials.

The first real Gemini Enterprise turn was refused 401 by Cloud Run before the service saw it:
the agent sent only the end user's OAuth token, which the edge does not accept. Google's own
connector sends its service identity in ``X-Serverless-Authorization`` as well, which Cloud Run
consumes and strips. These tests pin that the agent does the same, that the user's token is what
the service still receives, and that no edge credential ever substitutes for a missing user."""

from __future__ import annotations

from typing import Any

import pytest
from google.adk.agents.readonly_context import ReadonlyContext

from verifiable_answer_agent import tools


class _Context:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state


def _context(state: dict[str, Any]) -> ReadonlyContext:
    return _Context(state)  # type: ignore[return-value]


def test_the_audience_is_the_services_own_url() -> None:
    assert tools.service_audience("https://svc-123.europe-west4.run.app/mcp") == (
        "https://svc-123.europe-west4.run.app"
    )


def test_both_credentials_are_sent_and_the_user_is_the_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools, "edge_auth_token", lambda _audience: "edge-token")
    headers = tools.bearer_header_provider(
        _context({tools.USER_TOKEN_STATE_KEY: "user-token"}), audience="https://svc.run.app"
    )
    assert headers["Authorization"] == "Bearer user-token"
    assert headers[tools.EDGE_AUTH_HEADER] == "Bearer edge-token"


def test_no_edge_credential_means_no_header_rather_than_another_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools, "edge_auth_token", lambda _audience: None)
    headers = tools.bearer_header_provider(
        _context({tools.USER_TOKEN_STATE_KEY: "user-token"}), audience="https://svc.run.app"
    )
    assert tools.EDGE_AUTH_HEADER not in headers
    assert headers["Authorization"] == "Bearer user-token"


def test_the_edge_credential_never_stands_in_for_a_missing_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools, "edge_auth_token", lambda _audience: "edge-token")
    with pytest.raises(tools.MissingUserTokenError):
        tools.bearer_header_provider(_context({}), audience="https://svc.run.app")


def test_the_edge_token_is_reused_until_it_nears_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    minted: list[str] = []

    def fake_fetch(_request: object, audience: str) -> str:
        minted.append(audience)
        return f"token-{len(minted)}"

    monkeypatch.setattr(tools, "_edge_tokens", {})
    import google.oauth2.id_token as google_id_token

    monkeypatch.setattr(google_id_token, "fetch_id_token", fake_fetch)
    clock = [1000.0]
    first = tools.edge_auth_token("https://svc.run.app", now=lambda: clock[0])
    second = tools.edge_auth_token("https://svc.run.app", now=lambda: clock[0])
    assert first == second == "token-1"
    assert minted == ["https://svc.run.app"]
    clock[0] += 3600.0
    third = tools.edge_auth_token("https://svc.run.app", now=lambda: clock[0])
    assert third == "token-2"
