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
import struct
import sys
import zipfile
import zlib
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
    footnotes: str = ""
    endnotes: str = ""
    settings: str = ""
    final: str = "<w:sectPr/>"
    # Headers, footers and comments: (relationship id, kind, content), each in its own part.
    stories: tuple[tuple[str, str, str], ...] = ()
    # A picture part (PICTURE_ID) and the drawing namespaces, for the drawing cases.
    media: bool = False


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


def words(text: str) -> str:
    """A run of text."""
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


def cite(key: int, kind: str = "footnote", custom: bool = False) -> str:
    """A run holding a reference to note ``key``."""
    follows = ' w:customMarkFollows="1"' if custom else ""
    return f'<w:r><w:{kind}Reference w:id="{key}"{follows}/></w:r>'


def para(*pieces: str, props: str = "") -> str:
    """A paragraph of ``pieces``."""
    return f"<w:p>{f'<w:pPr>{props}</w:pPr>' if props else ''}{''.join(pieces)}</w:p>"


def _png() -> bytes:
    """A one-pixel black PNG."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data)
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(bytes(4), 9)
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")
    )


PICTURE_ID = "rIdPicture"
DRAWING_NAMESPACES = (
    f' xmlns:r="{OFFICE}"'
    ' xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
    ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    ' xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
    ' xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"'
    ' xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
    ' xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office"'
)
_SIZE = '<wp:extent cx="95250" cy="95250"/>'
_PICTURE = (
    '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
    '<pic:pic><pic:nvPicPr><pic:cNvPr id="0" name="dot.png"/><pic:cNvPicPr/></pic:nvPicPr>'
    f'<pic:blipFill><a:blip r:embed="{PICTURE_ID}"/><a:stretch><a:fillRect/></a:stretch>'
    '</pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="95250" cy="95250"/>'
    '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
    "</a:graphicData></a:graphic>"
)


def _frame(key: int, size: str, graphic: str, wrap: str | None) -> str:
    """A drawing's frame: in line with the text, or (with a wrap) anchored to the paragraph."""
    named = f'<wp:docPr id="{key}" name="Drawing {key}"/>'
    if wrap is None:
        return (
            f'<wp:inline distT="0" distB="0" distL="0" distR="0">{size}{named}{graphic}</wp:inline>'
        )
    return (
        '<wp:anchor distT="0" distB="0" distL="114300" distR="114300" simplePos="0" '
        f'relativeHeight="{key}" behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1">'
        '<wp:simplePos x="0" y="0"/><wp:positionH relativeFrom="column"><wp:posOffset>0'
        '</wp:posOffset></wp:positionH><wp:positionV relativeFrom="paragraph"><wp:posOffset>0'
        f'</wp:posOffset></wp:positionV>{size}<wp:effectExtent l="0" t="0" r="0" b="0"/>{wrap}'
        f"{named}<wp:cNvGraphicFramePr/>{graphic}</wp:anchor>"
    )


def picture(key: int, anchored: bool = False) -> str:
    """A run holding a DrawingML picture, in line or anchored to the paragraph."""
    wrap = '<wp:wrapSquare wrapText="bothSides"/>' if anchored else None
    return f"<w:r><w:drawing>{_frame(key, _SIZE, _PICTURE, wrap)}</w:drawing></w:r>"


def shape(key: int, geometry: str, anchored: bool) -> str:
    """A run holding a drawn shape (``line`` or ``rect``), as Word writes one: VML as fallback."""
    line = geometry == "line"
    width, height = (2000000, 0) if line else (95250, 95250)
    graphic = (
        '<a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/'
        f'wordprocessingShape"><wps:wsp>{"<wps:cNvCnPr/>" if line else "<wps:cNvSpPr/>"}'
        f'<wps:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{width}" cy="{height}"/></a:xfrm>'
        f'<a:prstGeom prst="{geometry}"><a:avLst/></a:prstGeom>'
        + ("" if line else '<a:solidFill><a:srgbClr val="000000"/></a:solidFill>')
        + '<a:ln w="12700"><a:solidFill><a:srgbClr val="000000"/></a:solidFill></a:ln>'
        "</wps:spPr><wps:bodyPr/></wps:wsp></a:graphicData></a:graphic>"
    )
    size = f'<wp:extent cx="{width}" cy="{height}"/>'
    frame = _frame(key, size, graphic, "<wp:wrapNone/>" if anchored else None)
    fallback = (
        '<v:line from="0,0" to="157.5pt,0" strokeweight="1pt"/>'
        if line
        else '<v:rect style="width:7.5pt;height:7.5pt" fillcolor="black"/>'
    )
    return (
        f'<w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing>{frame}</w:drawing>'
        f"</mc:Choice><mc:Fallback><w:pict>{fallback}</w:pict></mc:Fallback>"
        "</mc:AlternateContent></w:r>"
    )


def vml_picture(key: int, floating: bool) -> str:
    """A run holding a VML picture, in line or positioned absolutely (floating)."""
    place = "position:absolute;margin-left:0;margin-top:0;z-index:1;" if floating else ""
    return (
        f'<w:r><w:pict><v:shape id="p{key}" style="{place}width:7.5pt;height:7.5pt" '
        f'type="#_x0000_t75"><v:imagedata r:id="{PICTURE_ID}" o:title=""/></v:shape></w:pict></w:r>'
    )


def note(key: int, kind: str = "footnote") -> str:
    """A note: its mark's echo, then its text."""
    return (
        f'<w:{kind} w:id="{key}"><w:p><w:r><w:{kind}Ref/></w:r>{words(f" note {key}")}</w:p>'
        f"</w:{kind}>"
    )


def notes(kind: str, *keys: int) -> str:
    """The separators Word writes, and notes ``keys``."""
    return (
        f'<w:{kind} w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:{kind}>'
        f'<w:{kind} w:type="continuationSeparator" w:id="0"><w:p><w:r>'
        f"<w:continuationSeparator/></w:r></w:p></w:{kind}>" + "".join(note(k, kind) for k in keys)
    )


def section(footnote_pr: str = "") -> str:
    """A sectPr, with footnote properties if given."""
    inner = f"<w:footnotePr>{footnote_pr}</w:footnotePr>" if footnote_pr else ""
    return f"<w:sectPr>{inner}</w:sectPr>"


EACH_SECTION = '<w:numRestart w:val="eachSect"/>'

BACKSLASH = chr(92)


def field(code: str, stored: str) -> str:
    """A complex field showing ``stored``, as Word writes one."""
    return (
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        f'<w:r><w:instrText xml:space="preserve"> {code} </w:instrText></w:r>'
        f'<w:r><w:fldChar w:fldCharType="separate"/></w:r>{words(stored) if stored else ""}'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )


def bare(code: str) -> str:
    """A complex field with no stored result (no ``separate``), as WordPerfect conversions leave."""
    return (
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        f'<w:r><w:instrText xml:space="preserve"> {code} </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )


def seq(stored: str, switches: str = "", identifier: str = "Table") -> str:
    """A caption: "Table " and a SEQ field."""
    code = f"SEQ {identifier} {switches}".strip().replace("\\", BACKSLASH)
    return para(words(f"{identifier} "), field(code, stored))


def bookmark(key: int, name: str, *pieces: str) -> str:
    """``pieces`` inside a bookmark, as Word marks the target of a cross-reference."""
    return (
        f'<w:bookmarkStart w:id="{key}" w:name="{name}"/>{"".join(pieces)}'
        f'<w:bookmarkEnd w:id="{key}"/>'
    )


def heading(level: int, text: str) -> str:
    """A paragraph in the built-in style "heading <level>"."""
    return para(words(text), props=f'<w:pStyle w:val="Heading{level}"/>')


# Built-in heading styles, numbered 1, 1.1 by list 1 (OUTLINE), as Word's outline numbering is.
HEADINGS = "".join(
    f'<w:style w:type="paragraph" w:styleId="Heading{n}"><w:name w:val="heading {n}"/>'
    f"<w:pPr><w:numPr>{f'<w:ilvl w:val={chr(34)}{n - 1}{chr(34)}/>' if n > 1 else ''}"
    f'<w:numId w:val="1"/></w:numPr><w:outlineLvl w:val="{n - 1}"/></w:pPr></w:style>'
    for n in (1, 2)
)

TOGGLES_ON = "<w:b/><w:i/><w:caps/><w:strike/>"
TOGGLES_OFF = '<w:b w:val="0"/><w:i w:val="0"/><w:caps w:val="0"/><w:strike w:val="0"/>'


def _style(kind: str, key: str, rpr: str = "", based: str = "") -> str:
    parent = f'<w:basedOn w:val="{based}"/>' if based else ""
    return (
        f'<w:style w:type="{kind}" w:styleId="{key}"><w:name w:val="{key}"/>{parent}'
        f"<w:rPr>{rpr}</w:rPr></w:style>"
    )


TOGGLE_STYLES = (
    _style("paragraph", "PB", TOGGLES_ON)
    + _style("paragraph", "PB2", TOGGLES_ON, "PB")
    + _style("paragraph", "PBchild", "", "PB")
    + _style("paragraph", "POff", TOGGLES_OFF)
    + _style("character", "CB", TOGGLES_ON)
    + _style("character", "CB2", TOGGLES_ON, "CB")
    + _style("character", "CBchild", "", "CB")
    + _style("table", "TB", TOGGLES_ON)
)


class Toggles(NamedTuple):
    """Where a toggle case sets its toggles: paragraph style, character style, run, table."""

    pst: str = ""
    cst: str = ""
    direct: str = ""
    table: bool = False


def _toggle_paragraph(pst: str = "", cst: str = "", direct: str = "", table: bool = False) -> str:
    props = f'<w:pStyle w:val="{pst}"/>' if pst else ""
    rpr = (f'<w:rStyle w:val="{cst}"/>' if cst else "") + direct
    run = f"<w:r>{f'<w:rPr>{rpr}</w:rPr>' if rpr else ''}<w:t>x</w:t></w:r>"
    paragraph = para(run, props=props)
    if table:
        paragraph = (
            '<w:tbl><w:tblPr><w:tblStyle w:val="TB"/></w:tblPr><w:tr><w:tc>'
            + paragraph
            + "</w:tc></w:tr></w:tbl>"
        )
    return paragraph


TOGGLE_CASES: list[Toggles] = [
    Toggles(),
    Toggles(direct=TOGGLES_ON),
    Toggles(direct=TOGGLES_OFF),
    Toggles(pst="PB"),
    Toggles(cst="CB"),
    Toggles(pst="PB", cst="CB"),
    Toggles(pst="PB", direct=TOGGLES_ON),
    Toggles(pst="PB", direct=TOGGLES_OFF),
    Toggles(cst="CB", direct=TOGGLES_OFF),
    Toggles(pst="PBchild"),
    Toggles(pst="PB2"),
    Toggles(pst="PB2", cst="CB"),
    Toggles(pst="POff", cst="CB"),
    Toggles(cst="CB2"),
    Toggles(cst="CBchild"),
    Toggles(pst="PB", cst="CBchild"),
    Toggles(table=True),
    Toggles(table=True, pst="PB"),
    Toggles(table=True, cst="CB"),
    Toggles(table=True, pst="PB", cst="CB"),
    Toggles(table=True, direct=TOGGLES_ON),
]
DEFAULTS_OFF_STYLES = (
    TOGGLE_STYLES
    + _style("character", "COff", TOGGLES_OFF)
    + _style("table", "TOff", TOGGLES_OFF)
    + _style("paragraph", "POffChild", "", "POff")
)


def _defaults_paragraph(pst: str, cst: str, table: str = "") -> str:
    props = f'<w:pStyle w:val="{pst}"/>' if pst else ""
    rpr = f'<w:rPr><w:rStyle w:val="{cst}"/></w:rPr>' if cst else ""
    paragraph = para(f"<w:r>{rpr}<w:t>x</w:t></w:r>", props=props)
    if table:
        paragraph = (
            f'<w:tbl><w:tblPr><w:tblStyle w:val="{table}"/></w:tblPr><w:tr><w:tc>{paragraph}'
            "</w:tc></w:tr></w:tbl>"
        )
    return paragraph


TOGGLE_DEFAULT_CASES: list[Toggles] = [
    Toggles(),
    Toggles(pst="PB"),
    Toggles(cst="CB"),
    Toggles(pst="PB", cst="CB"),
    Toggles(direct=TOGGLES_ON),
    Toggles(direct=TOGGLES_OFF),
    Toggles(table=True),
]

OUTLINE_NUMBERING = abstract(1, lvl(0, text="%1"), lvl(1, text="%1.%2")) + num(1, 1)
DOTTED_NUMBERING = abstract(1, lvl(0, text="%1."), lvl(1, text="%1.%2.")) + num(1, 1)

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
WINGDINGS = '<w:rPr><w:rFonts w:ascii="Wingdings" w:hAnsi="Wingdings" w:hint="default"/></w:rPr>'
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
    "restart-never-rows": Case(
        "lvlRestart 0 on level 1, counted after a level 0 paragraph and the end of a table row "
        "(the reader refuses).",
        abstract(1, lvl(0, text="%1"), lvl(1, text="%2", extra='<w:lvlRestart w:val="0"/>'))
        + num(1, 1),
        items((1, 1), (1, 0))
        + "<w:tbl><w:tr><w:tc>"
        + items()
        + "<w:p/></w:tc></w:tr><w:tr><w:tc>"
        + items((1, 1))
        + "</w:tc></w:tr></w:tbl>",
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
    "override-implicit-continued": Case(
        "Level 0 first reached at level 1 by a list with a startOverride, then another list.",
        SECTIONS + num(1, 1) + num(3, 1, start_at(0, 6)),
        items((3, 1), (1, 1), (1, 0), (3, 0)),
    ),
    "override-implicit-reused": Case(
        "Level 1 reached at level 2, after its list's startOverride was used.",
        SECTIONS + num(1, 1) + num(3, 1, start_at(1, 6)),
        items((3, 1), (1, 0), (3, 2), (1, 1), (3, 1)),
    ),
    "override-implicit-rows": Case(
        "As override-implicit-continued, but the other list counts level 0 in a later row of a "
        "table: Word takes the override here, not in other tables (the reader refuses).",
        SECTIONS + num(1, 1) + num(3, 1, start_at(0, 6)),
        items((3, 1))
        + "<w:tbl><w:tr><w:tc>"
        + items((3, 1))
        + "</w:tc></w:tr><w:tr><w:tc>"
        + items((1, 0))
        + "</w:tc></w:tr></w:tbl>",
    ),
    "override-implicit-levels": Case(
        "Levels 0 and 1, both with startOverrides, first reached at level 2.",
        SECTIONS + num(1, 1) + num(3, 1, start_at(0, 6) + start_at(1, 4)),
        items((3, 2), (1, 1), (1, 0), (3, 1)),
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
    "bullets-wingdings": Case(
        "Word's third-level bullet, the Wingdings square, stored as U+F0A7 and as \u00a7.",
        abstract(1, lvl(0, "bullet", "\uf0a7", WINGDINGS))
        + abstract(2, lvl(0, "bullet", "\u00a7", WINGDINGS))
        + num(1, 1)
        + num(2, 2),
        items((1, 0), (2, 0)),
    ),
    "fields-seq": Case(
        "SEQ captions count 1, 2, 3, stored as Word prints them.",
        "",
        seq("1") + seq("2") + seq("3"),
    ),
    "fields-seq-formats": Case(
        "One SEQ count shown in each number format.",
        "",
        "".join(
            seq(stored, f"\\* {fmt}")
            for fmt, stored in (
                ("ARABIC", "1"),
                ("ROMAN", "II"),
                ("roman", "iii"),
                ("ALPHABETIC", "D"),
                ("alphabetic", "e"),
                ("ARABIC \\* MERGEFORMAT", "6"),
            )
        ),
    ),
    "fields-seq-identifiers": Case(
        "Tables and figures count apart.",
        "",
        seq("1") + seq("1", identifier="Figure") + seq("2") + seq("2", identifier="Figure"),
    ),
    "fields-seq-switches": Case(
        "SEQ \\r sets, \\c repeats, \\n counts on, \\h counts and shows nothing.",
        "",
        seq("1")
        + seq("5", "\\r 5")
        + seq("5", "\\c")
        + seq("6", "\\n")
        + seq("", "\\h")
        + seq("8"),
    ),
    "fields-chapter-reset": Case(
        "SEQ \\s 1 restarts after each heading 1, not after a heading 2.",
        OUTLINE_NUMBERING,
        heading(1, "Intro")
        + seq("1", "\\s 1")
        + seq("2", "\\s 1")
        + heading(2, "Sub")
        + seq("3", "\\s 1")
        + heading(1, "Next")
        + seq("1", "\\s 1"),
        HEADINGS,
    ),
    "fields-chapter-reset-level-2": Case(
        "SEQ \\s 2 restarts after a heading 2 and after a heading 1.",
        OUTLINE_NUMBERING,
        heading(1, "A")
        + heading(2, "A.1")
        + seq("1", "\\s 2")
        + seq("2", "\\s 2")
        + heading(1, "B")
        + seq("1", "\\s 2")
        + heading(2, "B.1")
        + seq("1", "\\s 2"),
        HEADINGS,
    ),
    "fields-chapter-captions": Case(
        "Captions by chapter: STYLEREF 1 \\s, a hyphen, SEQ \\s 1; and the heading's text.",
        OUTLINE_NUMBERING,
        heading(1, "Intro")
        + para(
            words("Table "),
            field(f"STYLEREF 1 {BACKSLASH}s", "1"),
            words("-"),
            field(f"SEQ Table {BACKSLASH}s 1", "1"),
        )
        + heading(1, "Next")
        + para(
            words("Table "),
            field(f"STYLEREF 1 {BACKSLASH}s", "2"),
            words("-"),
            field(f"SEQ Table {BACKSLASH}s 1", "1"),
        )
        + para(field("STYLEREF 1", "Next"))
        + para(field('STYLEREF "heading 1"', "Next")),
        HEADINGS,
    ),
    "fields-chapter-dotted": Case(
        "STYLEREF \\s of headings numbered 1. and 1.1. drops the final period.",
        DOTTED_NUMBERING,
        heading(1, "A")
        + heading(2, "A.1")
        + para(field(f"STYLEREF 1 {BACKSLASH}s", "1"))
        + para(field(f"STYLEREF 2 {BACKSLASH}s", "1.1")),
        HEADINGS,
    ),
    "fields-heading-by-name": Case(
        "Word's heading levels go by the style's name, not its outline level.",
        "",
        seq("1", "\\s 1")
        + para(words("custom outline 0"), props='<w:pStyle w:val="Chapter"/>')
        + seq("2", "\\s 1")
        + para(words("named heading 1"), props='<w:pStyle w:val="Heading1"/>')
        + seq("1", "\\s 1")
        + para(field("STYLEREF 1", "named heading 1")),
        '<w:style w:type="paragraph" w:styleId="Chapter"><w:name w:val="Chapter"/>'
        '<w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>',
    ),
    "fields-styleref-forward": Case(
        "STYLEREF before any paragraph of its style takes the next one.",
        OUTLINE_NUMBERING,
        para(field("STYLEREF 1", "Late")) + heading(1, "Late"),
        HEADINGS,
    ),
    "fields-styleref-characters": Case(
        "STYLEREF to a heading with a no-break space, a no-break hyphen and a soft hyphen: Word "
        "shows a space, a hyphen and nothing.",
        OUTLINE_NUMBERING,
        para(
            words("5\u00a0\u00b5g"),
            "<w:r><w:noBreakHyphen/></w:r>",
            words("once"),
            "<w:r><w:softHyphen/></w:r>",
            words("daily"),
            props='<w:pStyle w:val="Heading1"/>',
        )
        + para(field("STYLEREF 1", "5 \u00b5g-oncedaily")),
        HEADINGS,
    ),
    "fields-styleref-symbol": Case(
        "STYLEREF to a heading with a Symbol character, which Word leaves out (the reader "
        "refuses).",
        OUTLINE_NUMBERING,
        para(
            words("dose "),
            '<w:r><w:sym w:font="Symbol" w:char="F0B3"/></w:r>',
            words(" 5"),
            props='<w:pStyle w:val="Heading1"/>',
        )
        + para(field("STYLEREF 1", "dose  5")),
        HEADINGS,
    ),
    "fields-ref": Case(
        "A cross-reference (REF) to bookmarked text prints that text.",
        "",
        para(words("Store "), bookmark(1, "store", words("below 25 C")), words("."))
        + para(words("Keep it "), field(f"REF store {BACKSLASH}h", "below 25 C")),
    ),
    "fields-ref-stale": Case(
        "A REF stored as other text than its bookmark's (the reader refuses).",
        "",
        para(bookmark(1, "store", words("below 25 C"))) + para(field("REF store", "below 30 C")),
    ),
    "fields-ref-missing": Case(
        "A REF to a bookmark that is not there; Word prints an error (the reader refuses).",
        "",
        para(field("REF gone", "old text")),
    ),
    "fields-ref-caption": Case(
        "A cross-reference to a caption: the caption's bookmark holds its SEQ.",
        "",
        para(bookmark(1, "_Ref1", words("Table "), field("SEQ Table", "1")))
        + para(bookmark(2, "_Ref2", words("Table "), field("SEQ Table", "2")))
        + para(words("See "), field(f"REF _Ref2 {BACKSLASH}h", "Table 2"), words(".")),
    ),
    "fields-noteref": Case(
        "A NOTEREF prints the mark of the note its bookmark holds.",
        "",
        para(words("a"), bookmark(1, "fn1", cite(1)))
        + para(words("b"), bookmark(2, "fn2", cite(2)))
        + para(words("see note "), field(f"NOTEREF fn2 {BACKSLASH}h", "2")),
        footnotes=notes("footnote", 1, 2),
    ),
    "fields-noteref-stale": Case(
        "A NOTEREF stored as 7 for note 1 (the reader refuses).",
        "",
        para(words("a"), bookmark(1, "fn1", cite(1)))
        + para(words("see note "), field(f"NOTEREF fn1 {BACKSLASH}h", "7")),
        footnotes=notes("footnote", 1),
    ),
    "fields-stored": Case(
        "DOCPROPERTY and HYPERLINK, which Word prints as stored.",
        "",
        para(field("DOCPROPERTY Title", "a stored title"))
        + para(field('HYPERLINK "https://example.org"', "the agency's page")),
    ),
    "fields-seq-hidden-no-result": Case(
        "A hidden SEQ with no stored result shows nothing and still counts: \\r 3, then 4.",
        "",
        para(bare("SEQ CHAPTER \\h \\r 1"), words("Text after a hidden chapter count."))
        + para(words("Before "), bare("SEQ Table \\h \\r 3"))
        + seq("4"),
    ),
    "fields-seq-shown-no-result": Case(
        "A SEQ with no stored result that would show a number (the reader refuses it).",
        "",
        para(words("Table "), bare("SEQ Table")),
    ),
    "fields-stale": Case(
        "Captions stored as 7 and 7, which Word prints as 1 and 2 (the reader refuses).",
        "",
        seq("7") + seq("7"),
    ),
    "emphasis-toggles": Case(
        "Bold, italic, caps and strike set every way a style can set them (one 'x' each).",
        "",
        "".join(_toggle_paragraph(*case) for case in TOGGLE_CASES),
        TOGGLE_STYLES,
    ),
    "restart-never-shown-deeper": Case(
        "lvlRestart 0 on level 1, counted by list 1, then shown in list 2's level 2 label after "
        "list 2's level 0 paragraph: Word draws it as a space.",
        abstract(
            1,
            lvl(0, "decimalZero", "%1)", start=None),
            lvl(1, "upperLetter", "%2)", '<w:lvlRestart w:val="0"/>'),
            lvl(2, "upperLetter", "%1.%2.%3."),
        )
        + num(1, 1, start_at(2, 2))
        + num(2, 1, start_at(0, 2)),
        items((1, 1), (2, 0), (2, 2)),
    ),
    "restart-source-override": Case(
        "List 1 overrides level 1's start with 8; its level 0 paragraph restarts level 1, which "
        "list 2 counts next: from list 1's override.",
        abstract(1, lvl(0, text="%1)"), lvl(1, "decimalZero", "(%2)", start=0))
        + num(1, 1, start_at(1, 8))
        + num(2, 1),
        items((1, 0), (2, 1), (2, 1)),
    ),
    "restart-source-plain": Case(
        "List 1 overrides level 2's start; list 2's paragraph restarts level 2, which list 1 "
        "counts next: from the abstractNum's start.",
        abstract(
            1,
            lvl(0, "upperRoman", "%1.", start=2),
            lvl(1, "lowerRoman", "%1.%2.", start=5),
            lvl(2, "upperLetter", "%3."),
        )
        + num(1, 1, start_at(2, 5))
        + num(2, 1, start_at(0, 8)),
        items((1, 2), (2, 1), (1, 2)),
    ),
    "restart-source-unused": Case(
        "List 1 overrides level 2's start; its level 1 paragraph restarts level 2, never yet "
        "counted, which list 2 counts first: from list 1's override.",
        abstract(
            1,
            lvl(0, "decimalZero", "%1)", start=None),
            lvl(1, "upperLetter", "%2)"),
            lvl(2, "upperLetter", "%1.%2.%3."),
        )
        + num(1, 1, start_at(2, 2))
        + num(2, 1, start_at(0, 2)),
        items((1, 1), (2, 2), (2, 2)),
    ),
    "restart-source-cells": Case(
        "List 1's level 1 paragraph restarts level 2, which list 3 counts next, in the next cell "
        "of the same row: from list 1's override.",
        SECTIONS + num(1, 1, start_at(2, 7)) + num(3, 1),
        items((3, 0), (3, 2))
        + "<w:tbl><w:tr><w:tc>"
        + items((1, 1))
        + "</w:tc><w:tc>"
        + items((3, 2))
        + "</w:tc></w:tr></w:tbl>",
    ),
    "restart-source-rows": Case(
        "As restart-source-cells, but list 3 counts level 2 in the next row: not from list 1's "
        "override (the reader refuses).",
        SECTIONS + num(1, 1, start_at(2, 7)) + num(3, 1),
        items((3, 0), (3, 2))
        + "<w:tbl><w:tr><w:tc>"
        + items((1, 1))
        + "</w:tc></w:tr><w:tr><w:tc>"
        + items((3, 2))
        + "</w:tc></w:tr></w:tbl>",
    ),
    "emphasis-defaults-off": Case(
        "The document defaults turn the toggles on and styles of each kind turn them off: each "
        "kind that differs from the defaults turns them over.",
        "",
        "".join(
            _defaults_paragraph(*case)
            for case in (
                ("", "COff"),
                ("POff", ""),
                ("POff", "CB"),
                ("PB", "COff"),
                ("POff", "COff"),
                ("POffChild", ""),
                ("", "", "TOff"),
                ("POff", "", "TOff"),
                ("POff", "COff", "TOff"),
                ("POff", "", "TB"),
                ("", "CB", "TOff"),
            )
        ),
        "<w:docDefaults><w:rPrDefault><w:rPr>" + TOGGLES_ON + "</w:rPr></w:rPrDefault>"
        "</w:docDefaults>" + DEFAULTS_OFF_STYLES,
    ),
    "default-character-style": Case(
        "A default character style that turns bold on: Word does not apply it to text.",
        "",
        para(words("plain"))
        + para('<w:r><w:rPr><w:rStyle w:val="Missing"/></w:rPr><w:t>unknown style</w:t></w:r>'),
        '<w:style w:type="character" w:default="1" w:styleId="DC"><w:name w:val="DC"/>'
        "<w:rPr><w:b/></w:rPr></w:style>",
    ),
    "restart-level-above": Case(
        "lvlRestart 1 on level 1, the level directly above written out: Word draws it empty.",
        abstract(1, lvl(0, text="%1."), lvl(1, text="%1.%2.", extra='<w:lvlRestart w:val="1"/>'))
        + num(1, 1),
        items((1, 0), (1, 1), (1, 1)),
    ),
    "restart-skipped-ancestor": Case(
        "lvlRestart 0 on level 1, first counted by a level 2 item: Word draws it otherwise.",
        abstract(
            1,
            lvl(0, text="%1."),
            lvl(1, text="%1.%2.", extra='<w:lvlRestart w:val="0"/>', start=3),
            lvl(2, "lowerLetter", "%1.%2.%3."),
        )
        + num(1, 1),
        items((1, 2), (1, 0), (1, 2)),
    ),
    "emphasis-defaults": Case(
        "The same toggles with the document defaults turning them on.",
        "",
        "".join(_toggle_paragraph(*case) for case in TOGGLE_DEFAULT_CASES),
        "<w:docDefaults><w:rPrDefault><w:rPr>" + TOGGLES_ON + "</w:rPr></w:rPrDefault>"
        "</w:docDefaults>" + TOGGLE_STYLES,
    ),
    "notes-continuous": Case(
        "Footnotes number 1, 2, 3 through the document.",
        "",
        para(words("a"), cite(1)) + para(words("b"), cite(2), words(" c"), cite(3)),
        footnotes=notes("footnote", 1, 2, 3),
    ),
    "notes-each-section": Case(
        "Footnotes restart in each section (numRestart eachSect).",
        "",
        para(words("a"), cite(1))
        + para(words("b"), cite(2), props=section(EACH_SECTION))
        + para(words("c"), cite(3))
        + para(words("d"), cite(4)),
        footnotes=notes("footnote", 1, 2, 3, 4),
        final=section(EACH_SECTION),
    ),
    "notes-start-format": Case(
        "Footnotes from 5 in lower roman (the document's footnotePr).",
        "",
        para(words("a"), cite(1)) + para(words("b"), cite(2)),
        footnotes=notes("footnote", 1, 2),
        settings='<w:footnotePr><w:numFmt w:val="lowerRoman"/><w:numStart w:val="5"/>'
        "</w:footnotePr>",
    ),
    "notes-document-format": Case(
        "The document's footnotePr, with the separators Word lists in it, and no section's.",
        "",
        para(words("a"), cite(1)) + para(words("b"), cite(2)),
        footnotes=notes("footnote", 1, 2),
        settings='<w:footnotePr><w:numFmt w:val="upperRoman"/><w:numStart w:val="3"/>'
        '<w:footnote w:id="-1"/><w:footnote w:id="0"/></w:footnotePr>',
    ),
    "notes-section-rules": Case(
        "Footnotes from 5 in lower roman, set on the section as Word writes them.",
        "",
        para(words("a"), cite(1)) + para(words("b"), cite(2)),
        footnotes=notes("footnote", 1, 2),
        final=section('<w:numFmt w:val="lowerRoman"/><w:numStart w:val="5"/>'),
    ),
    "notes-section-chicago": Case(
        "Footnotes in symbols (chicago) set on the section, past the fourth.",
        "",
        para(words("a"), *(cite(k) for k in range(1, 7))),
        footnotes=notes("footnote", *range(1, 7)),
        final=section('<w:numFmt w:val="chicago"/>'),
    ),
    "notes-chicago": Case(
        "Footnotes in symbols (chicago), past the fourth.",
        "",
        para(words("a"), *(cite(k) for k in range(1, 7))),
        footnotes=notes("footnote", *range(1, 7)),
        settings='<w:footnotePr><w:numFmt w:val="chicago"/></w:footnotePr>',
    ),
    "notes-custom-mark": Case(
        "A footnote with a custom mark between two numbered ones: does it take a number?",
        "",
        para(words("a"), cite(1))
        + para(words("b"), cite(2, custom=True), words("\u2020"))
        + para(words("c"), cite(3)),
        footnotes=notes("footnote", 1, 2, 3),
    ),
    "notes-endnotes": Case(
        "Endnotes in Word's default format.",
        "",
        para(words("a"), cite(1, "endnote")) + para(words("b"), cite(2, "endnote")),
        endnotes=notes("endnote", 1, 2),
    ),
    "notes-mixed": Case(
        "Footnotes and endnotes count apart.",
        "",
        para(words("a"), cite(1), cite(1, "endnote"), cite(2)),
        footnotes=notes("footnote", 1, 2),
        endnotes=notes("endnote", 1),
    ),
    "notes-in-table": Case(
        "A footnote in a table cell counts in document order.",
        "",
        "<w:tbl><w:tr><w:tc>"
        + para(words("a"), cite(1))
        + "</w:tc></w:tr></w:tbl>"
        + para(words("b"), cite(2)),
        footnotes=notes("footnote", 1, 2),
    ),
    "notes-section-start-continuous": Case(
        "Continuous footnotes; the second section sets numStart 10.",
        "",
        para(words("a"), cite(1))
        + para(words("b"), cite(2), props=section())
        + para(words("c"), cite(3)),
        footnotes=notes("footnote", 1, 2, 3),
        final=section('<w:numStart w:val="10"/>'),
    ),
    "notes-section-format": Case(
        "Continuous footnotes; the second section sets upper letters.",
        "",
        para(words("a"), cite(1))
        + para(words("b"), cite(2), props=section())
        + para(words("c"), cite(3)),
        footnotes=notes("footnote", 1, 2, 3),
        final=section('<w:numFmt w:val="upperLetter"/>'),
    ),
    "drawing-inline-picture": Case(
        "A picture in line with the text: Word's text shows it as '/'.",
        "",
        para(words("a"), picture(1), words("b")),
        media=True,
    ),
    "drawing-anchored-picture": Case(
        "A picture anchored to the paragraph (floating): what Word's text shows for it.",
        "",
        para(words("a"), picture(2, anchored=True), words("b")),
        media=True,
    ),
    "drawing-anchored-line": Case(
        "A drawn line anchored to the paragraph, as the FDA template's rules: Word's text.",
        "",
        para(words("a"), shape(3, "line", anchored=True), words("b")),
        media=True,
    ),
    "drawing-anchored-line-alone": Case(
        "A paragraph holding only an anchored drawn line, then text.",
        "",
        para(shape(4, "line", anchored=True)) + para(words("after")),
        media=True,
    ),
    "drawing-inline-shape": Case(
        "A drawn square in line with the text: Word's text.",
        "",
        para(words("a"), shape(5, "rect", anchored=False), words("b")),
        media=True,
    ),
    "drawing-anchored-shape": Case(
        "A drawn square anchored to the paragraph: Word's text.",
        "",
        para(words("a"), shape(6, "rect", anchored=True), words("b")),
        media=True,
    ),
    "drawing-vml-inline-picture": Case(
        "A VML picture in line with the text: Word's text.",
        "",
        para(words("a"), vml_picture(7, floating=False), words("b")),
        media=True,
    ),
    "drawing-vml-floating-picture": Case(
        "A VML picture positioned absolutely (floating): Word's text.",
        "",
        para(words("a"), vml_picture(8, floating=True), words("b")),
        media=True,
    ),
}


def package(case: Case) -> bytes:
    """``case`` as a complete .docx, stored, with fixed timestamps."""
    # Parts only the note cases have, so the list cases' bytes do not change.
    extra = [
        (name, content)
        for name, content in (
            ("footnotes", case.footnotes),
            ("endnotes", case.endnotes),
            ("settings", case.settings),
        )
        if content
    ]
    roots = {
        "footnotes": "w:footnotes",
        "endnotes": "w:endnotes",
        "settings": "w:settings",
        "header": "w:hdr",
        "footer": "w:ftr",
        "comments": "w:comments",
    }
    # A story part is named by its relationship id; the document then declares the r: prefix.
    named = [(f"{key}.xml", kind, content) for key, kind, content in case.stories]
    declare = DRAWING_NAMESPACES if case.media else f' xmlns:r="{OFFICE}"' if case.stories else ""
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            + ('<Default Extension="png" ContentType="image/png"/>' if case.media else "")
            + f'<Override PartName="/word/document.xml" ContentType="{MAIN}.document.main+xml"/>'
            f'<Override PartName="/word/styles.xml" ContentType="{MAIN}.styles+xml"/>'
            f'<Override PartName="/word/numbering.xml" ContentType="{MAIN}.numbering+xml"/>'
            + "".join(
                f'<Override PartName="/word/{name}.xml" ContentType="{MAIN}.{name}+xml"/>'
                for name, content in extra
            )
            + "".join(
                f'<Override PartName="/word/{name}" ContentType="{MAIN}.{kind}+xml"/>'
                for name, kind, _ in named
            )
            + "</Types>"
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
            + "".join(
                f'<Relationship Id="rId{3 + i}" Type="{OFFICE}/{name}" Target="{name}.xml"/>'
                for i, (name, content) in enumerate(extra)
            )
            + "".join(
                f'<Relationship Id="{key}" Type="{OFFICE}/{kind}" Target="{key}.xml"/>'
                for key, kind, _ in case.stories
            )
            + (
                f'<Relationship Id="{PICTURE_ID}" Type="{OFFICE}/image" Target="media/dot.png"/>'
                if case.media
                else ""
            )
            + "</Relationships>"
        ),
        "word/document.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document xmlns:w="{W}"{declare}>'
            f"<w:body>{case.body}{case.final}</w:body></w:document>"
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
    for name, content in extra:
        parts[f"word/{name}.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><{roots[name]} xmlns:w="{W}">'
            f"{content}</{roots[name]}>"
        )
    for name, kind, content in named:
        parts[f"word/{name}"] = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><{roots[kind]} xmlns:w="{W}">'
            f"{content}</{roots[kind]}>"
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, content in parts.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            archive.writestr(info, content.encode("utf-8"))
        if case.media:
            info = zipfile.ZipInfo("word/media/dot.png", date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            archive.writestr(info, _png())
    return buffer.getvalue()


def files(folder: Path, cases: dict[str, Case], note: str) -> dict[Path, bytes]:
    """Every file of a set of cases, with its bytes: one .docx each, and ``sources.json``."""
    out: dict[Path, bytes] = {}
    sources = []
    for name, case in cases.items():
        data = package(case)
        out[folder / f"{name}.docx"] = data
        sources.append(
            {
                "name": case.question,
                "file": f"{name}.docx",
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    manifest = {"schemaVersion": "1.0.0", "note": note, "sources": sources}
    out[folder / "sources.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    return out


def write(folder: Path, wanted_files: dict[Path, bytes], check: bool) -> int:
    """Write the set's files (or, checking, report any out of date); 1 if checking finds one."""
    present = set(folder.glob("*.docx")) if folder.exists() else set()
    stale = [p for p, data in wanted_files.items() if not p.exists() or p.read_bytes() != data]
    extra = sorted(present - set(wanted_files))
    if check:
        for path in [*stale, *extra]:
            sys.stderr.write(f"out of date: {path.relative_to(ROOT)}\n")
        return 1 if stale or extra else 0
    folder.mkdir(parents=True, exist_ok=True)
    for path in extra:
        path.unlink()
    for path in stale:
        path.write_bytes(wanted_files[path])
        sys.stdout.write(f"wrote {path.relative_to(ROOT)}\n")
    return 0


def wanted() -> dict[Path, bytes]:
    """Every file of the corpus set, with its bytes."""
    return files(
        FOLDER, CASES, "Synthetic list-numbering cases written by scripts/numbering_cases.py."
    )


def main() -> int:
    """Write the cases, or with --check report whether they are current."""
    parser = argparse.ArgumentParser(description="Write or check corpus/numbering-cases.")
    parser.add_argument("--check", action="store_true", help="fail rather than write")
    return write(FOLDER, wanted(), parser.parse_args().check)


if __name__ == "__main__":
    raise SystemExit(main())
