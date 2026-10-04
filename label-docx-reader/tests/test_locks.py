"""What the reader produces is tied to the code that produces it (scripts/lock.py)."""

from __future__ import annotations

import json
import os

from lock import CORPUS, VERSIONS, base, current_versions, expected, released, released_problems


def test_the_current_versions_are_locked_to_the_current_code() -> None:
    lock = json.loads(VERSIONS.read_text("utf-8"))
    for name, (version, digest) in current_versions().items():
        locked = lock.get(name, {}).get(version)
        assert locked is not None, f"{name} {version} is not locked: run scripts/lock.py"
        assert locked == digest, f"{name} changed under {version}: bump it, run scripts/lock.py"


def test_every_corpus_document_reads_as_locked() -> None:
    folders = sorted(path for path in CORPUS.iterdir() if path.is_dir())
    assert folders
    for folder in folders:
        locked = json.loads((folder / "expected.json").read_text("utf-8"))
        assert locked == expected(folder), f"{folder.name}: review, then run scripts/lock.py"


def test_every_released_entry_is_kept() -> None:
    # Every lock in the first-parent history of LOCK_BASE (CI: scripts/ci/lock-base.sh) or
    # origin/main: a released version's hash never changes or goes.
    history = released(base())
    if os.environ.get("CI") == "true":
        assert history, "CI must name the lock's base (LOCK_BASE, full history)"
    if history is None:
        return
    assert released_problems(json.loads(VERSIONS.read_text("utf-8")), history) == []


def test_a_changed_or_dropped_released_entry_is_refused() -> None:
    lock = json.loads(VERSIONS.read_text("utf-8"))
    version, _ = current_versions()["reader"]
    changed = [("0" * 40, {"reader": {version: "f" * 64}})]
    (problem,) = released_problems(lock, changed)
    assert problem.startswith(f"reader {version} was released")
    dropped = {name: dict(entries) for name, entries in lock.items()}
    del dropped["reader"][version]
    (problem,) = released_problems(dropped, [("0" * 40, lock)])
    assert problem.startswith(f"reader {version} was released")
    assert released_problems(lock, [("0" * 40, lock)]) == []
