"""An SmPC's ePI sections from its Word file: each section's narrative and page, or why not.

    uv run --frozen python scripts/epi_from_word.py LABEL.docx \
        [--assign KEY=PARAGRAPH ...] [--no-drawing] [--out FILE]

The label is read with zone_a.certified, structured with zone_a.structure (``--assign`` as in
scripts/structure_label.py) and built with zone_a.word_epi; where Chrome is installed and
``--no-drawing`` is not given, each narrative is then held to what Chrome draws
(zone_a.drawing), and ``drawing`` is null otherwise. The result is written as canonical JSON to
FILE, or to standard output, with the file's SHA-256 and every version that decided it. A file
the reader refuses, or one with a floating object, gives its refusal; a structure a person must
still confirm gives the structure alone (``ready`` false). See ADR 0006.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from label_docx import browser, output, reader
from label_docx.reader import DocxRefusedError

from zone_a import drawing, word_epi
from zone_a.canonical_json import canonical_json
from zone_a.certified import read_body
from zone_a.structure import structure

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "qrd" / "registry" / "cap-smpc-en-10.4.json"
MAPPING = ROOT / "fhir" / "mappings" / "cap-smpc-en.json"


def _assignment(text: str) -> tuple[str, int]:
    key, sep, at = text.partition("=")
    if not sep or not at.isdigit():
        raise argparse.ArgumentTypeError(f"not KEY=PARAGRAPH: {text}")
    return key, int(at)


def build(data: bytes, assignments: dict[str, int], chrome: Path | None) -> dict[str, Any]:
    """The result for one label (the module docstring)."""
    result: dict[str, Any] = {
        "source": {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()},
        "reader": reader.READER_VERSION,
        "format": output.FORMAT_VERSION,
    }
    try:
        body = read_body(data)
    except DocxRefusedError as refused:
        return result | {"refusal": {"code": refused.code, "detail": refused.detail}}
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    structured = structure(body.paragraphs, registry, mapping, assignments)
    result["structure"] = structured
    if not structured["ready"]:
        return result
    try:
        built = word_epi.sections(body, structured, registry)
    except word_epi.RefusedError as refused:
        return result | {"refusal": {"code": refused.code, "detail": refused.detail}}
    result["epi"] = built
    result["drawing"] = None if chrome is None else drawing.check(body, built, chrome)
    return result


def main(argv: list[str] | None = None) -> int:
    """Writes the result.

    Returns:
        The exit status: 0 when written (a refusal included).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label", type=Path, help="the SmPC (.docx)")
    parser.add_argument("--assign", type=_assignment, action="append", default=[])
    parser.add_argument("--no-drawing", action="store_true", help="do not ask Chrome")
    parser.add_argument("--out", type=Path, help="write here instead of standard output")
    arguments = parser.parse_args(argv)
    chrome = None if arguments.no_drawing else browser.find_chrome()
    try:
        result = build(arguments.label.read_bytes(), dict(arguments.assign), chrome)
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
