"""Write, or check, the QRD template registry for the centrally authorised SmPC in English.

    uv run --frozen python scripts/generate_qrd_registry.py          # write
    uv run --frozen python scripts/generate_qrd_registry.py --check  # fail on drift

The registry is built from the pinned EMA files in qrd/sources/ (see qrd/sources.lock.json and
docs/design/qrd-registry.md). tests/test_qrd_registry.py runs the same comparison as --check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zone_a.qrd.registry import build, serialise

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "qrd" / "sources"
LOCK = ROOT / "qrd" / "sources.lock.json"
REGISTRY = ROOT / "qrd" / "registry" / "cap-smpc-en-10.4.json"


def expected() -> str:
    """The registry file's content, built from the pinned sources."""
    return serialise(build(SOURCES, json.loads(LOCK.read_text(encoding="utf-8"))))


def main() -> int:
    """Writes the registry, or with ``--check`` compares it with the committed file.

    Returns:
        The exit status: 0 when the registry was written or is up to date, 1 when it is out of
        date.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the file is out of date")
    arguments = parser.parse_args()
    content = expected()
    if arguments.check:
        current = REGISTRY.read_text(encoding="utf-8") if REGISTRY.exists() else None
        if current != content:
            print(f"{REGISTRY.relative_to(ROOT)} is out of date; run the script without --check")
            return 1
        print(f"{REGISTRY.relative_to(ROOT)} is up to date")
        return 0
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(content, encoding="utf-8")
    print(f"wrote {REGISTRY.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
