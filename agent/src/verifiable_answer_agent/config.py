"""Configuration, read once from the environment and never from another service's config.

ADR 0004 item 3: nothing implicit is shared. This deployable reads its own variables, holds its
own identity, and knows the query service only by URL.
"""

from __future__ import annotations

import math
import os
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, final

__all__ = ["AgentConfig", "MissingConfigurationError", "principal_digest_key"]

QUERY_SERVICE_URL: Final = "QUERY_SERVICE_MCP_URL"
AGENT_MODEL: Final = "AGENT_MODEL"
AGENT_SERVICE_VERSION: Final = "AGENT_SERVICE_VERSION"
MCP_TIMEOUT_SECONDS: Final = "MCP_TIMEOUT_SECONDS"
PRINCIPAL_DIGEST_KEY: Final = "AGENT_PRINCIPAL_DIGEST_KEY"

DEFAULT_TIMEOUT_SECONDS: Final = 30.0
# A timeout outside this range is a typo, not a choice: zero or less never waits, and anything
# past ten minutes outlives the turn it belongs to.
MAX_TIMEOUT_SECONDS: Final = 600.0
# The only hosts that may be reached over plain http: the tests' fake service, on loopback.
_LOOPBACK: Final = frozenset({"127.0.0.1", "::1", "localhost"})


class MissingConfigurationError(RuntimeError):
    """A required variable is absent. There is no default for any of them."""


@final
@dataclass(frozen=True, slots=True)
class AgentConfig:
    """The agent's own configuration, read from its environment.

    The query service's MCP URL, the model, this service's version, and the MCP timeout in
    seconds (30 unless ``MCP_TIMEOUT_SECONDS`` sets it). The deploy script builds the agent with
    this and Agent Engine runs a pickled copy of it, so every value here is fixed at deploy time.
    The one secret the agent can hold is therefore not here: ``principal_digest_key`` reads it
    from the runtime's own environment on each turn, so it is never in the pickle.
    """

    query_service_url: str
    model: str
    service_version: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        """Refuses a URL that would send the user's token in clear, and an unusable timeout.

        Raises:
            ValueError: The URL is not https (loopback excepted) or the timeout is not a finite
                number of seconds above zero and at most ``MAX_TIMEOUT_SECONDS``.
        """
        parts = urllib.parse.urlsplit(self.query_service_url)
        if not parts.hostname or not (
            parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in _LOOPBACK)
        ):
            raise ValueError(
                f"{QUERY_SERVICE_URL} must be an https URL: every request carries the user's token"
            )
        if not (
            math.isfinite(self.timeout_seconds) and 0 < self.timeout_seconds <= MAX_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"{MCP_TIMEOUT_SECONDS} must be above 0 and at most {MAX_TIMEOUT_SECONDS:g} seconds"
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AgentConfig:
        """The configuration read from ``env``, or from the process environment when it is None.

        Raises:
            MissingConfigurationError: ``QUERY_SERVICE_MCP_URL``, ``AGENT_MODEL`` or
                ``AGENT_SERVICE_VERSION`` is unset or blank.
            ValueError: The URL is not https, or ``MCP_TIMEOUT_SECONDS`` is set and is not a
                number in range.
        """
        source = env if env is not None else os.environ
        timeout = source.get(MCP_TIMEOUT_SECONDS, "").strip()
        return cls(
            query_service_url=_require(source, QUERY_SERVICE_URL),
            model=_require(source, AGENT_MODEL),
            service_version=_require(source, AGENT_SERVICE_VERSION),
            timeout_seconds=float(timeout) if timeout else DEFAULT_TIMEOUT_SECONDS,
        )


def principal_digest_key(env: Mapping[str, str] | None = None) -> bytes | None:
    """The key the audit record's ``principalDigest`` is made under, or ``None``.

    ``AGENT_PRINCIPAL_DIGEST_KEY`` in the runtime's environment — on Agent Engine a Secret
    Manager reference the deploy script names, never a value it holds. Read when a turn ends, not
    at deploy time. Without it a withheld session user is not identified in the record at all.
    """
    source = env if env is not None else os.environ
    key = source.get(PRINCIPAL_DIGEST_KEY, "").strip()
    return key.encode("utf-8") if key else None


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise MissingConfigurationError(f"{name} is not set")
    return value
