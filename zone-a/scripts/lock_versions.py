"""Write, or check, the version lock of Zone A's readers, registry and checks.

    uv run --frozen python scripts/lock_versions.py           # add the current versions
    uv run --frozen python scripts/lock_versions.py --check   # fail on unlocked code
    uv run --frozen python scripts/lock_versions.py --amend   # re-lock an unreleased version

Each component's results carry its version (a check result names ``reader``, ``format`` and
``checker``, the registry ``readerVersion``, ``readerFormat`` and ``registryVersion``, an
implementation report ``checker`` and ``readers``), so a change to what the code does must change
the version. ``versions.lock.json`` records, for every version of every component, the SHA-256 of
the source files that decide its output, as ``src/authority/importer.lock.json`` does for the
authority importer. A change to one of those files leaves the current version locked to other code,
which tests/test_versions_lock.py refuses until the version is bumped and this script run.
``--amend`` re-locks a version to changed code, and refuses one any lock in main's first-parent
history holds (``released``); the test holds every released entry unchanged too, as the importer's
lock test does. Versions released before the lock existed are not in it.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

ZONE_A = Path(__file__).resolve().parents[1]
LOCK = ZONE_A / "versions.lock.json"


class Component(NamedTuple):
    """A versioned output: the constant that names its version and the files that decide it."""

    module: str
    constant: str
    files: tuple[str, ...]


# The files each version covers: the component's own module and every module of this package it
# reads with (zone_a.underline and zone_a.fidelity.normalize decide which characters count).
# The readers and their formats are the label reader's (label-docx-reader/), each tied to the
# files its own lock ties its version to (label-docx-reader/scripts/lock.py, current_versions),
# so a change it versions there is versioned here; the results name both. zone_a.certified, which
# rebuilds the values the registry and the check read from the certified JSON, is theirs.
_LABEL_DOCX = "../label-docx-reader/src/label_docx"
COMPONENTS: dict[str, Component] = {
    "docx-reader": Component("label_docx.reader", "READER_VERSION", (f"{_LABEL_DOCX}/reader.py",)),
    "docx-format": Component(
        "label_docx.output",
        "FORMAT_VERSION",
        tuple(f"{_LABEL_DOCX}/{name}.py" for name in ("output", "certify", "documents")),
    ),
    "epi-reader": Component(
        "label_docx.epi", "READER_VERSION", (f"{_LABEL_DOCX}/epi.py", f"{_LABEL_DOCX}/reader.py")
    ),
    "epi-format": Component(
        "label_docx.epi_output",
        "FORMAT_VERSION",
        tuple(
            f"{_LABEL_DOCX}/{name}.py" for name in ("epi_output", "output", "documents", "certify")
        ),
    ),
    "qrd-registry": Component(
        "zone_a.qrd.registry",
        "REGISTRY_VERSION",
        (
            "src/zone_a/qrd/registry.py",
            "src/zone_a/certified.py",
            "src/zone_a/qrd/pattern.py",
            "src/zone_a/underline.py",
            "src/zone_a/fidelity/normalize.py",
        ),
    ),
    "implementation-check": Component(
        "zone_a.implementation",
        "IMPLEMENTATION_VERSION",
        ("src/zone_a/implementation.py", "src/zone_a/certified.py"),
    ),
    "smpc-structure": Component(
        "zone_a.structure",
        "STRUCTURE_VERSION",
        ("src/zone_a/structure.py", "src/zone_a/qrd/headings.py", "src/zone_a/qrd/registry.py"),
    ),
    "pl-structure": Component(
        "zone_a.leaflet",
        "LEAFLET_VERSION",
        (
            "src/zone_a/leaflet.py",
            "src/zone_a/structure.py",
            "src/zone_a/qrd/headings.py",
            "src/zone_a/qrd/pattern.py",
            "src/zone_a/qrd/registry.py",
        ),
    ),
    "recompute": Component("zone_a.recompute", "RECOMPUTE_VERSION", ("src/zone_a/recompute.py",)),
    "word-epi": Component(
        "zone_a.word_epi",
        "WORD_EPI_VERSION",
        (
            "src/zone_a/word_epi.py",
            "src/zone_a/certified.py",
            "src/zone_a/structure.py",
            "src/zone_a/qrd/headings.py",
            "src/zone_a/underline.py",
            "src/zone_a/fidelity/normalize.py",
            "src/zone_a/fidelity/xhtml.py",
        ),
    ),
    # The drawing record's fields (python -m zone_a.drawing) are decided by these and by the
    # versions its request names (the recompute's, the reader's, the structurer's, the builder's):
    # canonical JSON and its hashes, the recompute's written bytes, the statements the structurer
    # skips (qrd/check.py, under no version of its own) and the reader's thresholds the browser
    # check marks by (epi.py). tests/test_versions_lock.py holds this list to what it imports.
    "word-drawing": Component(
        "zone_a.drawing",
        "DRAWING_VERSION",
        (
            "src/zone_a/drawing.py",
            "src/zone_a/word_epi.py",
            f"{_LABEL_DOCX}/browser.py",
            "src/zone_a/recompute.py",
            "src/zone_a/canonical_json.py",
            "src/zone_a/qrd/check.py",
            f"{_LABEL_DOCX}/epi.py",
        ),
    ),
    "product": Component(
        "zone_a.product", "PRODUCT_VERSION", ("src/zone_a/product.py", "src/zone_a/structure.py")
    ),
    "qrd-check": Component(
        "zone_a.qrd.check",
        "CHECKER_VERSION",
        (
            "src/zone_a/qrd/check.py",
            "src/zone_a/certified.py",
            "src/zone_a/qrd/headings.py",
            "src/zone_a/qrd/pattern.py",
            "src/zone_a/underline.py",
            "src/zone_a/fidelity/normalize.py",
        ),
    ),
}


def source_sha256(component: Component) -> str:
    """The SHA-256 of the component's files: each path, a NUL, its bytes, a NUL, in order."""
    digest = hashlib.sha256()
    for file in component.files:
        digest.update(f"{file}\0".encode())
        digest.update((ZONE_A / file).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def current_version(component: Component) -> str:
    """The version the component's constant names now."""
    return str(getattr(importlib.import_module(component.module), component.constant))


def load() -> dict[str, dict[str, str]]:
    """The lock: for each component, each locked version's source SHA-256."""
    return dict(json.loads(LOCK.read_text(encoding="utf-8"))) if LOCK.exists() else {}


def problems(lock: dict[str, dict[str, str]]) -> list[str]:
    """Why the lock does not hold the current code at its current version (empty when it does)."""
    out: list[str] = []
    if set(lock) != set(COMPONENTS):
        out.append(f"the lock's components are {sorted(lock)}, expected {sorted(COMPONENTS)}")
    for name, component in COMPONENTS.items():
        version = current_version(component)
        locked = lock.get(name, {}).get(version)
        if locked is None:
            out.append(f"{name} {version} is not locked; run scripts/lock_versions.py")
        elif locked != source_sha256(component):
            out.append(
                f"{name}: the code changed but {component.module}.{component.constant} is still "
                f"{version}; bump it and run scripts/lock_versions.py"
            )
    return out


def _git(*arguments: str) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(ZONE_A.parent), *arguments],
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        ).stdout.strip()
    except OSError, subprocess.CalledProcessError:
        return None


def released(base: str) -> list[tuple[str, dict[str, dict[str, str]]]] | None:
    """Every lock in the first-parent history of ``base``, with its commit; None without it.

    What was released, as for the importer's lock (test/authority/lock.test.ts): in CI ``base`` is
    scripts/ci/lock-base.sh's LOCK_BASE (the commit before a push, main otherwise), locally
    origin/main. Reading the whole history means neither a second push nor a manual run after a
    changed released entry compares with the change itself.
    """
    commit = _git("rev-parse", "--verify", f"{base}^{{commit}}")
    if commit is None:
        return None
    path = LOCK.relative_to(ZONE_A.parent).as_posix()
    history = _git("log", "--first-parent", "--format=%H", commit, "--", path) or ""
    out: list[tuple[str, dict[str, dict[str, str]]]] = []
    for each in filter(None, history.split("\n")):
        text = _git("show", f"{each}:{path}")
        if text is not None:  # the commit that deleted the lock, if any
            out.append((each, dict(json.loads(text))))
    return out


def released_problems(
    lock: dict[str, dict[str, str]], history: list[tuple[str, dict[str, dict[str, str]]]]
) -> list[str]:
    """Every released entry the lock no longer holds unchanged."""
    out: list[str] = []
    for commit, old in history:
        for name, versions in old.items():
            for version, sha256 in versions.items():
                if lock.get(name, {}).get(version) != sha256:
                    out.append(f"{name} {version} was released ({commit}); bump the version")
    return out


def base() -> str:
    """The commit whose history is released: LOCK_BASE in CI, else origin/main."""
    return os.environ.get("LOCK_BASE", "origin/main")


def main() -> int:
    """Adds the current versions to the lock, or with ``--check`` reports unlocked code.

    Returns:
        The exit status: 0 when the lock holds the current code, 1 when it does not (or, when
        writing, when a locked version's code changed without a bump).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="fail if the lock is out of date")
    mode.add_argument("--amend", action="store_true", help="re-lock an unreleased version")
    arguments = parser.parse_args()
    lock = load()
    history = released(base()) or []
    if arguments.check:
        found = problems(lock) + released_problems(lock, history)
        for problem in found:
            print(problem)
        return 1 if found else 0
    shipped = {(name, version) for _, old in history for name in old for version in old[name]}
    status = 0
    for name, component in COMPONENTS.items():
        version, sha256 = current_version(component), source_sha256(component)
        versions = lock.setdefault(name, {})
        if versions.get(version, sha256) != sha256 and (
            not arguments.amend or (name, version) in shipped
        ):
            # --amend re-locks only a version no released lock holds.
            print(f"{name} {version} is locked to other code; bump the version")
            status = 1
            continue
        versions[version] = sha256
    if status == 0:
        LOCK.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {LOCK.relative_to(ZONE_A)}")
    return status


if __name__ == "__main__":
    sys.exit(main())
