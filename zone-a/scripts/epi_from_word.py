"""An SmPC's ePI sections from its Word file: each section's narrative and page, or why not.

    uv run --frozen python scripts/epi_from_word.py LABEL.docx \
        [--view accepted|original] [--assign KEY=PARAGRAPH ...] [--no-drawing] [--document smpc|pl]
        [--out FILE]

The label is read with zone_a.certified, structured with zone_a.structure (``--assign`` as in
scripts/structure_label.py) and built with zone_a.word_epi; where Chrome is installed and
``--no-drawing`` is not given, each narrative is then held to what Chrome draws
(zone_a.drawing); a section Chrome draws otherwise, or that it did not draw (no Chrome, or
``--no-drawing``), is refused. The result is written as canonical JSON to
FILE, or to standard output, with the file's SHA-256 and every version that decided it. A file
the reader refuses, or one with something Word draws the read does not yet say (``Body.layout``),
gives its refusal; a section anchoring a floating object is refused alone; a structure a person must
still confirm gives the structure alone (``ready`` false), with ``product``, what the label says
it is for (zone_a.product). A label with tracked changes is built only from the view a person
names with ``--view`` (every change accepted, or every one rejected), which the result records
with the number of changes (``tracked``); without one it is refused. An Annex I holding several
SmPCs gives each its own result, in ``smpcs`` (its ``span`` of paragraphs, its structure, product
and sections; ``--assign`` goes to the SmPC holding the paragraph), or the reason a person must
settle where one ends (zone_a.structure.smpcs). With ``--document pl`` the label is read for
its package leaflets instead (zone_a.leaflet): each gives its own result in ``leaflets`` (its
``span``, structure and sections; no product proposal yet), or the reason a person must find
them. See ADR 0006.
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

from zone_a import drawing, leaflet, product, word_epi
from zone_a.canonical_json import canonical_json
from zone_a.certified import VIEWS, Body, read_body
from zone_a.structure import smpcs, structure

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "qrd" / "registry" / "cap-smpc-en-10.4.json"
MAPPING = ROOT / "fhir" / "mappings" / "cap-smpc-en.json"
LEAFLET_REGISTRY = ROOT / "qrd" / "registry" / "cap-pl-en-10.4.json"
LEAFLET_MAPPING = ROOT / "fhir" / "mappings" / "cap-pl-en.json"


def _assignment(text: str) -> tuple[str, int]:
    key, sep, at = text.partition("=")
    if not sep or not at.isdigit():
        raise argparse.ArgumentTypeError(f"not KEY=PARAGRAPH: {text}")
    return key, int(at)


def _placed(assignments: dict[str, int], parts: list[tuple[int, int, int | None]]) -> None:
    """Refuses an assignment to a paragraph in no part: it would be dropped, not refused."""
    for key, at in assignments.items():
        if not any(start <= at < end for start, end, _ in parts):
            raise ValueError(f"{key}={at}: paragraph {at} is in no part the document holds")


def build(
    data: bytes,
    assignments: dict[str, int],
    chrome: Path | None,
    view: str | None = None,
    document: str = "smpc",
) -> dict[str, Any]:
    """The result for one label (the module docstring)."""
    result: dict[str, Any] = {
        "source": {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()},
        "reader": reader.READER_VERSION,
        "format": output.FORMAT_VERSION,
    }
    try:
        body = read_body(data, view)
    except DocxRefusedError as refused:
        return result | {"refusal": {"code": refused.code, "detail": refused.detail}}
    if body.view is not None:
        result["tracked"] = {"view": body.view, "changes": body.changes}
    if document == "pl":
        registry = json.loads(LEAFLET_REGISTRY.read_text(encoding="utf-8"))
        mapping = json.loads(LEAFLET_MAPPING.read_text(encoding="utf-8"))
        found, reason = leaflet.leaflets(body.paragraphs, registry)
        if reason is not None:
            return result | {"leaflets": {"ready": False, "reason": reason}}
        _placed(assignments, found)
        result["leaflets"] = [
            {"span": [start, end]}
            | _sections(
                body,
                leaflet.structure(
                    body.paragraphs,
                    registry,
                    mapping,
                    {key: at for key, at in assignments.items() if start <= at < end},
                    (start, end, shared),
                ),
                registry,
                chrome,
            )
            for start, end, shared in found
        ]
        return result
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    parts, why = smpcs(body.paragraphs, registry, mapping)
    if why is not None:
        # Several SmPCs whose boundary the template's own lines do not settle: for a person.
        return result | {"smpcs": {"ready": False, "reason": why}}
    _placed(assignments, parts)
    if len(parts) == 1:
        return result | _smpc(body, registry, mapping, assignments, None, chrome)
    result["smpcs"] = [
        {"span": [start, end]}
        | _smpc(
            body,
            registry,
            mapping,
            {key: at for key, at in assignments.items() if start <= at < end},
            (start, end, shared),
            chrome,
        )
        for start, end, shared in parts
    ]
    return result


def _smpc(
    body: Body,
    registry: dict[str, Any],
    mapping: dict[str, Any],
    assignments: dict[str, int],
    part: tuple[int, int, int | None] | None,
    chrome: Path | None,
) -> dict[str, Any]:
    """One SmPC's structure, who it is for, and its ePI sections once the structure is ready."""
    structured = structure(body.paragraphs, registry, mapping, assignments, part)
    # Who it is for, from its sections 1, 7 and 8, for a person to confirm (zone_a.product).
    out = {"product": product.propose(body.paragraphs, structured)}
    return out | _sections(body, structured, registry, chrome)


def _sections(
    body: Body, structured: dict[str, Any], registry: dict[str, Any], chrome: Path | None
) -> dict[str, Any]:
    """A structure, and its ePI sections once it is ready."""
    out: dict[str, Any] = {"structure": structured}
    if not structured["ready"]:
        return out
    try:
        built = word_epi.sections(body, structured, registry)
    except word_epi.RefusedError as refused:
        return out | {"refusal": {"code": refused.code, "detail": refused.detail}}
    verdict = None if chrome is None else drawing.check(body, built, chrome)
    out["epi"] = drawing.refuse(built, verdict)
    out["drawing"] = verdict
    return out


def main(argv: list[str] | None = None) -> int:
    """Writes the result.

    Returns:
        The exit status: 0 when written (a refusal included).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label", type=Path, help="the label (.docx)")
    parser.add_argument(
        "--view", choices=VIEWS, help="for a label with tracked changes, the view to build"
    )
    parser.add_argument("--assign", type=_assignment, action="append", default=[])
    parser.add_argument("--no-drawing", action="store_true", help="do not ask Chrome")
    parser.add_argument("--out", type=Path, help="write here instead of standard output")
    parser.add_argument(
        "--document", choices=("smpc", "pl"), default="smpc", help="the part to build"
    )
    arguments = parser.parse_args(argv)
    chrome = None if arguments.no_drawing else browser.find_chrome()
    try:
        result = build(
            arguments.label.read_bytes(),
            dict(arguments.assign),
            chrome,
            arguments.view,
            arguments.document,
        )
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
