"""Find an SmPC's sections in its Word file, by the QRD template's own headings.

    uv run --frozen python scripts/structure_label.py LABEL.docx \
        [--assign KEY=PARAGRAPH ...] [--out FILE]

The label is read with zone_a.certified and structured with zone_a.structure against the QRD
registry (``qrd/registry/cap-smpc-en-10.4.json``) and the section mapping
(``fhir/mappings/cap-smpc-en.json``). ``--assign smpc.4.4=57`` names a section's heading where a
person has confirmed it. The result is written as canonical JSON to FILE, or to standard output,
with the file's SHA-256 and the reader's and format's versions; a file the reader refuses gives
its refusal instead. See docs/design/smpc-structure.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from label_docx import output, reader
from label_docx.reader import DocxRefusedError

from zone_a.canonical_json import canonical_json
from zone_a.certified import read_docx
from zone_a.structure import structure

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "qrd" / "registry" / "cap-smpc-en-10.4.json"
MAPPING = ROOT / "fhir" / "mappings" / "cap-smpc-en.json"


def _assignment(text: str) -> tuple[str, int]:
    key, sep, at = text.partition("=")
    if not sep or not at.isdigit():
        raise argparse.ArgumentTypeError(f"not KEY=PARAGRAPH: {text}")
    return key, int(at)


def main(argv: list[str] | None = None) -> int:
    """Writes the structure, or the reader's refusal.

    Returns:
        The exit status: 0 when written (a refusal included).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label", type=Path, help="the SmPC (.docx)")
    parser.add_argument("--assign", type=_assignment, action="append", default=[])
    parser.add_argument("--out", type=Path, help="write here instead of standard output")
    arguments = parser.parse_args(argv)
    data = arguments.label.read_bytes()
    result: dict[str, Any] = {
        "source": {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()},
        "reader": reader.READER_VERSION,
        "format": output.FORMAT_VERSION,
    }
    try:
        paragraphs = read_docx(data)
    except DocxRefusedError as refused:
        result["refusal"] = {"code": refused.code, "detail": refused.detail}
    else:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
        try:
            result |= structure(paragraphs, registry, mapping, dict(arguments.assign))
        except ValueError as problem:
            parser.error(str(problem))
    text = canonical_json(result) + "\n"
    if arguments.out is not None:
        arguments.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
