"""Write, or check, the recompute's results the TypeScript importer is held to (ADR 0006 P4, D1).

    uv run --frozen python scripts/certified_word_fixtures.py          # write
    uv run --frozen python scripts/certified_word_fixtures.py --check  # fail on drift

CI's Node job has no Python, so the importer (``src/certified-word/``) is tested on what
``python -m zone_a.recompute`` writes, committed here: for each synthetic Word label below, the
label itself as ``<name>.docx``, the very bytes the command writes on standard output for it
(canonical JSON and a line feed, or the refusal) as ``<name>.json``, and ``cases.json`` naming each
label's recompute request and what a person confirmed for it (the ePI's document id and the
canonical product, its name and holder chosen from the label's own text). The gate's tests read the
labels as the uploaded bytes (D4), and where a Python is at hand run the recompute on them (D2). A
change to the reader, the structurer or the builder changes these files;
``tests/test_certified_word_fixtures.py`` fails until this is run again, and the TypeScript tests
then read the new results.

Every label is synthetic, built here from XML, never from the EMA corpus or a company's label: its
names say so, its EU numbers are EU/1/24/9999's, and every section's text says it is not for
clinical use (test/synthetic-only.test.ts). The ids are in the reserved synthetic block
(``00000000-5979-4e74-8000-``), which marks a synthetic submission (ADR 0002 invariant 10).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import zipfile
from collections.abc import Callable
from html import escape
from pathlib import Path
from typing import Any

from zone_a import leaflet, recompute, structure

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "test" / "fixtures" / "certified-word" / "recompute"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CHANGE = 'w:author="Synthetic" w:date="2024-01-01T00:00:00Z"'
MARKED = "Synthetic text, not for clinical use."
BLOCK = "00000000-5979-4e74-8000-"
NAME = "Synthetic Exampline"
HOLDER = "Synthetic Holder B.V."
PRODUCT = {
    "id": f"{BLOCK}0000000000c2",
    "name": NAME,
    "holder": {"id": f"{BLOCK}0000000000c3", "name": HOLDER},
    "euAuthorisationNumbers": ["EU/1/24/9999/001", "EU/1/24/9999/002"],
}


def _run(text: str, *marks: str) -> str:
    """A run of text (a tab as Word's tab), with marks as run properties."""
    props = "".join(mark if mark.startswith("<") else f"<w:{mark}/>" for mark in marks)
    tab = '</w:t><w:tab/><w:t xml:space="preserve">'
    return (
        f"<w:r>{f'<w:rPr>{props}</w:rPr>' if props else ''}"
        f'<w:t xml:space="preserve">{escape(text, quote=False).replace(chr(9), tab)}</w:t></w:r>'
    )


def _p(*runs: str) -> str:
    """A paragraph of runs: plain text, or run XML as ``_run`` writes it."""
    return "<w:p>" + "".join(r if r.startswith("<") else _run(r) for r in runs) + "</w:p>"


def _table(rows: list[list[str]]) -> str:
    """A table of plain cells, its grid on record."""
    grid = "".join('<w:gridCol w:w="2000"/>' for _ in rows[0])
    cell = '<w:tc><w:tcPr><w:tcW w:w="2000" w:type="dxa"/></w:tcPr>{}</w:tc>'
    body = "".join(
        "<w:tr>" + "".join(cell.format(_p(text)) for text in row) + "</w:tr>" for row in rows
    )
    return f"<w:tbl><w:tblPr/><w:tblGrid>{grid}</w:tblGrid>{body}</w:tbl>"


def _docx(blocks: list[str]) -> bytes:
    """A minimal .docx of these body blocks, byte for byte the same on every run."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as package:
        for name, xml in (
            (
                "[Content_Types].xml",
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="xml" ContentType="application/xml"/></Types>',
            ),
            (
                "_rels/.rels",
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                'relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                "</Relationships>",
            ),
            (
                "word/_rels/document.xml.rels",
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                'relationships"/>',
            ),
            (
                "word/document.xml",
                f'<w:document xmlns:w="{W}"><w:body>{"".join(blocks)}</w:body></w:document>',
            ),
        ):
            package.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), xml)
    return out.getvalue()


def _smpc(change: Callable[[str, list[str]], list[str]] | None = None) -> list[str]:
    """An SmPC with every required section's heading and synthetic text.

    Section 1 names the product, 7 its holder and 8 its EU numbers; 4.1 has bold, italic and a
    superscript, 4.4 typed bullets, 6.1 a table. ``change(key, blocks)`` may rewrite a section's
    blocks, its heading first.
    """
    registry, mapping = recompute._load("smpc", ROOT)
    text: dict[str, list[str]] = {
        "smpc.1": [_p("Synthetic Exampline 10 mg film-coated tablets"), _p(MARKED)],
        "smpc.4.1": [
            _p(_run("Bold", "b"), " and ", _run("italic", "i"), " text, not for clinical use."),
            _p("Area 10 mm", _run("2", '<w:vertAlign w:val="superscript"/>'), ", synthetic."),
        ],
        "smpc.4.4": [_p("•\tFirst, not for clinical use."), _p("•\tSecond, synthetic.")],
        "smpc.6.1": [_p(MARKED), _table([["Core", "Lactose"], ["Coating", "Synthetic"]])],
        "smpc.7": [_p("Synthetic Holder B.V."), _p("1 Example Street"), _p(MARKED)],
        "smpc.8": [_p("EU/1/24/9999/001"), _p("EU/1/24/9999/002"), _p(MARKED)],
    }
    out = [_p("ANNEX I")]
    for node in structure._nodes(registry, mapping):
        if node["required"]:
            blocks = [_p(node["title"]), *text.get(node["key"], [_p(MARKED)])]
            out += change(node["key"], blocks) if change else blocks
    return out


def _tracked(key: str, blocks: list[str]) -> list[str]:
    """4.1 with a change tracked: 25 deleted, 30 inserted."""
    if key != "smpc.4.1":
        return blocks
    deleted = f'<w:del w:id="1" {CHANGE}><w:r><w:delText>25</w:delText></w:r></w:del>'
    inserted = f'<w:ins w:id="2" {CHANGE}>{_run("30")}</w:ins>'
    return [*blocks, _p("Store below ", deleted, inserted, " °C, not for clinical use.")]


def _assigned(key: str, blocks: list[str]) -> list[str]:
    """4.1 under a heading of the label's own, which a person assigns."""
    return [_p("4.1 Indications"), *blocks[1:]] if key == "smpc.4.1" else blocks


def _jump(key: str, blocks: list[str]) -> list[str]:
    """A tab in 4.2's text, which Word draws as a jump to a tab stop: the section is refused."""
    return [*blocks, _p("Dose\t1 tablet, not for clinical use.")] if key == "smpc.4.2" else blocks


def _leaflet() -> list[str]:
    """A leaflet with every required section's heading in the template's words.

    The product's name stands for X in every heading; section 6's holder section names the
    holder on its first line. A leaflet states no EU number.
    """
    registry, mapping = recompute._load("pl", ROOT)
    text = {"pl.6.holder": [_p(HOLDER), _p("1 Example Street"), _p(MARKED)]}
    out = [_p("B. PACKAGE LEAFLET"), _p("Package leaflet: Information for the patient"), _p(MARKED)]
    for node in leaflet._nodes(registry, mapping)[1:]:
        lines, prefix = leaflet.forms(node["head"])
        heading = prefix if prefix is not None else sorted(lines, key=len)[-1]
        out += [_p(heading.replace("X", NAME)), *text.get(node["key"], [_p(MARKED)])]
    return out


def _index(blocks: list[str], heading: str) -> int:
    """The body paragraph index of the paragraph that is exactly this heading (a cell's count)."""
    at = blocks.index(_p(heading))
    return sum(block.count("<w:p>") for block in blocks[:at])


def _cases() -> list[tuple[str, str, list[str], dict[str, Any]]]:
    """(name, what it shows, the label's blocks, the recompute request without versions)."""
    plain: dict[str, Any] = {"document": "smpc", "view": None, "part": 0, "assignments": {}}
    assigned = _smpc(_assigned)
    return [
        ("smpc", "an SmPC the importer carries", _smpc(), plain),
        (
            "smpc-tracked",
            "an SmPC with tracked changes, every one accepted",
            _smpc(_tracked),
            plain | {"view": "accepted"},
        ),
        (
            "smpc-assigned",
            "an SmPC whose 4.1 heading a person assigned: its title is carried as written",
            assigned,
            plain | {"assignments": {"smpc.4.1": _index(assigned, "4.1 Indications")}},
        ),
        ("smpc-refused", "an SmPC the recompute refuses: a tab in 4.2", _smpc(_jump), plain),
        ("pl", "a package leaflet the importer carries", _leaflet(), plain | {"document": "pl"}),
    ]


def _recompute(data: bytes, request: dict[str, Any]) -> str:
    """What ``python -m zone_a.recompute`` writes on standard output for these bytes."""
    with tempfile.TemporaryDirectory() as folder:
        label = Path(folder) / "label.docx"
        label.write_bytes(data)
        out = io.StringIO()
        stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(request))
        try:
            with contextlib.redirect_stdout(out):
                recompute.main([str(label)])
        finally:
            sys.stdin = stdin
    return out.getvalue()


def render() -> dict[str, bytes]:
    """Each file of ``TARGET`` by name, as this build writes it."""
    files: dict[str, bytes] = {}
    cases: list[dict[str, Any]] = []
    for position, (name, about, blocks, asked) in enumerate(_cases()):
        request = asked | {"versions": recompute.versions(asked["document"])}
        files[f"{name}.docx"] = _docx(blocks)
        files[f"{name}.json"] = _recompute(files[f"{name}.docx"], request).encode("utf-8")
        cases.append(
            {
                "name": name,
                "about": about,
                "request": request,
                "documentId": f"{BLOCK}{position + 0xD0:012x}",
                # A leaflet states no EU number, so a person confirms none for it.
                "product": PRODUCT | {"euAuthorisationNumbers": []}
                if asked["document"] == "pl"
                else PRODUCT,
            }
        )
    files["cases.json"] = (json.dumps(cases, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return files


def main() -> int:
    """Write the files, or with --check report whether they are current."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    files = render()
    if parser.parse_args().check:
        # Bytes, not text: a text read folds a carriage return into a line feed, and the TypeScript
        # tests read the bytes.
        current = {path.name: path.read_bytes() for path in TARGET.glob("*")}
        if current != files:
            sys.stderr.write(f"{TARGET} is out of date; run this script\n")
            return 1
        return 0
    TARGET.mkdir(parents=True, exist_ok=True)
    for stale in {path.name for path in TARGET.glob("*")} - set(files):
        (TARGET / stale).unlink()
    for name, content in files.items():
        (TARGET / name).write_bytes(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
