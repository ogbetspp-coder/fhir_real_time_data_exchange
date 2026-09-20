"""The ADK agent object: a name, a pinned model, the instruction, and the four tools.

Deliberately thin. Everything that decides whether an answer is trustworthy lives in
``compose``, ``postcheck`` and ``audit``, none of which this module can skip: ``render`` takes
only a ``CheckedAnswer``.

``root_agent`` is the attribute Agent Engine and the ADK CLI look for. It is built lazily by
``build_agent`` so that importing this module does not read the environment — which is what
lets the tests import the package with no configuration present.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent

from .config import AgentConfig
from .instruction import SYSTEM_INSTRUCTION
from .tools import build_query_toolset

__all__ = ["AGENT_DESCRIPTION", "AGENT_NAME", "build_agent"]

AGENT_NAME = "verifiable_answer_agent"

AGENT_DESCRIPTION = (
    "Answers questions about approved product information by quoting the validated store "
    "verbatim with citations, and re-checks every quotation against the store before showing "
    "it. Does not draft, summarise, or amend label content."
)


def build_agent(config: AgentConfig | None = None) -> LlmAgent:
    """Construct the agent. Pure: no network call, no model resolution until it is run."""
    settings = config if config is not None else AgentConfig.from_env()
    return LlmAgent(
        name=AGENT_NAME,
        model=settings.model,
        description=AGENT_DESCRIPTION,
        instruction=SYSTEM_INSTRUCTION,
        tools=[build_query_toolset(settings)],
    )
