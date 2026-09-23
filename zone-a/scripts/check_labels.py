"""Write, or check, the QRD conformance results for the pinned EMA ePI labels.

    uv run --frozen python scripts/check_labels.py          # write
    uv run --frozen python scripts/check_labels.py --check  # fail on drift

Each pinned file in labels/ema-epi/sources/ is read with zone_a.epi.reader and checked with
zone_a.qrd.check against the QRD registry and the SmPC mapping; the result is written to
labels/ema-epi/checks/<file>. See docs/design/qrd-conformance-check.md.
tests/test_qrd_check.py runs the same comparison as --check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zone_a.qrd.check import report

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "labels" / "ema-epi"
REGISTRY = ROOT / "qrd" / "registry" / "cap-smpc-en-10.4.json"
MAPPING = ROOT / "fhir" / "mappings" / "cap-smpc-en.json"


def expected() -> dict[Path, str]:
    lock = json.loads((LABELS / "sources.lock.json").read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    out: dict[Path, str] = {}
    for entry in lock["sources"]:
        data = (LABELS / "sources" / entry["file"]).read_bytes()
        out[LABELS / "checks" / entry["file"]] = report(entry["file"], data, registry, mapping)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if a result is out of date")
    arguments = parser.parse_args()
    results = expected()
    status = 0
    for path, content in results.items():
        name = path.relative_to(ROOT)
        if arguments.check:
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current != content:
                print(f"{name} is out of date; run the script without --check")
                status = 1
            else:
                print(f"{name} is up to date")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"wrote {name}")
    return status


if __name__ == "__main__":
    sys.exit(main())
