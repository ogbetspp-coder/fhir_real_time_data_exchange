"""The version lock: a change to what a reader, the registry build or the QRD check does bumps
its version (``scripts/lock_versions.py``, ``versions.lock.json``)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from label_docx import reader as docx_reader

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
    component = script.COMPONENTS["qrd-check"]
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


# What python -m zone_a.drawing imports that decides nothing it writes: package docstrings and
# re-exports, the ePI reader (certified.py reads ePIs with it, never a .docx) and the fidelity
# verifier its package re-exports, which the drawing never calls.
_DECIDES_NOTHING = {
    "label-docx-reader/src/label_docx/__init__.py",
    "label-docx-reader/src/label_docx/epi_output.py",
    "zone-a/src/zone_a/__init__.py",
    "zone-a/src/zone_a/fidelity/__init__.py",
    "zone-a/src/zone_a/fidelity/verify.py",
    "zone-a/src/zone_a/qrd/__init__.py",
}
_SOURCES = ("zone-a/src", "label-docx-reader/src")
# The components whose versions the drawing request names (zone_a.recompute.versions).
_NAMED = ("recompute", "docx-reader", "docx-format", "smpc-structure", "pl-structure", "word-epi")


def test_the_drawings_version_covers_what_decides_the_record() -> None:
    """Every module the entry point imports is the drawing's own, or versioned in the request."""
    script = _script()
    repository = ZONE_A.parent
    with subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            "import json, sys, zone_a.drawing; "
            "print(json.dumps([getattr(m, '__file__', None) for m in list(sys.modules.values())]))",
        ],
        stdout=subprocess.PIPE,
        env={},
    ) as process:
        loaded, _ = process.communicate()
    ours = {
        Path(file).resolve().relative_to(repository).as_posix()
        for file in json.loads(loaded)
        if file and any(Path(file).resolve().is_relative_to(repository / src) for src in _SOURCES)
    }

    def files(name: str) -> set[str]:
        return {
            (ZONE_A / file).resolve().relative_to(repository).as_posix()
            for file in script.COMPONENTS[name].files
        }

    drawing = files("word-drawing")
    named = set().union(*(files(name) for name in _NAMED))
    assert {"zone-a/src/zone_a/canonical_json.py", "zone-a/src/zone_a/recompute.py"} <= drawing
    assert sorted(ours - drawing - named - _DECIDES_NOTHING) == []
    assert ours >= _DECIDES_NOTHING
