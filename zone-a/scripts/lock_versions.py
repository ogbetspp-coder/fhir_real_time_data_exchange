"""Write, or check, the version lock of Zone A's readers, registry build and QRD check.

    uv run --frozen python scripts/lock_versions.py           # add the current versions
    uv run --frozen python scripts/lock_versions.py --check   # fail on unlocked code
    uv run --frozen python scripts/lock_versions.py --amend   # re-lock an unreleased version

Each component's results carry its version (a check result names ``reader`` and ``checker``, the
registry ``readerVersion`` and ``registryVersion``), so a change to what the code does must change
the version. ``versions.lock.json`` records, for every version of every component, the SHA-256 of
the source files that decide its output, as ``src/authority/importer.lock.json`` does for the
authority importer. A change to one of those files leaves the current version locked to other
code, which tests/test_versions_lock.py refuses until the version is bumped and this script run.
``--amend`` re-locks a version to changed code, for a version not yet merged to main only (a
reviewer checks that it is not in main's lock); nothing else changes a locked hash. Versions
released before the lock existed are not in it.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
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
COMPONENTS: dict[str, Component] = {
    "docx-reader": Component(
        "zone_a.docx.reader", "READER_VERSION", ("src/zone_a/docx/reader.py",)
    ),
    "epi-reader": Component(
        "zone_a.epi.reader",
        "READER_VERSION",
        ("src/zone_a/epi/reader.py", "src/zone_a/fidelity/normalize.py"),
    ),
    "qrd-registry": Component(
        "zone_a.qrd.registry",
        "REGISTRY_VERSION",
        (
            "src/zone_a/qrd/registry.py",
            "src/zone_a/qrd/pattern.py",
            "src/zone_a/underline.py",
            "src/zone_a/fidelity/normalize.py",
        ),
    ),
    "qrd-check": Component(
        "zone_a.qrd.check",
        "CHECKER_VERSION",
        (
            "src/zone_a/qrd/check.py",
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
    if arguments.check:
        found = problems(lock)
        for problem in found:
            print(problem)
        return 1 if found else 0
    status = 0
    for name, component in COMPONENTS.items():
        version, sha256 = current_version(component), source_sha256(component)
        versions = lock.setdefault(name, {})
        if versions.get(version, sha256) != sha256 and not arguments.amend:
            print(f"{name} {version} is locked to other code; bump the version (or --amend)")
            status = 1
            continue
        versions[version] = sha256
    if status == 0:
        LOCK.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {LOCK.relative_to(ZONE_A)}")
    return status


if __name__ == "__main__":
    sys.exit(main())
