"""Configuration, read once from the environment and never from another service's config.

ADR 0004 item 3: nothing implicit is shared. This deployable reads its own variables, holds its
own identity, and knows the query service only by URL.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, final

__all__ = ["AgentConfig", "MissingConfigurationError"]

QUERY_SERVICE_URL: Final = "QUERY_SERVICE_MCP_URL"
AGENT_MODEL: Final = "AGENT_MODEL"
AGENT_SERVICE_VERSION: Final = "AGENT_SERVICE_VERSION"
MCP_TIMEOUT_SECONDS: Final = "MCP_TIMEOUT_SECONDS"


class MissingConfigurationError(RuntimeError):
    """A required variable is absent. There is no default for any of them."""


@final
@dataclass(frozen=True, slots=True)
class AgentConfig:
    """The agent's own configuration, read from its environment.

    The query service's MCP URL, the model, this service's version, and the MCP timeout in
    seconds (30 unless ``MCP_TIMEOUT_SECONDS`` sets it).
    """

    query_service_url: str
    model: str
    service_version: str
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AgentConfig:
        """The configuration read from ``env``, or from the process environment when it is None.

        Raises:
            MissingConfigurationError: ``QUERY_SERVICE_MCP_URL``, ``AGENT_MODEL`` or
                ``AGENT_SERVICE_VERSION`` is unset or blank.
            ValueError: ``MCP_TIMEOUT_SECONDS`` is set and is not a number.
        """
        source = env if env is not None else os.environ
        timeout = source.get(MCP_TIMEOUT_SECONDS)
        return cls(
            query_service_url=_require(source, QUERY_SERVICE_URL),
            model=_require(source, AGENT_MODEL),
            service_version=_require(source, AGENT_SERVICE_VERSION),
            timeout_seconds=float(timeout) if timeout else 30.0,
        )


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise MissingConfigurationError(f"{name} is not set")
    return value
