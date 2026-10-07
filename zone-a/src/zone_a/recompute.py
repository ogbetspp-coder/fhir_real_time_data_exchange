"""Zone B's recompute of a certified Word source: the sections a submission carries, made again.

    python -m zone_a.recompute LABEL.docx < REQUEST.json > RESULT.json

ADR 0006 decision 1's second leg, P4 D2 (``docs/design/certified-word-import.md``): Zone B must
make, from the uploaded bytes and what a person confirmed, the very sections the submission
carries. This is the one function both sides run: the producer to make the sections, the gate to
make them again and compare. It is deterministic and reads nothing but the bytes and the committed
registry and mapping files: no network, no clock, no browser (the drawing is D3's, not this). The
files are read from this checkout, or from ``ZONE_A_ROOT`` where it is set: the worker image
installs the package, which then is in no checkout, and names its copy of the files there.

The request (JSON on standard input) names:

- ``document``: ``smpc`` or ``pl``;
- ``view``: for a label with tracked changes, the view a person named (``accepted`` or
  ``original``), else null;
- ``part``: which SmPC of an Annex I, or which leaflet, counted from 0 in the document's order
  (``zone_a.structure.smpcs``, ``zone_a.leaflet.leaflets``);
- ``assignments``: the headings a person named, section key to paragraph index;
- ``versions``: every version that decides the result, as ``versions()`` gives them; a request
  that names other versions is refused, so a submission is recomputed only by the build that made
  it.

The result is canonical JSON: the versions, the source's SHA-256 and length, the view and its
number of changes, the part's span, its structure and its sections (each with its key, parent,
code, title, heading, paragraphs, narrative and page). Or a refusal, ``{"refusal": {code,
detail}}``, with exit status 1: the reader refuses the bytes, the request does not fit them, the
structure is not ready, or a section is refused. An ePI carries every section its label has, so
one refused section refuses the whole.

``recompute_with_read`` gives the result with the read it was made from, for the drawing record
(``zone_a.drawing``, ``docs/design/certified-word-drawing.md``): its narratives are drawn and held
to that very read, so nothing reads the .docx twice. ``written`` is what the command writes for a
result, byte for byte, whose hash the record names.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from label_docx import output, reader
from label_docx.reader import DocxRefusedError

from zone_a import leaflet, structure, word_epi
from zone_a.canonical_json import canonical_json
from zone_a.certified import VIEWS, Body, read_body

RECOMPUTE_VERSION: Final = "recompute/1.2.0"

ROOT: Final = Path(__file__).resolve().parents[3]
# The registry and the mapping each document is found by.
FILES: Final = {
    "smpc": ("qrd/registry/cap-smpc-en-10.4.json", "fhir/mappings/cap-smpc-en.json"),
    "pl": ("qrd/registry/cap-pl-en-10.4.json", "fhir/mappings/cap-pl-en.json"),
}


class RefusedError(Exception):
    """The recompute cannot make the sections the request names."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _load(document: str, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    registry, mapping = FILES[document]
    return (
        json.loads((root / registry).read_text(encoding="utf-8")),
        json.loads((root / mapping).read_text(encoding="utf-8")),
    )


def versions(document: str, root: Path = ROOT) -> dict[str, str]:
    """Every version that decides a recompute of this document kind, in this build."""
    if document not in FILES:
        raise RefusedError("request", f"no document {document!r}")
    registry, mapping = _load(document, root)
    return {
        "recompute": RECOMPUTE_VERSION,
        "reader": reader.READER_VERSION,
        "format": output.FORMAT_VERSION,
        "structurer": (
            structure.STRUCTURE_VERSION if document == "smpc" else leaflet.LEAFLET_VERSION
        ),
        "registryVersion": str(registry["registryVersion"]),
        "mappingVersion": str(mapping["mappingVersion"]),
        "builder": word_epi.WORD_EPI_VERSION,
    }


def _request(request: object) -> tuple[str, str | None, int, dict[str, int], object]:
    expected = {"document", "view", "part", "assignments", "versions"}
    if not isinstance(request, Mapping) or set(request) != expected:
        raise RefusedError("request", f"the request holds exactly {sorted(expected)}")
    document, view, part, assignments = (
        request["document"],
        request["view"],
        request["part"],
        request["assignments"],
    )
    if document not in FILES:
        raise RefusedError("request", f"no document {document!r}")
    if view is not None and view not in VIEWS:
        raise RefusedError("request", f"no view {view!r}")
    if type(part) is not int or part < 0:
        raise RefusedError("request", "the part is a count from 0")
    if not isinstance(assignments, Mapping) or not all(
        isinstance(key, str) and type(at) is int for key, at in assignments.items()
    ):
        raise RefusedError("request", "assignments map a section key to a paragraph index")
    return document, view, part, dict(assignments), request["versions"]


def recompute(data: bytes, request: object, root: Path = ROOT) -> dict[str, Any]:
    """The sections the request names, made from the bytes (the module docstring).

    Raises:
        RefusedError: The sections cannot be made as the request names them.
    """
    return recompute_with_read(data, request, root)[0]


def recompute_with_read(
    data: bytes, request: object, root: Path = ROOT
) -> tuple[dict[str, Any], Body]:
    """``recompute``'s result, and the body the label reader certified that it was made from.

    Raises:
        RefusedError: The sections cannot be made as the request names them.
    """
    document, view, part, assignments, named = _request(request)
    built_by = versions(document, root)
    if named != built_by:
        raise RefusedError("versions", "the request names versions this build does not have")
    try:
        body = read_body(data, view)
    except DocxRefusedError as refused:
        raise RefusedError(refused.code, refused.detail) from refused
    except ValueError as wrong:
        raise RefusedError("request", str(wrong)) from wrong
    registry, mapping = _load(document, root)
    if document == "smpc":
        parts, why = structure.smpcs(body.paragraphs, registry, mapping)
    else:
        parts, why = leaflet.leaflets(body.paragraphs, registry)
    if why is not None:
        raise RefusedError("parts", why)
    if part >= len(parts):
        raise RefusedError("request", f"no part {part}: the document has {len(parts)}")
    start, stop, shared = parts[part]
    try:
        if document == "smpc":
            # One SmPC is structured whole, as Zone A shows it (zone_a.structure.smpcs).
            span = (start, stop, shared) if len(parts) > 1 else None
            structured = structure.structure(body.paragraphs, registry, mapping, assignments, span)
        else:
            structured = leaflet.structure(
                body.paragraphs, registry, mapping, assignments, (start, stop, shared)
            )
    except ValueError as wrong:
        raise RefusedError("assignments", str(wrong)) from wrong
    if not structured["ready"]:
        raise RefusedError(
            "structure", "the structure is not ready: a person must still confirm it"
        )
    try:
        built = word_epi.sections(body, structured, registry)
    except word_epi.RefusedError as refused:
        raise RefusedError(refused.code, refused.detail) from refused
    refused_keys = [s["key"] for s in built["sections"] if s["refusal"] is not None]
    if refused_keys:
        raise RefusedError("section", f"refused: {', '.join(refused_keys)}")
    result = {
        "versions": built_by,
        "source": {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)},
        "view": body.view,
        "changes": body.changes,
        "document": document,
        "part": part,
        "span": [start, stop],
        "structure": structured,
        "sections": built["sections"],
    }
    return result, body


def written(result: Mapping[str, Any]) -> str:
    """What the command writes for a result: its canonical JSON and a line feed."""
    return canonical_json(result) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Reads the label named and the request on standard input; writes the result.

    Returns:
        The exit status: 0 with the sections, 1 with a refusal.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        sys.stderr.write("usage: python -m zone_a.recompute LABEL.docx < REQUEST.json\n")
        return 2
    try:
        request = json.loads(sys.stdin.read())
        data = Path(args[0]).read_bytes()
    except json.JSONDecodeError:
        return _refuse("request", "not JSON")
    except OSError:
        return _refuse("request", "the label cannot be read")
    try:
        result = recompute(data, request, Path(os.environ.get("ZONE_A_ROOT", ROOT)))
    except RefusedError as refused:
        return _refuse(refused.code, refused.detail)
    sys.stdout.write(written(result))
    return 0


def _refuse(code: str, detail: str) -> int:
    sys.stdout.write(canonical_json({"refusal": {"code": code, "detail": detail}}) + "\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
