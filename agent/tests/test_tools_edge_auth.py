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

# The real minting function. conftest.py stubs ``tools.edge_auth_token`` for every test so that
# none waits on the metadata server; the tests here that exercise minting call this one, with
# Google's own calls patched below it.
edge_auth_token = tools.edge_auth_token
AUDIENCE = "https://svc.run.app"


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "_edge_tokens", {})
    monkeypatch.setattr(tools, "_edge_failures", {})


class _Context:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state


def _context(state: dict[str, Any]) -> ReadonlyContext:
    return _Context(state)  # type: ignore[return-value]


def test_the_audience_is_the_services_own_url() -> None:
    assert tools.service_audience("https://svc-123.europe-west4.run.app/mcp") == (
        "https://svc-123.europe-west4.run.app"
    )


async def test_both_credentials_are_sent_and_the_user_is_the_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools, "edge_auth_token", lambda _audience: "edge-token")
    headers = await tools.bearer_header_provider(
        _context({tools.USER_TOKEN_STATE_KEY: "user-token"}), audience="https://svc.run.app"
    )
    assert headers["Authorization"] == "Bearer user-token"
    assert headers[tools.EDGE_AUTH_HEADER] == "Bearer edge-token"


async def test_no_edge_credential_means_no_header_rather_than_another_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools, "edge_auth_token", lambda _audience: None)
    headers = await tools.bearer_header_provider(
        _context({tools.USER_TOKEN_STATE_KEY: "user-token"}), audience="https://svc.run.app"
    )
    assert tools.EDGE_AUTH_HEADER not in headers
    assert headers["Authorization"] == "Bearer user-token"


async def test_the_edge_credential_never_stands_in_for_a_missing_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools, "edge_auth_token", lambda _audience: "edge-token")
    with pytest.raises(tools.MissingUserTokenError):
        await tools.bearer_header_provider(_context({}), audience="https://svc.run.app")


def test_the_edge_token_is_reused_until_it_nears_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    minted: list[str] = []

    def fake_fetch(_request: object, audience: str) -> str:
        minted.append(audience)
        return f"token-{len(minted)}"

    import google.oauth2.id_token as google_id_token

    monkeypatch.setattr(google_id_token, "fetch_id_token", fake_fetch)
    clock = [1000.0]
    first = edge_auth_token(AUDIENCE, now=lambda: clock[0])
    second = edge_auth_token(AUDIENCE, now=lambda: clock[0])
    assert first == second == "token-1"
    assert minted == [AUDIENCE]
    clock[0] += 3600.0
    third = edge_auth_token(AUDIENCE, now=lambda: clock[0])
    assert third == "token-2"


# --- where the environment holds no ID token -------------------------------------------------


class _Scoped:
    def __init__(self, audience: str, refresh_error: Exception | None) -> None:
        self.audience = audience
        self.token: str | None = None
        self._refresh_error = refresh_error

    def refresh(self, _request: object) -> None:
        if self._refresh_error is not None:
            raise self._refresh_error
        self.token = f"signed-for-{self.audience}"


class _ServiceAccount:
    """A credential that can sign an ID token for an audience, as a service account can."""

    def __init__(self, refresh_error: Exception | None = None) -> None:
        self.audiences: list[str] = []
        self._refresh_error = refresh_error

    def with_target_audience(self, audience: str) -> _Scoped:
        self.audiences.append(audience)
        return _Scoped(audience, self._refresh_error)


class _UserCredential:
    """A person's credential, as on a laptop: it has no ``with_target_audience``."""


def _auth_error(name: str, message: str) -> Exception:
    """One of google-auth's own exceptions, which it declares without types."""
    import google.auth.exceptions

    error_type: type[Exception] = getattr(google.auth.exceptions, name)
    return error_type(message)


def _no_id_token_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import google.oauth2.id_token as google_id_token

    attempts: list[str] = []

    def refuse(_request: object, audience: str) -> str:
        attempts.append(audience)
        raise _auth_error("DefaultCredentialsError", "no ID token here (synthetic)")

    monkeypatch.setattr(google_id_token, "fetch_id_token", refuse)
    return attempts


def _default_credentials(monkeypatch: pytest.MonkeyPatch, credentials: object) -> None:
    import google.auth

    monkeypatch.setattr(google.auth, "default", lambda: (credentials, "synthetic-project"))


def test_a_service_account_signs_the_edge_token_when_the_environment_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_id_token_in_the_environment(monkeypatch)
    account = _ServiceAccount()
    _default_credentials(monkeypatch, account)
    assert edge_auth_token(AUDIENCE) == f"signed-for-{AUDIENCE}"
    assert account.audiences == [AUDIENCE]


def test_a_credential_that_cannot_sign_means_no_edge_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_id_token_in_the_environment(monkeypatch)
    _default_credentials(monkeypatch, _UserCredential())
    assert edge_auth_token(AUDIENCE) is None


def test_no_credential_at_all_means_no_edge_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import google.auth
    import google.auth.exceptions

    _no_id_token_in_the_environment(monkeypatch)

    def none_found() -> tuple[object, str]:
        raise _auth_error("DefaultCredentialsError", "no credential (synthetic)")

    monkeypatch.setattr(google.auth, "default", none_found)
    assert edge_auth_token(AUDIENCE) is None


@pytest.mark.parametrize("error", [OSError("unreachable"), None], ids=["os", "auth"])
def test_a_signing_failure_means_no_edge_token(
    monkeypatch: pytest.MonkeyPatch, error: Exception | None
) -> None:

    _no_id_token_in_the_environment(monkeypatch)
    refusal = error if error is not None else _auth_error("RefreshError", "refused (synthetic)")
    _default_credentials(monkeypatch, _ServiceAccount(refresh_error=refusal))
    assert edge_auth_token(AUDIENCE) is None


def test_a_failed_mint_is_not_retried_for_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    # Off Google Cloud each attempt waits on the metadata server; before 2026-09-22 every MCP
    # request of a turn waited again.
    attempts = _no_id_token_in_the_environment(monkeypatch)
    _default_credentials(monkeypatch, _UserCredential())
    clock = [1000.0]
    assert edge_auth_token(AUDIENCE, now=lambda: clock[0]) is None
    clock[0] += 59.0
    assert edge_auth_token(AUDIENCE, now=lambda: clock[0]) is None
    assert attempts == [AUDIENCE]
    # Another audience is its own question.
    assert edge_auth_token("https://other.run.app", now=lambda: clock[0]) is None
    assert attempts == [AUDIENCE, "https://other.run.app"]
    clock[0] += 2.0
    assert edge_auth_token(AUDIENCE, now=lambda: clock[0]) is None
    assert attempts == [AUDIENCE, "https://other.run.app", AUDIENCE]


def test_a_credential_that_appears_is_used_once_the_minute_is_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_id_token_in_the_environment(monkeypatch)
    _default_credentials(monkeypatch, _UserCredential())
    clock = [1000.0]
    assert edge_auth_token(AUDIENCE, now=lambda: clock[0]) is None
    _default_credentials(monkeypatch, _ServiceAccount())
    assert edge_auth_token(AUDIENCE, now=lambda: clock[0]) is None
    clock[0] += 61.0
    assert edge_auth_token(AUDIENCE, now=lambda: clock[0]) == f"signed-for-{AUDIENCE}"
    assert tools._edge_failures == {}
