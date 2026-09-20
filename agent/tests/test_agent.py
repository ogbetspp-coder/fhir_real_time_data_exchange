"""The agent object is constructed but never run: no model is called anywhere in this suite."""

from __future__ import annotations

import pytest
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset

from verifiable_answer_agent.agent import AGENT_NAME, build_agent
from verifiable_answer_agent.config import AgentConfig, MissingConfigurationError
from verifiable_answer_agent.instruction import SYSTEM_INSTRUCTION
from verifiable_answer_agent.tools import QUERY_TOOL_NAMES, bearer_header_provider, begin_turn

CONFIG = AgentConfig(
    query_service_url="https://example.invalid/mcp",
    model="gemini-3.5-flash",
    service_version="agent/0.1.0",
)


def test_the_agent_constructs_with_the_instruction_and_one_toolset() -> None:
    agent = build_agent(CONFIG)
    assert isinstance(agent, LlmAgent)
    assert agent.name == AGENT_NAME
    assert agent.model == "gemini-3.5-flash"
    assert agent.instruction == SYSTEM_INSTRUCTION
    (toolset,) = agent.tools
    assert isinstance(toolset, McpToolset)


def test_the_toolset_is_filtered_to_the_four_tools_and_signs_as_the_user() -> None:
    (toolset,) = build_agent(CONFIG).tools
    assert isinstance(toolset, McpToolset)
    tool_filter = toolset.tool_filter
    assert isinstance(tool_filter, list)
    assert sorted(tool_filter) == sorted(QUERY_TOOL_NAMES)
    assert toolset._header_provider is bearer_header_provider


def test_the_turn_id_is_generated_before_the_agent_runs() -> None:
    agent = build_agent(CONFIG)
    assert agent.before_agent_callback is begin_turn


def test_the_configuration_has_no_defaults_to_fall_back_on() -> None:
    for missing in ("QUERY_SERVICE_MCP_URL", "AGENT_MODEL", "AGENT_SERVICE_VERSION"):
        env = {
            "QUERY_SERVICE_MCP_URL": "https://example.invalid/mcp",
            "AGENT_MODEL": "gemini-3.5-flash",
            "AGENT_SERVICE_VERSION": "agent/0.1.0",
        }
        del env[missing]
        with pytest.raises(MissingConfigurationError, match=missing):
            AgentConfig.from_env(env)


def test_the_instruction_says_the_four_things_it_is_meant_to_say() -> None:
    # Not a wording test: the post-check does not care what the model was told. This only
    # checks that layer 3 has not been quietly emptied out.
    lowered = SYSTEM_INSTRUCTION.lower()
    assert "quote" in lowered
    assert "tool" in lowered
    assert "sourcekey" in lowered
    assert "never" in lowered


def test_the_instruction_covers_a_truncated_product_search() -> None:
    # query-tools 2.0.0: truncated true means the list is shorter than the entitlement holds,
    # either because documents went unsearched or because more matched than the limit returns.
    # So neither an empty result nor a full one may be presented as the whole answer. Not a
    # wording test, only that the instruction addresses the field and both of its causes.
    assert "truncated" in SYSTEM_INSTRUCTION
    assert "incomplete" in SYSTEM_INSTRUCTION
    assert "narrower" in SYSTEM_INSTRUCTION
    assert "no such product" in SYSTEM_INSTRUCTION
    assert "complete" in SYSTEM_INSTRUCTION
