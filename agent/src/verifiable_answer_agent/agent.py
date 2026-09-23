"""The ADK agent object: a name, a pinned model, the instruction, the four tools, its callbacks.

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
from .finish import build_finish_turn
from .hold import DraftHold
from .instruction import SYSTEM_INSTRUCTION
from .tools import begin_turn, build_query_toolset

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
    toolset = build_query_toolset(settings)
    hold = DraftHold()
    return LlmAgent(
        name=AGENT_NAME,
        model=settings.model,
        description=AGENT_DESCRIPTION,
        instruction=SYSTEM_INSTRUCTION,
        tools=[toolset],
        # Generates the turn id before the model runs, so the first tool call already carries
        # X-Query-Turn-Id and the audit record's turnId is the same value.
        before_agent_callback=begin_turn,
        # The model is not called without the four query tools, and none of its text leaves
        # as an event: both are held for the turn's end (hold.py says why a callback at the end
        # alone is not enough).
        before_model_callback=hold.before_model,
        after_model_callback=hold.after_model,
        on_model_error_callback=hold.on_model_error,
        # The turn's only text: the checked, rendered answer. Bound to the toolset above so the
        # post-check's verify_quote calls travel the same MCP session, as the same user, under
        # the same turn id.
        after_agent_callback=build_finish_turn(settings, toolset, hold),
    )
