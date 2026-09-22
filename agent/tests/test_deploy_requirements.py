"""The deployed runtime's requirements keep every platform marker from uv.lock.

The first real deploy (2026-09-22) failed on Google's Linux build because a hand reading of the
lock dropped the markers and asked for ``pywin32``, which exists only for Windows. The list now
comes from ``uv export``; these tests hold it to that."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))

import deploy_agent_engine


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
    import pytest

    with pytest.raises(SystemExit, match="do not resolve"):
        deploy_agent_engine.check_resolves(["google-genai==2.24.0", "google-genai==1.0.0"])
