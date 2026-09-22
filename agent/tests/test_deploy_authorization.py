"""The Gemini Enterprise authorization id and the agent's state key are one fact, kept in two
places: Gemini Enterprise hands the end user's token to the agent as ``temp:<authorization id>``,
and the agent reads ``tools.USER_TOKEN_STATE_KEY``. If either drifts, every tool call fails
closed with MissingUserTokenError and the agent can answer nothing."""

from __future__ import annotations

import re
from pathlib import Path

from verifiable_answer_agent.tools import USER_TOKEN_STATE_KEY

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def _authorization_id(script: str) -> str:
    match = re.search(r'^AUTHORIZATION_ID="([a-z0-9_]+)"$', (DEPLOY / script).read_text(), re.M)
    assert match, f"{script} does not pin AUTHORIZATION_ID"
    return match.group(1)


def test_authorization_id_matches_the_state_key() -> None:
    assert USER_TOKEN_STATE_KEY.startswith("temp:")
    expected = USER_TOKEN_STATE_KEY.removeprefix("temp:")
    assert _authorization_id("authorization.sh") == expected
    assert _authorization_id("register.sh") == expected


def test_registration_names_that_authorization_and_only_the_four_tools_agent() -> None:
    register = (DEPLOY / "register.sh").read_text()
    assert (
        '"toolAuthorizations": [f"projects/{number}/locations/global/authorizations/{auth}"]'
        in register
    )
    assert "Does not search the web" in register
