"""How a set of Word SmPCs fares, label by label: read, structured, carried, drawn, and agreeing.

    uv run --frozen python scripts/scoreboard.py FOLDER [--keys KEYS] [--no-drawing]
        [--view accepted|original] [--document smpc|pl] [--out FILE]

Each .docx in FOLDER is read with zone_a.certified, structured with zone_a.structure and built
with zone_a.word_epi; where Chrome is installed and ``--no-drawing`` is not given, each carried
narrative is drawn (zone_a.drawing). With ``--keys``, a folder of answer keys (an EMA ePI
Bundle named as the .docx, ``NAME.json``), each carried section is compared with the key's
section of the same EMA code, as the label reader reads it, line by line (list labels and text,
spaces collapsed); a key's sub-sections the template has no place for are read into their
parent. An Annex I holding several SmPCs (``zone_a.structure.smpcs``) is counted over them all
(``smpcs``), each structured and built on its own; it has no answer key, and one whose boundary
a person must settle needs a person (``smpc-boundary``). With ``--document pl`` each file is
read for its package leaflets instead (``zone_a.leaflet``), each structured and built on its own
and counted together (``leaflets``), with no answer key; a file whose leaflet root line is not
there once needs a person (``pl-root``).

A document with tracked changes is refused unless a view is named; with ``--view`` it is
measured by that view (``zone_a.certified.read_body``), as a person may import it (ADR 0006),
and the entry says so (``view``).

Nothing a label says is printed or written: only codes, counts, keys and line numbers, so it can
be run on documents that must not leave their environment. The result is JSON: one entry per
file and the totals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from label_docx import browser
from label_docx.epi_output import read as read_epi
from label_docx.reader import DocxRefusedError

from zone_a import drawing, leaflet, word_epi
from zone_a.canonical_json import canonical_json
from zone_a.certified import read_body
from zone_a.structure import smpcs, structure

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "qrd" / "registry" / "cap-smpc-en-10.4.json"
MAPPING = ROOT / "fhir" / "mappings" / "cap-smpc-en.json"
LEAFLET_REGISTRY = ROOT / "qrd" / "registry" / "cap-pl-en-10.4.json"
LEAFLET_MAPPING = ROOT / "fhir" / "mappings" / "cap-pl-en.json"
_SPACE = re.compile("[ \t\n\u00a0]+")


def _line(label: str | None, text: str) -> str:
    return _SPACE.sub(" ", (f"{label} " if label else "") + text).strip()


def _key_lines(key: Mapping[str, Any], codes: set[str]) -> dict[str, list[str] | None]:
    """Each key section's lines by EMA code; None for a section the reader refused.

    A sub-section with no code of ours is read into its parent, with its title.
    """
    out: dict[str, list[str] | None] = {}

    def lines(section: Mapping[str, Any], inner: bool) -> list[str] | None:
        if section["refusal"] is not None:
            return None
        got = [_line(None, section["title"])] if inner else []
        for paragraph in section["paragraphs"]:
            numbering = paragraph["numbering"]
            got.append(_line(numbering["text"] if numbering else None, paragraph["text"]))
        for child in section["sections"]:
            if child["code"] not in codes:
                more = lines(child, True)
                if more is None:
                    return None
                got += more
        return [x for x in got if x]

    def visit(section: Mapping[str, Any]) -> None:
        out[section["code"]] = lines(section, False)
        for child in section["sections"]:
            visit(child)

    for section in key["sections"]:
        visit(section)
    return out


def score(
    path: Path,
    keys: Path | None,
    chrome: Path | None,
    view: str | None = None,
    document: str = "smpc",
) -> dict[str, Any]:
    """One label's entry (the module docstring)."""
    data = path.read_bytes()
    entry: dict[str, Any] = {"file": path.name, "sha256": hashlib.sha256(data).hexdigest()}
    try:
        body = read_body(data)
    except DocxRefusedError as refused:
        if refused.code != "tracked-change" or view is None:
            return entry | {"outcome": "reader-refused", "code": refused.code}
        try:
            body = read_body(data, view)
        except DocxRefusedError as again:
            return entry | {"outcome": "reader-refused", "code": again.code, "view": view}
        entry["view"] = view
    if document == "pl":
        registry = json.loads(LEAFLET_REGISTRY.read_text(encoding="utf-8"))
        mapping = json.loads(LEAFLET_MAPPING.read_text(encoding="utf-8"))
        parts, why = leaflet.leaflets(body.paragraphs, registry)
        if why is not None:
            return entry | {"outcome": "needs-a-person", "sections": ["pl-root"]}
        several = len(parts) > 1
        structures = [
            leaflet.structure(body.paragraphs, registry, mapping, None, part) for part in parts
        ]
    else:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
        parts, why = smpcs(body.paragraphs, registry, mapping)
        if why is not None:
            return entry | {"outcome": "needs-a-person", "sections": ["smpc-boundary"]}
        # One SmPC as before; several, each structured and built on its own, counted together.
        several = len(parts) > 1
        structures = [
            structure(body.paragraphs, registry, mapping, None, part if several else None)
            for part in parts
        ]
    summary = sum((Counter(s["summary"]) for s in structures), Counter[str]())
    entry["structure"] = {k: v for k, v in summary.items() if v}
    if several:
        entry["smpcs" if document == "smpc" else "leaflets"] = len(parts)
    if not all(s["ready"] for s in structures):
        needs = [
            s["key"]
            for structured in structures
            for s in structured["sections"]
            if s["status"] in ("missing", "duplicate", "order", "no-code")
        ]
        return entry | {"outcome": "needs-a-person", "sections": needs}
    built: dict[str, Any] = {"sections": []}
    for structured in structures:
        try:
            one = word_epi.sections(body, structured, registry)
        except word_epi.RefusedError as refused:
            return entry | {"outcome": "document-refused", "code": refused.code}
        if chrome is not None:
            one = drawing.refuse(one, drawing.check(body, one, chrome))
        built["sections"] += one["sections"]
    carried = [s for s in built["sections"] if s["refusal"] is None]
    entry["outcome"] = "built"
    entry["sections"] = len(built["sections"])
    entry["carried"] = len(carried)
    entry["refused"] = dict(
        Counter(s["refusal"]["code"] for s in built["sections"] if s["refusal"])
    )
    entry["drawn"] = chrome is not None
    key_path = None if keys is None or several else keys / f"{path.stem}.json"
    if key_path is not None and key_path.exists():
        key = json.loads(read_epi(key_path.read_bytes())[0])
        theirs = _key_lines(key, {s["code"] for s in built["sections"]})
        agree = Counter[str]()
        differ: list[dict[str, Any]] = []
        for section in carried:
            start, stop = section["paragraphs"]
            mine = [
                _line(p.numbering.text if p.numbering else None, p.text)
                for p in body.paragraphs[start:stop]
                if not word_epi.blank(p)
            ]
            mine = [x for x in mine if x]
            key_section = theirs.get(section["code"], "missing")
            if key_section == "missing":
                agree["no key section"] += 1
            elif key_section is None:
                agree["key section refused"] += 1
            elif key_section == mine:
                agree["same"] += 1
            else:
                agree["differs"] += 1
                at = next(
                    (i for i, (a, b) in enumerate(zip(key_section, mine, strict=False)) if a != b),
                    min(len(key_section), len(mine)),
                )
                differ.append({"key": section["key"], "line": at})
        entry["key"] = {"result": dict(agree), "differs": differ}
    return entry


def main(argv: list[str] | None = None) -> int:
    """Writes the scoreboard.

    Returns:
        The exit status: 0 when written.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--keys", type=Path, help="answer keys: EMA ePI Bundles named as the files")
    parser.add_argument("--no-drawing", action="store_true", help="do not ask Chrome")
    parser.add_argument(
        "--view", choices=("accepted", "original"), help="measure a tracked document by this view"
    )
    parser.add_argument("--out", type=Path, help="write here instead of standard output")
    parser.add_argument(
        "--document", choices=("smpc", "pl"), default="smpc", help="the part each file is read for"
    )
    arguments = parser.parse_args(argv)
    chrome = None if arguments.no_drawing else browser.find_chrome()
    entries = [
        score(p, arguments.keys, chrome, arguments.view, arguments.document)
        for p in sorted(arguments.folder.glob("*.docx"))
    ]
    built = [e for e in entries if e["outcome"] == "built"]
    totals: dict[str, Any] = {
        "files": len(entries),
        "outcomes": dict(Counter(e["outcome"] for e in entries)),
        "sections": sum(e["sections"] for e in built),
        "carried": sum(e["carried"] for e in built),
        "refused": dict(sum((Counter(e["refused"]) for e in built), Counter[str]())),
        "readerRefusals": dict(
            Counter(e["code"] for e in entries if e["outcome"] == "reader-refused")
        ),
        "key": dict(
            sum((Counter(e["key"]["result"]) for e in built if "key" in e), Counter[str]())
        ),
    }
    text = (
        canonical_json({"builder": word_epi.WORD_EPI_VERSION, "totals": totals, "files": entries})
        + "\n"
    )
    if arguments.out is not None:
        arguments.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
