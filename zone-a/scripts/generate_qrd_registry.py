"""Write, or check, the QRD template registries: the centralised SmPC and leaflet in English.

    uv run --frozen python scripts/generate_qrd_registry.py          # write
    uv run --frozen python scripts/generate_qrd_registry.py --check  # fail on drift

The registries are built from the pinned EMA files in qrd/sources/ (see qrd/sources.lock.json and
docs/design/qrd-registry.md), the leaflet's with its mapping (fhir/mappings/cap-pl-en.json).
tests/test_qrd_registry.py runs the same comparison as --check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zone_a.qrd.registry import build, build_leaflet, serialise

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "qrd" / "sources"
LOCK = ROOT / "qrd" / "sources.lock.json"
REGISTRY = ROOT / "qrd" / "registry" / "cap-smpc-en-10.4.json"
LEAFLET = ROOT / "qrd" / "registry" / "cap-pl-en-10.4.json"
LEAFLET_MAPPING = ROOT / "fhir" / "mappings" / "cap-pl-en.json"


def expected() -> dict[Path, str]:
    """Each registry file's content, built from the pinned sources."""
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    mapping = json.loads(LEAFLET_MAPPING.read_text(encoding="utf-8"))
    return {
        REGISTRY: serialise(build(SOURCES, lock)),
        LEAFLET: serialise(build_leaflet(SOURCES, lock, mapping)),
    }


def main() -> int:
    """Writes the registry, or with ``--check`` compares it with the committed file.

    Returns:
        The exit status: 0 when the registries were written or are up to date, 1 when one is
        out of date.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the file is out of date")
    arguments = parser.parse_args()
    status = 0
    for path, content in expected().items():
        if arguments.check:
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current != content:
                print(f"{path.relative_to(ROOT)} is out of date; run the script without --check")
                status = 1
            else:
                print(f"{path.relative_to(ROOT)} is up to date")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return status


if __name__ == "__main__":
    sys.exit(main())
