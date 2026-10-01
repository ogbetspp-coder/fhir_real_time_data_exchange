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
    footnotes: str = ""
    endnotes: str = ""
    settings: str = ""
    final: str = "<w:sectPr/>"


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


def seq(stored: str, switches: str = "", identifier: str = "Table") -> str:
    """A caption: "Table " and a SEQ field."""
    code = f"SEQ {identifier} {switches}".strip().replace("\\", BACKSLASH)
    return para(words(f"{identifier} "), field(code, stored))


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
    "fields-stale": Case(
        "Captions stored as 7 and 7, which Word prints as 1 and 2 (the reader refuses).",
        "",
        seq("7") + seq("7"),
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
    roots = {"footnotes": "w:footnotes", "endnotes": "w:endnotes", "settings": "w:settings"}
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/word/document.xml" ContentType="{MAIN}.document.main+xml"/>'
            f'<Override PartName="/word/styles.xml" ContentType="{MAIN}.styles+xml"/>'
            f'<Override PartName="/word/numbering.xml" ContentType="{MAIN}.numbering+xml"/>'
            + "".join(
                f'<Override PartName="/word/{name}.xml" ContentType="{MAIN}.{name}+xml"/>'
                for name, content in extra
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
            + "</Relationships>"
        ),
        "word/document.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W}">'
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
