"""The deployed runtime's requirements keep every platform marker from uv.lock.

The first real deploy (2026-09-22) failed on Google's Linux build because a hand reading of the
lock dropped the markers and asked for ``pywin32``, which exists only for Windows. The list now
comes from ``uv export``; these tests hold it to that.

Resolving for Google's build platform reads package metadata from the index, so the two tests
that resolve are skipped, not passed, where the index cannot be reached: until 2026-09-27 the
conflict test passed offline because the resolver failed for want of a network, not because it
found the conflict (audit AG-11)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))

import deploy_agent_engine

# What uv writes when it could not reach the index: the answer is then about the network.
_OFFLINE = (
    "failed to fetch",
    "error sending request",
    "dns error",
    "network",
    "connection",
    "offline",
    "timed out",
)


def _resolve(requirements: list[str]) -> str | None:
    """``None`` when the set resolves; otherwise uv's own words. Skips when uv is offline."""
    try:
        deploy_agent_engine.check_resolves(requirements)
    except SystemExit as refusal:
        message = str(refusal)
        if any(sign in message.lower() for sign in _OFFLINE):
            pytest.skip("the package index cannot be reached; nothing was resolved")
        return message
    return None


def test_every_requirement_is_pinned_exactly() -> None:
    requirements = deploy_agent_engine.requirements_from_lock()
    assert len(requirements) > 40
    for requirement in requirements:
        assert "==" in requirement.split(";")[0], requirement


def test_windows_only_packages_keep_their_marker() -> None:
    for requirement in deploy_agent_engine.requirements_from_lock():
        if requirement.split("==")[0].strip().lower() in {"pywin32", "pywin32-ctypes"}:
            assert "sys_platform == 'win32'" in requirement, requirement


def test_the_runtime_gets_both_sdks_and_cloudpickle_pinned() -> None:
    assert deploy_agent_engine.AGENT_PLATFORM_SDK.endswith("==2.1.3")
    assert deploy_agent_engine.AIPLATFORM.endswith("==2.1.3")
    assert deploy_agent_engine.CLOUDPICKLE == "cloudpickle==3.1.2"


def test_a_conflicting_set_is_refused_before_upload() -> None:
    refusal = _resolve(["google-genai==2.24.0", "google-genai==1.0.0"])
    assert refusal is not None
    assert "do not resolve" in refusal
    # uv's own finding, not a failure to look.
    assert "no solution found" in refusal.lower()


def test_a_set_that_resolves_is_let_through() -> None:
    assert _resolve(["google-genai==2.24.0"]) is None


# --- what is deployed, and under which version (audit AG-9, AG-11) ------------------------

ENV = {
    "AGENT_ENGINE_PROJECT": "synthetic-project",
    "AGENT_ENGINE_LOCATION": "europe-west4",
    "AGENT_ENGINE_STAGING_BUCKET": "gs://synthetic-bucket",
    "QUERY_SERVICE_MCP_URL": "https://query.invalid/mcp",
    "AGENT_MODEL": "gemini-2.5-flash",
}


def _environment(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    for name in (
        "AGENT_SERVICE_VERSION",
        "MCP_TIMEOUT_SECONDS",
        "AGENT_PRINCIPAL_DIGEST_SECRET",
        "AGENT_ENGINE_SERVICE_ACCOUNT",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in (ENV | extra).items():
        monkeypatch.setenv(name, value)


def test_a_typed_service_version_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _environment(monkeypatch, AGENT_SERVICE_VERSION="agent/0.1.0")
    with pytest.raises(SystemExit, match="unset AGENT_SERVICE_VERSION"):
        deploy_agent_engine.read_environment()


def test_the_version_names_the_commit_and_a_changed_tree_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = {"status": "", "rev-parse": "0123abc" * 5 + "01234\n"}

    def git(*arguments: str) -> str:
        return answers[arguments[0]]

    monkeypatch.setattr(deploy_agent_engine, "_git", git)
    version = deploy_agent_engine.service_version()
    assert version.startswith("agent/")
    assert version.endswith("+" + answers["rev-parse"].strip())
    answers["status"] = " M src/verifiable_answer_agent/finish.py\n"
    with pytest.raises(SystemExit, match="differs from its commit"):
        deploy_agent_engine.service_version()


def test_the_runtime_gets_its_settings_and_a_secret_by_reference_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _environment(
        monkeypatch,
        MCP_TIMEOUT_SECONDS="20",
        AGENT_PRINCIPAL_DIGEST_SECRET="agent-principal-digest-key",
        AGENT_ENGINE_SERVICE_ACCOUNT="ema-flow-agent@synthetic-project.iam.gserviceaccount.com",
    )
    monkeypatch.setattr(deploy_agent_engine, "requirements_from_lock", list)
    settings = deploy_agent_engine.read_environment()
    settings["AGENT_SERVICE_VERSION"] = "agent/0.1.0+abc"
    config: dict[str, Any] = deploy_agent_engine.build_config(settings)
    assert config["env_vars"] == {
        "QUERY_SERVICE_MCP_URL": "https://query.invalid/mcp",
        "AGENT_MODEL": "gemini-2.5-flash",
        "AGENT_SERVICE_VERSION": "agent/0.1.0+abc",
        "MCP_TIMEOUT_SECONDS": "20",
        "AGENT_PRINCIPAL_DIGEST_KEY": {"secret": "agent-principal-digest-key", "version": "latest"},
    }
    assert config["service_account"] == ("ema-flow-agent@synthetic-project.iam.gserviceaccount.com")


def test_without_the_optional_settings_nothing_is_invented(monkeypatch: pytest.MonkeyPatch) -> None:
    _environment(monkeypatch)
    monkeypatch.setattr(deploy_agent_engine, "requirements_from_lock", list)
    settings = deploy_agent_engine.read_environment()
    settings["AGENT_SERVICE_VERSION"] = "agent/0.1.0+abc"
    config = deploy_agent_engine.build_config(settings)
    assert sorted(config["env_vars"]) == [
        "AGENT_MODEL",
        "AGENT_SERVICE_VERSION",
        "QUERY_SERVICE_MCP_URL",
    ]
    assert "service_account" not in config
