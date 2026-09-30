"""Write, or check, the synthetic list-numbering corpus (corpus/numbering-cases).

    uv run --frozen python scripts/numbering_cases.py           # write the .docx files
    uv run --frozen python scripts/numbering_cases.py --check   # fail if they would change

Each case is a small, complete .docx that Word opens, built to put one rule of list numbering to
Word: how lists that share a definition count, what a start override restarts, what a level
never counted shows. ``scripts/word_oracle.py record`` then asks Word for the label it draws for
each numbered paragraph, and ``tests/test_word_oracle.py`` holds the reader to those answers. The
cases include the ones the reader refuses as ambiguous, so Word's answer is on record for them.

The files are stored, not deflated, with fixed timestamps, so the same cases give the same bytes
on any machine.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "corpus" / "numbering-cases"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml"


class Case(NamedTuple):
    """One .docx: what it puts to Word, its numbering part, its body and its styles."""

    question: str
    numbering: str
    body: str
    styles: str = ""


def lvl(
    level: int,
    fmt: str = "decimal",
    text: str | None = None,
    extra: str = "",
    start: int | None = 1,
) -> str:
    """A list level; its text is ``%n.`` for its own counter unless given."""
    shown = f"%{level + 1}." if text is None else text
    first = "" if start is None else f'<w:start w:val="{start}"/>'
    return (
        f'<w:lvl w:ilvl="{level}">{first}<w:numFmt w:val="{fmt}"/>'
        f'<w:lvlText w:val="{shown}"/>{extra}</w:lvl>'
    )


def abstract(key: int, *levels: str) -> str:
    """An abstractNum."""
    return f'<w:abstractNum w:abstractNumId="{key}">{"".join(levels)}</w:abstractNum>'


def num(key: int, abstract_id: int, overrides: str = "") -> str:
    """A num naming an abstractNum."""
    return f'<w:num w:numId="{key}"><w:abstractNumId w:val="{abstract_id}"/>{overrides}</w:num>'


def start_at(level: int, value: int) -> str:
    """A lvlOverride with a startOverride."""
    return f'<w:lvlOverride w:ilvl="{level}"><w:startOverride w:val="{value}"/></w:lvlOverride>'


def items(*entries: tuple[int, int] | tuple[int, int, str]) -> str:
    """Numbered paragraphs, each ``(numId, ilvl)`` or ``(numId, ilvl, extra pPr)``."""
    out: list[str] = []
    for index, entry in enumerate(entries, 1):
        props = entry[2] if len(entry) == 3 else ""
        numbered = f'<w:numPr><w:ilvl w:val="{entry[1]}"/><w:numId w:val="{entry[0]}"/></w:numPr>'
        out.append(f"<w:p><w:pPr>{props}{numbered}</w:pPr><w:r><w:t>item {index}</w:t></w:r></w:p>")
    return "".join(out)


def styled(style: str) -> str:
    """A paragraph in ``style`` that sets no list of its own."""
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t>styled</w:t></w:r></w:p>'


SECTIONS = abstract(1, lvl(0, text="%1."), lvl(1, text="%1.%2"), lvl(2, text="%1.%2.%3"))
SHARED = (
    abstract(7, lvl(0))
    + num(10, 7)
    + num(11, 7)
    + num(12, 7, start_at(0, 1))
    + num(13, 7, start_at(0, 5))
)
FORMATS = [
    ("decimal", 0),
    ("decimalZero", 7),
    ("decimalZero", 12),
    ("upperRoman", 1994),
    ("lowerRoman", 4),
    ("upperLetter", 1),
    ("lowerLetter", 26),
    ("lowerLetter", 27),
    ("lowerLetter", 53),
    ("none", 5),
]
SYMBOL = '<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol" w:hint="default"/></w:rPr>'
COURIER = '<w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/></w:rPr>'
LEGACY = (
    '<w:lvlOverride w:ilvl="0"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
    '<w:lvlText w:val="-"/><w:legacy w:legacy="1" w:legacySpace="0" w:legacyIndent="360"/>'
    "</w:lvl></w:lvlOverride>"
)
HEADING = (
    '<w:style w:type="paragraph" w:styleId="H2"><w:name w:val="H2"/><w:pPr><w:numPr>'
    '<w:numId w:val="1"/></w:numPr></w:pPr></w:style>'
)
OUTLINE = (
    '<w:style w:type="numbering" w:styleId="Outline"><w:name w:val="Outline"/><w:pPr><w:numPr>'
    '<w:numId w:val="31"/></w:numPr></w:pPr></w:style>'
)

CASES: dict[str, Case] = {
    "multilevel": Case(
        "Levels count and restart the deeper levels.",
        SECTIONS + num(1, 1),
        items((1, 0), (1, 1), (1, 1), (1, 0), (1, 1), (1, 2), (1, 1)),
    ),
    "formats": Case(
        "Each number format at the edges of its range.",
        # The schema puts every abstractNum before every num.
        "".join(abstract(20 + i, lvl(0, fmt, start=v)) for i, (fmt, v) in enumerate(FORMATS))
        + "".join(num(20 + i, 20 + i) for i in range(len(FORMATS))),
        items(*((20 + i, 0) for i in range(len(FORMATS)))),
    ),
    "legal": Case(
        "isLgl shows every level in decimal.",
        abstract(1, lvl(0, "upperRoman", "%1"), lvl(1, text="%1.%2", extra="<w:isLgl/>"))
        + num(1, 1),
        items((1, 0), (1, 1)),
    ),
    "restart-never": Case(
        "lvlRestart 0: the level never restarts.",
        abstract(1, lvl(0, text="%1"), lvl(1, text="%2", extra='<w:lvlRestart w:val="0"/>'))
        + num(1, 1),
        items((1, 0), (1, 1), (1, 1), (1, 0), (1, 1)),
    ),
    "restart-after-first": Case(
        "lvlRestart 1 on level 2: it restarts after level 0 only.",
        abstract(
            1,
            lvl(0, text="%1"),
            lvl(1, text="%2"),
            lvl(2, text="%3", extra='<w:lvlRestart w:val="1"/>'),
        )
        + num(1, 1),
        items((1, 0), (1, 2), (1, 1), (1, 2), (1, 0), (1, 2)),
    ),
    "two-definitions": Case(
        "Lists of different abstractNums count apart.",
        abstract(1, lvl(0)) + abstract(2, lvl(0)) + num(1, 1) + num(2, 2),
        items((1, 0), (2, 0), (1, 0)),
    ),
    "shared-continue": Case(
        "Two lists of one abstractNum that override nothing continue each other.",
        SHARED,
        items((10, 0), (11, 0), (10, 0)),
    ),
    "start-override-restart": Case(
        "A new list with a startOverride restarts the shared count.",
        SHARED,
        items((10, 0), (10, 0), (12, 0), (12, 0)),
    ),
    "start-override-first": Case(
        "A startOverride on the first list sets its start.", SHARED, items((13, 0), (13, 0))
    ),
    "return-after-restart": Case(
        "Back to the first list after a second restarted the count (the reader refuses).",
        SHARED,
        items((10, 0), (12, 0), (10, 0)),
    ),
    "plain-after-restart": Case(
        "A list that overrides nothing after one that restarted (the reader refuses).",
        SHARED,
        items((12, 0), (11, 0)),
    ),
    "override-deeper-level": Case(
        "A new list restarts level 1 at 5 under a level 0 counted by another list.",
        SECTIONS + num(1, 1) + num(2, 1, start_at(1, 5)),
        items((1, 0), (1, 1), (2, 1), (2, 1)),
    ),
    "ancestor-never-counted": Case(
        "A level shown before its parent level was counted (the reader refuses).",
        SECTIONS + num(1, 1),
        items((1, 1), (1, 1), (1, 0), (1, 1)),
    ),
    "missing-start": Case(
        "A decimal level with no w:start (the reader refuses).",
        abstract(1, lvl(0, start=None)) + num(1, 1),
        items((1, 0), (1, 0)),
    ),
    "style-tied-deeper-level": Case(
        "A style names numId 1 but no level; level 1 names the style (the reader refuses).",
        abstract(1, lvl(0), lvl(1, text="%1.%2", extra='<w:pStyle w:val="H2"/>')) + num(1, 1),
        items((1, 0)) + styled("H2") + styled("H2"),
        HEADING,
    ),
    "style-tied-level-zero": Case(
        "A style names numId 1 but no level; level 0 names the style.",
        abstract(1, lvl(0, extra='<w:pStyle w:val="H2"/>'), lvl(1, text="%1.%2")) + num(1, 1),
        styled("H2") + styled("H2"),
        HEADING,
    ),
    "numbering-style-link": Case(
        "A list linked through a numbering style shares the linked list's count.",
        '<w:abstractNum w:abstractNumId="30"><w:styleLink w:val="Outline"/>'
        + lvl(0, text="Section %1")
        + "</w:abstractNum>"
        + '<w:abstractNum w:abstractNumId="32"><w:numStyleLink w:val="Outline"/></w:abstractNum>'
        + num(31, 30)
        + num(33, 32),
        items((33, 0), (31, 0), (33, 0)),
        OUTLINE,
    ),
    "numbering-style-link-one-way": Case(
        "A numStyleLink whose target abstractNum has no styleLink back.",
        abstract(30, lvl(0, text="Section %1"))
        + '<w:abstractNum w:abstractNumId="32"><w:numStyleLink w:val="Outline"/></w:abstractNum>'
        + num(31, 30)
        + num(33, 32),
        items((33, 0), (31, 0), (33, 0)),
        OUTLINE,
    ),
    "override-return": Case(
        "Back to a list with a startOverride after another list: does it restart again?",
        SHARED,
        items((12, 0), (10, 0), (12, 0), (10, 0)),
    ),
    "override-restart-within": Case(
        "A list that restarts level 1 at 5, then restarts level 1 itself after its level 0.",
        SECTIONS + num(1, 1) + num(2, 1, start_at(1, 5)),
        items((1, 0), (2, 1), (2, 0), (2, 1)),
    ),
    "override-ancestor": Case(
        "A new list with a startOverride on level 0, first used at level 1.",
        SECTIONS + num(1, 1) + num(2, 1, start_at(0, 5)),
        items((1, 0), (1, 1), (2, 1), (2, 0)),
    ),
    "level-override-shared": Case(
        "A list that redefines level 0 (w:lvl, start 1) after a list of the same abstractNum.",
        abstract(40, lvl(0))
        + num(40, 40)
        + num(41, 40, '<w:lvlOverride w:ilvl="0">' + lvl(0, text="%1)") + "</w:lvlOverride>"),
        items((40, 0), (40, 0), (41, 0), (40, 0)),
    ),
    "level-override-start": Case(
        "A list that redefines level 0 with start 7, after a list of the same abstractNum.",
        abstract(40, lvl(0))
        + num(40, 40)
        + num(
            42, 40, '<w:lvlOverride w:ilvl="0">' + lvl(0, text="%1)", start=7) + "</w:lvlOverride>"
        ),
        items((40, 0), (42, 0), (42, 0), (40, 0)),
    ),
    "level-override-first": Case(
        "A list that redefines level 0 with start 7, as the first list of its abstractNum.",
        abstract(40, lvl(0))
        + num(40, 40)
        + num(
            42, 40, '<w:lvlOverride w:ilvl="0">' + lvl(0, text="%1)", start=7) + "</w:lvlOverride>"
        ),
        items((42, 0), (42, 0), (40, 0)),
    ),
    "override-implicit-ancestor": Case(
        "A startOverride on level 0 of a list first used at level 1, nothing counted before.",
        SECTIONS + num(1, 1) + num(2, 1, start_at(0, 5)),
        items((2, 1), (2, 0), (1, 0)),
    ),
    "ancestor-two-levels": Case(
        "A level 2 item before levels 0 and 1 were counted, then levels 1 and 0.",
        SECTIONS + num(1, 1),
        items((1, 2), (1, 1), (1, 2), (1, 0), (1, 2)),
    ),
    "level-override": Case(
        "A level override replaces the level whole (the EMA template's Word 6 dash).",
        abstract(1, lvl(0)) + num(1, 1, LEGACY),
        items((1, 0), (1, 0)),
    ),
    "bullets": Case(
        "Bullets in Symbol, in Courier New, and in the paragraph mark's font.",
        abstract(1, lvl(0, "bullet", "", SYMBOL))
        + abstract(2, lvl(0, "bullet", "o", COURIER))
        + abstract(3, lvl(0, "bullet", ""))
        + num(1, 1)
        + num(2, 2)
        + num(3, 3),
        items(
            (1, 0), (2, 0), (3, 0, '<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr>')
        ),
    ),
}


def package(case: Case) -> bytes:
    """``case`` as a complete .docx, stored, with fixed timestamps."""
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/word/document.xml" ContentType="{MAIN}.document.main+xml"/>'
            f'<Override PartName="/word/styles.xml" ContentType="{MAIN}.styles+xml"/>'
            f'<Override PartName="/word/numbering.xml" ContentType="{MAIN}.numbering+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{RELS}">'
            f'<Relationship Id="rId1" Type="{OFFICE}/officeDocument" Target="word/document.xml"/>'
            "</Relationships>"
        ),
        "word/_rels/document.xml.rels": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{RELS}">'
            f'<Relationship Id="rId1" Type="{OFFICE}/styles" Target="styles.xml"/>'
            f'<Relationship Id="rId2" Type="{OFFICE}/numbering" Target="numbering.xml"/>'
            "</Relationships>"
        ),
        "word/document.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W}">'
            f"<w:body>{case.body}<w:sectPr/></w:body></w:document>"
        ),
        "word/styles.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{W}">'
            f"{case.styles}</w:styles>"
        ),
        "word/numbering.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering xmlns:w="{W}">'
            f"{case.numbering}</w:numbering>"
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, content in parts.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            archive.writestr(info, content.encode("utf-8"))
    return buffer.getvalue()


def wanted() -> dict[Path, bytes]:
    """Every file of the corpus set, with its bytes."""
    out: dict[Path, bytes] = {}
    sources = []
    for name, case in CASES.items():
        data = package(case)
        out[FOLDER / f"{name}.docx"] = data
        sources.append(
            {
                "name": case.question,
                "file": f"{name}.docx",
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    manifest = {
        "schemaVersion": "1.0.0",
        "note": "Synthetic list-numbering cases written by scripts/numbering_cases.py.",
        "sources": sources,
    }
    out[FOLDER / "sources.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    return out


def main() -> int:
    """Write the cases, or with --check report whether they are current."""
    parser = argparse.ArgumentParser(description="Write or check corpus/numbering-cases.")
    parser.add_argument("--check", action="store_true", help="fail rather than write")
    args = parser.parse_args()
    files = wanted()
    present = set(FOLDER.glob("*.docx")) if FOLDER.exists() else set()
    stale = [p for p, data in files.items() if not p.exists() or p.read_bytes() != data]
    extra = sorted(present - set(files))
    if args.check:
        for path in [*stale, *extra]:
            sys.stderr.write(f"out of date: {path.relative_to(ROOT)}\n")
        return 1 if stale or extra else 0
    FOLDER.mkdir(parents=True, exist_ok=True)
    for path in extra:
        path.unlink()
    for path in stale:
        path.write_bytes(files[path])
        sys.stdout.write(f"wrote {path.relative_to(ROOT)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
