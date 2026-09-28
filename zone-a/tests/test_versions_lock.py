"""The version lock: a change to what a reader, the registry build or the QRD check does bumps
its version (``scripts/lock_versions.py``, ``versions.lock.json``)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType

from zone_a.docx import reader as docx_reader
from zone_a.qrd import registry

ZONE_A = Path(__file__).resolve().parents[1]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "lock_versions", ZONE_A / "scripts" / "lock_versions.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_lock_holds_every_component_at_its_current_version() -> None:
    script = _script()
    assert script.problems(script.load()) == []


def test_code_changed_under_a_locked_version_is_refused() -> None:
    script = _script()
    lock = script.load()
    version = script.current_version(script.COMPONENTS["qrd-check"])
    changed = {**lock, "qrd-check": {**lock["qrd-check"], version: "0" * 64}}
    (problem,) = script.problems(changed)
    assert problem.startswith("qrd-check: the code changed")


def test_a_version_not_in_the_lock_is_refused() -> None:
    script = _script()
    lock = script.load()
    missing = {**lock, "epi-reader": {}}
    (problem,) = script.problems(missing)
    assert "is not locked" in problem


def test_the_hash_covers_every_file_by_name_and_content() -> None:
    script = _script()
    component = script.COMPONENTS["epi-reader"]
    fewer = script.Component(component.module, component.constant, component.files[:1])
    assert script.source_sha256(fewer) != script.source_sha256(component)
    for name, each in script.COMPONENTS.items():
        for file in each.files:
            assert (ZONE_A / file).is_file(), (name, file)


def test_every_released_entry_is_kept() -> None:
    # Every lock in the first-parent history of LOCK_BASE (CI: scripts/ci/lock-base.sh) or
    # origin/main: a released version's hash never changes, --amend or not.
    script = _script()
    history = script.released(script.base())
    if os.environ.get("CI") == "true":
        assert history is not None, "CI must name the lock's base (LOCK_BASE, full history)"
    if history is None:
        return
    assert script.released_problems(script.load(), history) == []


def test_a_changed_released_entry_is_refused() -> None:
    script = _script()
    lock = script.load()
    version = script.current_version(script.COMPONENTS["qrd-check"])
    history = [("0" * 40, {"qrd-check": {version: "f" * 64}})]
    (problem,) = script.released_problems(lock, history)
    assert problem.startswith(f"qrd-check {version} was released")
    assert script.released_problems(lock, [("0" * 40, lock)]) == []


def test_the_word_readers_version_lives_in_the_reader() -> None:
    # The registry records the Word reader's version; it is the reader's own constant.
    assert registry.READER_VERSION is docx_reader.READER_VERSION
