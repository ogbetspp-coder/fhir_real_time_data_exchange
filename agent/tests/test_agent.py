"""The agent object is constructed but never run: no model is called anywhere in this suite."""

from __future__ import annotations

import functools

import pytest
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset

from verifiable_answer_agent.agent import AGENT_NAME, build_agent
from verifiable_answer_agent.config import (
    AgentConfig,
    MissingConfigurationError,
    principal_digest_key,
)
from verifiable_answer_agent.instruction import SYSTEM_INSTRUCTION
from verifiable_answer_agent.tools import (
    QUERY_TOOL_NAMES,
    ToolCallLog,
    bearer_header_provider,
    begin_turn,
)

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
    # The provider is bound to the service's own audience so it can also satisfy Cloud Run's edge
    # check; what the service itself sees is still the user's token.
    provider = toolset._header_provider
    assert isinstance(provider, functools.partial)
    assert provider.func is bearer_header_provider
    assert provider.keywords == {"audience": "https://example.invalid"}


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


BASE_ENV = {
    "QUERY_SERVICE_MCP_URL": "https://example.invalid/mcp",
    "AGENT_MODEL": "gemini-3.5-flash",
    "AGENT_SERVICE_VERSION": "agent/0.1.0",
}


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid/mcp",
        "http://10.0.0.7/mcp",
        "ftp://example.invalid/mcp",
        "example.invalid/mcp",
        "https:///mcp",
    ],
)
def test_a_url_that_would_send_the_token_in_clear_is_refused(url: str) -> None:
    # Every request carries the user's bearer token (audit AG-11).
    with pytest.raises(ValueError, match="https"):
        AgentConfig.from_env(BASE_ENV | {"QUERY_SERVICE_MCP_URL": url})


def test_loopback_may_be_plain_http_for_the_tests_fake_service() -> None:
    config = AgentConfig.from_env(BASE_ENV | {"QUERY_SERVICE_MCP_URL": "http://127.0.0.1:9/mcp"})
    assert config.query_service_url == "http://127.0.0.1:9/mcp"


@pytest.mark.parametrize("timeout", ["nan", "inf", "0", "-1", "601"])
def test_a_timeout_outside_the_bound_is_refused(timeout: str) -> None:
    with pytest.raises(ValueError, match="MCP_TIMEOUT_SECONDS"):
        AgentConfig.from_env(BASE_ENV | {"MCP_TIMEOUT_SECONDS": timeout})


def test_the_timeout_is_read_and_the_digest_key_never_enters_the_configuration() -> None:
    # The configuration is pickled with the agent at deploy time; the key is read at run time
    # from the runtime's own environment, so it is never in the pickle.
    env = BASE_ENV | {"MCP_TIMEOUT_SECONDS": "12.5", "AGENT_PRINCIPAL_DIGEST_KEY": "s3cret-key"}
    config = AgentConfig.from_env(env)
    assert config.timeout_seconds == 12.5
    assert "s3cret" not in repr(config)
    assert principal_digest_key(env) == b"s3cret-key"
    assert principal_digest_key(BASE_ENV) is None


def test_the_instruction_never_asks_the_model_to_write_label_text_ids_or_hashes() -> None:
    # Checked blocks are composed from get_section by code; whatever the model writes lands in
    # the unchecked assistant part. Until 2026-09-27 it was told to quote and to write each
    # quotation's ids and hash itself (audit AG-3).
    flat = " ".join(SYSTEM_INSTRUCTION.lower().split())
    assert "every quotation carries" not in flat
    assert "do not reproduce, quote, paraphrase or summarise label text" in flat
    assert "never write a bundleid, versionid, sourcekey, checksum or hash" in flat


def test_every_query_tool_call_is_logged_by_the_agents_own_callbacks() -> None:
    agent = build_agent(CONFIG)
    for callback in (
        agent.before_tool_callback,
        agent.after_tool_callback,
        agent.on_tool_error_callback,
    ):
        assert isinstance(getattr(callback, "__self__", None), ToolCallLog)


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
