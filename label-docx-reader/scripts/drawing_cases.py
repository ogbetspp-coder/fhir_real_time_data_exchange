"""Write, or check, the synthetic drawing cases (corpus/drawing-cases).

    uv run --frozen python scripts/drawing_cases.py           # write the .docx files
    uv run --frozen python scripts/drawing_cases.py --check   # fail if they would change

Each case is a small, complete .docx that Word opens, built to put to Word what it draws for
formatting the reader decides on without a character of text to show for it: a picture's effect
extent and the markup around it that may draw nothing (``picture-*``), a theme's shading and a
pattern's theme colour (``shading-*``), a tab stop's leader (``tabs*``). ``scripts/word_drawn.py
record`` has Word save each as PDF and measures the ink (``word-drawn.json``);
``tests/test_word_drawn.py`` holds the reader to it without Word.

A row case lays one paragraph a row on an exact line (``LINE``) from the page's top margin
(``TOP``), a page break every ``PAGE_ROWS`` rows, so each row's ink is found in its own band. Text
that a row measures across is red (before) and blue (after). The theme is the QRD template's
(corpus/ema-templates), as EMA's labels have it. The files are stored, not deflated, with fixed
timestamps, so the same cases give the same bytes on any machine.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import struct
import zipfile
import zlib
from pathlib import Path
from typing import NamedTuple

from numbering_cases import write

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "corpus" / "drawing-cases"
TEMPLATE = (
    ROOT / "corpus" / "ema-templates" / "qrd-product-information-template-version-104_es.docx"
)
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml"
A14 = "http://schemas.microsoft.com/office/drawing/2010/main"
NAMESPACES = (
    f' xmlns:w="{W}" xmlns:r="{OFFICE}"'
    ' xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
    ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    ' xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
    f' xmlns:a14="{A14}"'
)
# A row case's geometry, in points: the top margin, each row's exact line, rows on a page.
TOP = 36.0
LINE = 24.0
PAGE_ROWS = 30
# A4, half-inch margins, no header or footer space.
SECTION = (
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="720" w:right="720" '
    'w:bottom="720" w:left="720" w:header="0" w:footer="0" w:gutter="0"/></w:sectPr>'
)
RED = '<w:color w:val="FF0000"/>'
BLUE = '<w:color w:val="0000FF"/>'
NBSP = "\u00a0" * 30


class Case(NamedTuple):
    """One .docx: what it puts to Word, its body, and its other parts."""

    question: str
    body: str
    styles: str = ""
    numbering: str = ""
    settings: str | None = None
    # The theme's lt1 (``window`` keeps the template's sysClr), or None for no theme.
    theme: str | None = None
    picture: bool = False
    # How many rows it lays out (0: a picture case, measured as a picture).
    rows: int = 0


def _checkerboard() -> bytes:
    """A 40 x 20 PNG of 4-pixel red and blue squares: its pixels are recognisable when drawn."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    red, blue = b"\xff\x00\x00", b"\x00\x00\xff"
    rows = b"".join(
        b"\x00" + b"".join(red if (x // 4 + y // 4) % 2 else blue for x in range(40))
        for y in range(20)
    )
    header = struct.pack(">IIBBBBB", 40, 20, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def _theme(lt1: str) -> str:
    """The QRD template's theme, its lt1 replaced unless ``window``."""
    with zipfile.ZipFile(TEMPLATE) as template:
        theme = template.read("word/theme/theme1.xml").decode("utf-8")
    # Its own XML declaration goes: ``package`` writes one for every part.
    theme = theme.split("?>", 1)[1].lstrip() if theme.startswith("<?xml") else theme
    window = '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
    if window not in theme:
        raise SystemExit(f"{TEMPLATE.name}: its theme's lt1 is not the window colour")
    return theme if lt1 == "window" else theme.replace(window, f"<a:lt1>{lt1}</a:lt1>")


# --- pictures ------------------------------------------------------------------------------

# The probe's extent: 40 x 20 pixels at 96 dpi, 30 x 15 points.
EXTENT = 'cx="381000" cy="190500"'
SHAPE = (
    f'<a:xfrm><a:off x="0" y="0"/><a:ext {EXTENT}/></a:xfrm>'
    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/>'
)
UNFILLED = "<a:ln><a:noFill/></a:ln>"
SHADOW_OBSCURED = (
    '<a:extLst><a:ext uri="{53640926-AAD7-44D8-BBD7-CCE9431645EC}">'
    "<a14:shadowObscured/></a:ext></a:extLst>"
)
SHADOW = (
    '<a:effectLst><a:outerShdw dist="76200" dir="2700000" algn="tl" rotWithShape="0">'
    '<a:srgbClr val="00FF00"/></a:outerShdw></a:effectLst>'
)
ENDS = "<a:headEnd/><a:tailEnd/>"


def _line(inside: str, attributes: str = "") -> str:
    return f"<a:ln{attributes}><a:noFill/>{inside}</a:ln>"


# Each picture case: its effect extent (l, t, r, b), the blipFill's attributes, its shape.
PICTURES: dict[str, tuple[tuple[int, int, int, int], str, str]] = {
    "plain": ((0, 0, 0, 0), "", SHAPE + UNFILLED),
    "extent-even": ((19050, 19050, 19050, 19050), "", SHAPE + UNFILLED),
    "extent-left": ((95250, 0, 0, 0), "", SHAPE + UNFILLED),
    "extent-right-bottom": ((0, 0, 190500, 95250), "", SHAPE + UNFILLED),
    "extent-big": ((952500, 952500, 952500, 952500), "", SHAPE + UNFILLED),
    "extent-negative": ((-9525, -9525, 0, 0), "", SHAPE + UNFILLED),
    "extent-negative-bottom": ((0, 0, 0, -19050), "", SHAPE + UNFILLED),
    "rot-with-shape-1": ((0, 0, 0, 0), ' rotWithShape="1"', SHAPE + UNFILLED),
    "rot-with-shape-0": ((0, 0, 0, 0), ' rotWithShape="0"', SHAPE + UNFILLED),
    "rot-with-shape-true": ((0, 0, 0, 0), ' rotWithShape="true"', SHAPE + UNFILLED),
    "rot-with-shape-false": ((0, 0, 0, 0), ' rotWithShape="false"', SHAPE + UNFILLED),
    "shadow-obscured": ((0, 0, 0, 0), "", SHAPE + UNFILLED + SHADOW_OBSCURED),
    "shadow-drawn": ((0, 0, 0, 0), "", SHAPE + UNFILLED + SHADOW + SHADOW_OBSCURED),
    "line-miter": ((0, 0, 0, 0), "", SHAPE + _line('<a:miter lim="800000"/>' + ENDS)),
    "line-miter-width": (
        (0, 0, 0, 0),
        "",
        SHAPE + _line('<a:miter lim="800000"/>' + ENDS, ' w="9525"'),
    ),
    "line-miter-bare": ((0, 0, 0, 0), "", SHAPE + _line("<a:miter/>" + ENDS)),
    "line-miter-zero": ((0, 0, 0, 0), "", SHAPE + _line('<a:miter lim="0"/>' + ENDS)),
    "line-round": ((0, 0, 0, 0), "", SHAPE + _line("<a:round/>" + ENDS)),
    "line-bevel": ((0, 0, 0, 0), "", SHAPE + _line("<a:bevel/>" + ENDS)),
    "line-head": ((0, 0, 0, 0), "", SHAPE + _line("<a:headEnd/>")),
    "line-tail": ((0, 0, 0, 0), "", SHAPE + _line("<a:tailEnd/>")),
    "line-wide": ((0, 0, 0, 0), "", SHAPE + _line("", ' w="190500"')),
    "line-drawn": (
        (0, 0, 0, 0),
        "",
        SHAPE + '<a:ln w="38100"><a:solidFill><a:srgbClr val="00FF00"/></a:solidFill>'
        '<a:miter lim="800000"/><a:headEnd/><a:tailEnd/></a:ln>',
    ),
    "combo": (
        (19050, 9525, 19050, 0),
        ' rotWithShape="1"',
        SHAPE + _line('<a:miter lim="800000"/>' + ENDS, ' w="9525"') + SHADOW_OBSCURED,
    ),
}


def picture(effect: tuple[int, int, int, int], fill: str, shape: str, props: str = "") -> str:
    """A paragraph holding one in-line picture of the checkerboard, as Word writes it."""
    left, top, right, bottom = effect
    return (
        f"<w:p>{f'<w:pPr>{props}</w:pPr>' if props else ''}"
        '<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent {EXTENT}/><wp:effectExtent l="{left}" t="{top}" r="{right}" b="{bottom}"/>'
        '<wp:docPr id="1" name="Picture 1"/><wp:cNvGraphicFramePr/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="check.png"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill{fill}><a:blip r:embed="rIdPicture"/><a:stretch><a:fillRect/></a:stretch>'
        f'</pic:blipFill><pic:spPr bwMode="auto">{shape}</pic:spPr></pic:pic></a:graphicData>'
        "</a:graphic></wp:inline></w:drawing></w:r></w:p>"
    )


# Pictures whose effect extent stands where its space may not be free: a narrow cell of fixed
# width, and a frame of exact height.
PLACED: dict[str, str] = {
    "extent-cell-fixed": (
        '<w:tbl><w:tblPr><w:tblW w:w="1200" w:type="dxa"/><w:tblLayout w:type="fixed"/></w:tblPr>'
        '<w:tblGrid><w:gridCol w:w="1200"/></w:tblGrid><w:tr><w:tc><w:tcPr>'
        '<w:tcW w:w="1200" w:type="dxa"/></w:tcPr>'
        + picture((952500, 0, 0, 0), "", SHAPE + UNFILLED)
        + "</w:tc></w:tr></w:tbl><w:p/>"
    ),
    "extent-cell-wide": (
        '<w:tbl><w:tblPr><w:tblW w:w="8000" w:type="dxa"/></w:tblPr>'
        '<w:tblGrid><w:gridCol w:w="8000"/></w:tblGrid><w:tr><w:tc><w:tcPr>'
        '<w:tcW w:w="8000" w:type="dxa"/></w:tcPr>'
        + picture((95250, 0, 0, 0), "", SHAPE + UNFILLED)
        + "</w:tc></w:tr></w:tbl><w:p/>"
    ),
    "extent-frame-exact": picture(
        (0, 476250, 0, 476250),
        "",
        SHAPE + UNFILLED,
        '<w:framePr w:w="2000" w:h="300" w:hRule="exact" w:hSpace="0" w:wrap="around" '
        'w:vAnchor="text" w:hAnchor="text" w:x="0" w:y="0"/>',
    ),
}


# --- rows ----------------------------------------------------------------------------------


def row(content: str, props: str = "", index: int = 0) -> str:
    """One row: a paragraph on its exact line, after a page break where a page is full."""
    brk = "<w:pageBreakBefore/>" if index and index % PAGE_ROWS == 0 else ""
    spacing = f'<w:spacing w:before="0" w:after="0" w:line="{int(LINE * 20)}" w:lineRule="exact"/>'
    # In the schema's order: a style, the break, then the rest (a list, shading, tab stops).
    style, rest = (props, "") if props.startswith("<w:pStyle") else ("", props)
    return f"<w:p><w:pPr>{style}{brk}{rest}{spacing}</w:pPr>{content}</w:p>"


def rows(entries: list[tuple[str, str]]) -> str:
    """Each (content, paragraph properties) as a row."""
    return "".join(row(content, props, i) for i, (content, props) in enumerate(entries))


def shaded(shd: str) -> str:
    """A run of no-break spaces under ``shd``'s attributes."""
    return f'<w:r><w:rPr><w:shd {shd}/></w:rPr><w:t xml:space="preserve">{NBSP}</w:t></w:r>'


def plain() -> str:
    """A run of no-break spaces, not shaded."""
    return f'<w:r><w:t xml:space="preserve">{NBSP}</w:t></w:r>'


MAPPING = (
    '<w:clrSchemeMapping w:bg1="light1" w:t1="dark1" w:bg2="light2" w:t2="dark2" '
    'w:accent1="accent1" w:accent2="accent2" w:accent3="accent3" w:accent4="accent4" '
    'w:accent5="accent5" w:accent6="accent6" w:hyperlink="hyperlink" '
    'w:followedHyperlink="followedHyperlink"/>'
)
BG1 = 'w:val="clear" w:color="auto" w:themeFill="background1"'
# Each shading row: its name, and the run's shading (or the paragraph's, ``para:``).
SHADINGS: list[tuple[str, str]] = [
    ("none", ""),
    ("bg1", f'{BG1} w:fill="FFFFFF"'),
    ("bg1-stale", f'{BG1} w:fill="FF0000"'),
    ("bg1-shade-D9", f'{BG1} w:fill="D9D9D9" w:themeFillShade="D9"'),
    ("bg1-shade-D9-stale", f'{BG1} w:fill="FFFFFF" w:themeFillShade="D9"'),
    ("bg1-shade-BF", f'{BG1} w:fill="BFBFBF" w:themeFillShade="BF"'),
    (
        "bg1-no-colour",
        'w:val="clear" w:themeFill="background1" w:fill="D9D9D9" w:themeFillShade="D9"',
    ),
    (
        "bg1-colour-black",
        'w:val="clear" w:color="000000" w:themeFill="background1" w:fill="D9D9D9" '
        'w:themeFillShade="D9"',
    ),
    ("bg1-shade-lower", f'{BG1} w:fill="D9D9D9" w:themeFillShade="d9"'),
    ("bg1-tint", f'{BG1} w:fill="FFFFFF" w:themeFillTint="80"'),
    ("bg1-tint-shade", f'{BG1} w:fill="D9D9D9" w:themeFillTint="80" w:themeFillShade="D9"'),
    ("bg1-nil", 'w:val="nil" w:themeFill="background1" w:fill="FFFFFF"'),
    ("nil-fill", 'w:val="nil" w:fill="D9D9D9"'),
    ("nil-fill-colour", 'w:val="nil" w:color="FF0000" w:fill="FF0000"'),
    ("nil-shade", 'w:val="nil" w:fill="D9D9D9" w:themeFillShade="BF"'),
    ("para-nil-fill", 'para:w:val="nil" w:fill="D9D9D9"'),
    ("para-nil-shade", 'para:w:val="nil" w:fill="D9D9D9" w:themeFillShade="BF"'),
    ("light1", 'w:val="clear" w:color="auto" w:themeFill="light1" w:fill="FFFFFF"'),
    ("accent1", 'w:val="clear" w:color="auto" w:themeFill="accent1" w:fill="4F81BD"'),
    ("text1", 'w:val="clear" w:color="auto" w:themeFill="text1" w:fill="000000"'),
    ("para-bg1", f'para:{BG1} w:fill="FF0000"'),
    ("para-bg1-shade-D9", f'para:{BG1} w:fill="FFFFFF" w:themeFillShade="D9"'),
    ("pct15", 'w:val="pct15" w:color="auto" w:fill="auto"'),
    ("pct15-accent2", 'w:val="pct15" w:color="auto" w:themeColor="accent2" w:fill="auto"'),
    (
        "pct15-accent2-shade",
        'w:val="pct15" w:color="auto" w:themeColor="accent2" w:themeShade="BF" w:fill="auto"',
    ),
    ("pct15-text1", 'w:val="pct15" w:color="auto" w:themeColor="text1" w:fill="auto"'),
    ("pct15-fill-bg1", 'w:val="pct15" w:color="auto" w:fill="FFFFFF" w:themeFill="background1"'),
]
SHADES = [f"{value:02X}" for value in range(256)]


def shading_rows(entries: list[tuple[str, str]]) -> str:
    """The shading rows: each a run of no-break spaces, shaded or in a shaded paragraph."""
    out = []
    for _, shd in entries:
        if shd.startswith("para:"):
            out.append((plain(), f"<w:shd {shd[5:]}/>"))
        else:
            out.append((shaded(shd) if shd else plain(), ""))
    return rows(out)


# The tab rows: text before the tab is red, after it blue (``A``, a tab, ``B``).
def _text(text: str, colour: str) -> str:
    return f'<w:r><w:rPr>{colour}</w:rPr><w:t xml:space="preserve">{text}</w:t></w:r>'


TABBED = _text("A", RED) + "<w:r><w:tab/></w:r>" + _text("B", BLUE)


def stop(leader: str | None) -> str:
    """Paragraph properties' one tab stop, left at 3 inches, with ``leader`` or none named."""
    named = "" if leader is None else f' w:leader="{leader}"'
    return f'<w:tabs><w:tab w:val="left"{named} w:pos="4320"/></w:tabs>'


def ptab(leader: str) -> str:
    """A positional tab (its leader is required) to the right margin."""
    return (
        _text("A", RED)
        + f'<w:r><w:ptab w:relativeTo="margin" w:alignment="right" w:leader="{leader}"/></w:r>'
        + _text("B", BLUE)
    )


BAR = '<w:tabs><w:tab w:val="bar" w:pos="360"/></w:tabs>'


def numbered(key: int) -> str:
    """A paragraph's list ``key`` at level 0."""
    return f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{key}"/></w:numPr>'


TAB_STYLES = (
    '<w:style w:type="paragraph" w:styleId="Leader"><w:name w:val="Leader"/>'
    f"<w:pPr>{stop('dot')}</w:pPr></w:style>"
    '<w:style w:type="paragraph" w:styleId="LeaderChild"><w:name w:val="Leader Child"/>'
    '<w:basedOn w:val="Leader"/></w:style>'
)
# List 1: a red label and no suffix, its level's tab stop with a dot leader; list 2: a red label
# followed by a tab, no stops of its own.
TAB_NUMBERING = (
    '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/>'
    '<w:numFmt w:val="decimal"/><w:suff w:val="nothing"/><w:lvlText w:val="%1."/>'
    f'<w:lvlJc w:val="left"/><w:pPr>{stop("dot")}</w:pPr><w:rPr>{RED}</w:rPr></w:lvl>'
    "</w:abstractNum>"
    '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:start w:val="1"/>'
    '<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:lvlJc w:val="left"/>'
    f"<w:rPr>{RED}</w:rPr></w:lvl></w:abstractNum>"
    '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
    '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
)
# Each tab row: its name, its content, its paragraph properties.
TABS: list[tuple[str, str, str]] = [
    ("no-stop", TABBED, ""),
    ("dot", TABBED, stop("dot")),
    ("none", TABBED, stop("none")),
    ("no-leader", TABBED, stop(None)),
    ("underscore", TABBED, stop("underscore")),
    ("hyphen", TABBED, stop("hyphen")),
    ("middle-dot", TABBED, stop("middleDot")),
    ("heavy", TABBED, stop("heavy")),
    ("style", TABBED, '<w:pStyle w:val="Leader"/>'),
    ("based-on", TABBED, '<w:pStyle w:val="LeaderChild"/>'),
    ("level", TABBED, numbered(1)),
    ("label-tab", _text("B", BLUE), numbered(2) + stop("dot")),
    ("label-tab-no-stop", _text("B", BLUE), numbered(2)),
    ("ptab-dot", ptab("dot"), ""),
    ("ptab-none", ptab("none"), ""),
    # A bar stop draws a rule down the line, with a tab or without one.
    ("bar", TABBED, BAR),
    ("bar-no-tab", _text("A", RED) + _text("B", BLUE), BAR),
]
TABLE_STYLE = (
    '<w:style w:type="table" w:styleId="LeaderTable"><w:name w:val="Leader Table"/>'
    f'<w:pPr>{stop("dot")}</w:pPr><w:tblPr><w:tblCellMar><w:top w:w="0" w:type="dxa"/>'
    '<w:bottom w:w="0" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
)


def _table(content: str) -> str:
    return (
        '<w:tbl><w:tblPr><w:tblStyle w:val="LeaderTable"/><w:tblW w:w="8000" w:type="dxa"/>'
        '</w:tblPr><w:tblGrid><w:gridCol w:w="8000"/></w:tblGrid><w:tr><w:tc><w:tcPr>'
        f'<w:tcW w:w="8000" w:type="dxa"/></w:tcPr>{content}</w:tc></w:tr></w:tbl>'
    )


def _conditional(style: str, part: str) -> str:
    """A table style whose conditional part ``part`` has a tab stop with a dot leader."""
    return (
        f'<w:style w:type="table" w:styleId="{style}"><w:name w:val="{style}"/><w:tblPr>'
        '<w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/>'
        f'</w:tblCellMar></w:tblPr><w:tblStylePr w:type="{part}"><w:pPr>{stop("dot")}</w:pPr>'
        "</w:tblStylePr></w:style>"
    )


def _styled_table(style: str, content: str, cell: str = "") -> str:
    """A table of one cell in ``style``, its look the first row's, holding ``content``."""
    return (
        f'<w:tbl><w:tblPr><w:tblStyle w:val="{style}"/><w:tblW w:w="8000" w:type="dxa"/>'
        '<w:tblLook w:val="0020" w:firstRow="1" w:lastRow="0" w:firstColumn="0" '
        'w:lastColumn="0" w:noHBand="0" w:noVBand="0"/></w:tblPr><w:tblGrid>'
        '<w:gridCol w:w="8000"/></w:tblGrid><w:tr><w:tc><w:tcPr><w:tcW w:w="8000" w:type="dxa"/>'
        f"{cell}</w:tcPr>{content}</w:tc></w:tr></w:tbl>"
    )


def _cell(content: str, shd: str = "") -> str:
    """A table of one plain cell, shaded by ``shd``'s attributes, holding ``content``."""
    return (
        '<w:tbl><w:tblPr><w:tblW w:w="8000" w:type="dxa"/><w:tblCellMar><w:top w:w="0" '
        'w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>'
        '<w:gridCol w:w="8000"/></w:tblGrid><w:tr><w:tc><w:tcPr><w:tcW w:w="8000" w:type="dxa"/>'
        f"{f'<w:shd {shd}/>' if shd else ''}</w:tcPr>{content}</w:tc></w:tr></w:tbl>"
    )


GREY = 'w:val="clear" w:color="auto" w:fill="D9D9D9"'
WHITE = 'w:val="clear" w:color="auto" w:fill="FFFFFF"'
SHADE_80 = f'{BG1} w:fill="808080" w:themeFillShade="80"'
SPACES = " " * 10


def _spaces(shd: str) -> str:
    return f'<w:r><w:rPr><w:shd {shd}/></w:rPr><w:t xml:space="preserve">{SPACES}</w:t></w:r>'


# A white run over painted ground, and controls (each row a band; a table's row is one too).
WHITE_ROWS = (
    row(shaded(WHITE), f"<w:shd {GREY}/>", 0)
    + row(shaded(f'{BG1} w:fill="FFFFFF"'), f"<w:shd {GREY}/>", 1)
    + row(plain(), f"<w:shd {GREY}/>", 2)
    + _cell(row(shaded(WHITE)), GREY)
    + row("", "", 4)
    + _cell(row(plain()), GREY)
    + row("", "", 6)
    + row(shaded(WHITE), "", 7)
)
# A theme's shade over spaces that end a paragraph or a cell, and between words.
TRAILING_ROWS = (
    row(_text("A", RED) + _spaces(SHADE_80), "", 0)
    + row(_text("A", RED) + _spaces(SHADE_80) + _text("B", BLUE), "", 1)
    + row(_spaces(SHADE_80), "", 2)
    + _cell(row(_spaces(SHADE_80)))
    + row("", "", 4)
    + row(_text("A", RED) + _spaces('w:val="clear" w:color="auto" w:fill="808080"'), "", 5)
)


def _defaults(ppr: str) -> str:
    return f"<w:docDefaults><w:pPrDefault><w:pPr>{ppr}</w:pPr></w:pPrDefault></w:docDefaults>"


# The QRD template's compatibility options (Word 2013 and later, ``compatibilityMode`` 15), which
# EMA's labels carry: each ``-compat`` case is its case again under them.
_OPTIONS = (
    ("compatibilityMode", "15"),
    ("overrideTableStyleFontSizeAndJustification", "1"),
    ("enableOpenTypeFeatures", "1"),
    ("doNotFlipMirrorIndents", "1"),
    ("differentiateMultirowTableHeaders", "1"),
    ("useWord2013TrackBottomHyphenation", "0"),
)
COMPAT = (
    "<w:compat>"
    + "".join(
        f'<w:compatSetting w:name="{name}" w:uri="http://schemas.microsoft.com/office/word" '
        f'w:val="{value}"/>'
        for name, value in _OPTIONS
    )
    + "</w:compat>"
)


def _cases() -> dict[str, Case]:
    cases: dict[str, Case] = {}
    for name, shape in PICTURES.items():
        cases[f"picture-{name}"] = Case(
            f"An in-line picture: {name}.", picture(*shape), picture=True
        )
    for name, body in PLACED.items():
        cases[f"picture-{name}"] = Case(f"An in-line picture: {name}.", body, picture=True)
    window = '<a:sysClr val="window" lastClr="FFFFFF"/>'
    for name, lt1, settings, entries in (
        ("shading", window, MAPPING, SHADINGS),
        ("shading-srgb-white", '<a:srgbClr val="FFFFFF"/>', MAPPING, SHADINGS),
        ("shading-red", '<a:srgbClr val="FF0000"/>', MAPPING, SHADINGS),
        ("shading-unmapped", window, "", SHADINGS),
        (
            "shading-shades",
            window,
            MAPPING,
            [(s, f'{BG1} w:fill="FF0000" w:themeFillShade="{s}"') for s in SHADES],
        ),
    ):
        cases[name] = Case(
            f"Theme and pattern shadings, the theme's lt1 {lt1}.",
            shading_rows(entries),
            settings=settings,
            theme=lt1,
            rows=len(entries),
        )
    cases["shading-white"] = Case(
        "A white run over a grey paragraph and a grey cell, and the same unshaded.",
        WHITE_ROWS,
        settings=MAPPING,
        theme=window,
        rows=8,
    )
    cases["shading-trailing"] = Case(
        "A theme's shade over spaces that end a paragraph or a cell, and between words.",
        TRAILING_ROWS,
        settings=MAPPING,
        theme=window,
        rows=6,
    )
    cases["tabs"] = Case(
        "A tab, a list label's tab, a positional tab and a bar stop, with and without a leader.",
        rows([(content, props) for _, content, props in TABS]),
        styles=TAB_STYLES,
        numbering=TAB_NUMBERING,
        rows=len(TABS),
    )
    cases["tabs-defaults"] = Case(
        "A tab under a leader the document defaults' tab stop gives.",
        rows([(TABBED, "")]),
        styles=_defaults(stop("dot")),
        rows=1,
    )
    cases["tabs-table-style"] = Case(
        "A tab under a leader the table style's tab stop gives.",
        _table(row(TABBED)) + row(""),
        styles=TABLE_STYLE,
        rows=1,
    )
    cases["tabs-conditional"] = Case(
        "A tab under a leader a table style's first-row or whole-table part gives.",
        _styled_table("CondFirst", row(TABBED))
        + row("", "", 1)
        + _styled_table("CondWhole", row(TABBED))
        + row("", "", 3),
        styles=_conditional("CondFirst", "firstRow") + _conditional("CondWhole", "wholeTable"),
        rows=4,
    )
    # Each case again in the QRD template's compatibility mode.
    for name, case in list(cases.items()):
        cases[f"{name}-compat"] = case._replace(
            question=f"{case.question[:-1]}, in the QRD template's compatibility mode.",
            settings=COMPAT + (case.settings or ""),
        )
    return cases


def package(case: Case) -> bytes:
    """``case`` as a complete .docx, stored, with fixed timestamps."""
    parts = {
        "word/document.xml": (
            f"<w:document{NAMESPACES}><w:body>{case.body}{SECTION}</w:body></w:document>"
        ),
        "word/styles.xml": f'<w:styles xmlns:w="{W}">{case.styles}</w:styles>',
    }
    related = [("styles", "styles.xml")]
    types = [("/word/styles.xml", f"{MAIN}.styles+xml")]
    if case.numbering:
        parts["word/numbering.xml"] = f'<w:numbering xmlns:w="{W}">{case.numbering}</w:numbering>'
        related.append(("numbering", "numbering.xml"))
        types.append(("/word/numbering.xml", f"{MAIN}.numbering+xml"))
    if case.settings is not None:
        parts["word/settings.xml"] = f'<w:settings xmlns:w="{W}">{case.settings}</w:settings>'
        related.append(("settings", "settings.xml"))
        types.append(("/word/settings.xml", f"{MAIN}.settings+xml"))
    if case.theme is not None:
        parts["word/theme/theme1.xml"] = _theme(case.theme)
        related.append(("theme", "theme/theme1.xml"))
        types.append(
            ("/word/theme/theme1.xml", "application/vnd.openxmlformats-officedocument.theme+xml")
        )
    declaration = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    parts = {name: declaration + content for name, content in parts.items()}
    parts["[Content_Types].xml"] = (
        declaration + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        + ('<Default Extension="png" ContentType="image/png"/>' if case.picture else "")
        + f'<Override PartName="/word/document.xml" ContentType="{MAIN}.document.main+xml"/>'
        + "".join(f'<Override PartName="{n}" ContentType="{t}"/>' for n, t in types)
        + "</Types>"
    )
    parts["_rels/.rels"] = (
        f'{declaration}<Relationships xmlns="{RELS}"><Relationship Id="rId1" '
        f'Type="{OFFICE}/officeDocument" Target="word/document.xml"/></Relationships>'
    )
    parts["word/_rels/document.xml.rels"] = (
        f'{declaration}<Relationships xmlns="{RELS}">'
        + "".join(
            f'<Relationship Id="rId{i}" Type="{OFFICE}/{kind}" Target="{target}"/>'
            for i, (kind, target) in enumerate(related, 1)
        )
        + (
            f'<Relationship Id="rIdPicture" Type="{OFFICE}/image" Target="media/check.png"/>'
            if case.picture
            else ""
        )
        + "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        contents = {name: content.encode("utf-8") for name, content in parts.items()}
        if case.picture:
            contents["word/media/check.png"] = _checkerboard()
        for name in sorted(contents, key=lambda n: (n != "[Content_Types].xml", n)):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            archive.writestr(info, contents[name])
    return buffer.getvalue()


CASES = _cases()


def wanted() -> dict[Path, bytes]:
    """Every file of the corpus set, with its bytes: one .docx a case, and ``sources.json``."""
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
    note = (
        "Synthetic drawing cases written by scripts/drawing_cases.py; Word's drawing of them is "
        "word-drawn.json (scripts/word_drawn.py)."
    )
    manifest = {"schemaVersion": "1.0.0", "note": note, "sources": sources}
    out[FOLDER / "sources.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    return out


def main() -> int:
    """Write the cases, or with --check report whether they are current."""
    parser = argparse.ArgumentParser(description="Write or check corpus/drawing-cases.")
    parser.add_argument("--check", action="store_true", help="fail rather than write")
    return write(FOLDER, wanted(), parser.parse_args().check)


if __name__ == "__main__":
    raise SystemExit(main())
