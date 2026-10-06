"""The drawing check of ADR 0006 decision 1: what Chrome draws for each narrative, held to the read.

Each carried section's narrative (``zone_a.word_epi``) is drawn by headless Chrome
(``label_docx.browser``, the reader's own Chrome oracle) and compared with the section's
paragraphs as the label reader read them, which is what Word draws (the reader's Word oracle):
the text of each line, the marks of each character, the list markers in order, and each
picture's size as Chrome decoded it against the size the reader read from its header (a picture
Chrome cannot decode is drawn broken, and differs). A table's
cells are lines like any other, in reading order (Chrome's tab between cells is a line's end);
where a cell stands in the grid is the fidelity check's to prove, from the page.

Both sides are compared as a reader of the page sees them. Spaces, tabs and line feeds collapse
to one space and lines are trimmed, as a browser lays out text and as Word's extra spaces read;
empty lines are none. Marks are compared on the characters that are not whitespace. On the
read's side, the marks the narrative leaves out by its closed list (an underline, capitals over
what they draw the same) are left out here too, so this check is no second guard of those rules;
any other mark the narrative did not carry differs. The template's grey is read as the silver
background the narrative draws it with (``DRAWN_GREY``), so a grey character Chrome draws on any
other background, or a character drawn grey that Word does not shade, differs. A list label the
narrative writes as text (``zone_a.word_epi.text_labels``) is read as the start of its line, not
as a marker.

Not compared: a list's indentation and nesting (the builder refuses two levels in a section), and
which line a bullet stands before (bullets are compared in order, and section 3 step 4 removes
them from both texts the fidelity check reads).

``check`` gives the verdicts; ``refuse`` turns a section that differs, or one Chrome did not draw,
into a refusal (``drawn-otherwise``, ``not-drawn``). Chrome is not a dependency of Zone A; where it
is not installed, ``check`` raises ``BrowserError``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from label_docx import browser
from label_docx.reader import Paragraph

from zone_a.certified import Body
from zone_a.word_epi import CAPITALS, GREY, WHITESPACE, blank, text_labels, unchanged_by_capitals

DRAWING_VERSION: Final = "word-drawing/1.1.1"
LEFT_OUT: Final = frozenset({"underline"})
# The template's grey as Chrome reports the narrative's silver span (``label_docx.browser``).
DRAWN_GREY: Final = "shading-#c0c0c0"
_SPACE: Final = re.compile(r"[ \t\r\f]+|[^ \t\r\f]")

Line = tuple[str, tuple[frozenset[str], ...]]


def _shown(characters: Sequence[tuple[str, frozenset[str]]]) -> list[Line]:
    """Characters in lines, as a page shows them: whitespace collapsed, lines trimmed."""
    lines: list[Line] = []
    text = "".join(c for c, _ in characters)
    at = 0
    for piece in text.split("\n"):
        kinds = [k for _, k in characters[at : at + len(piece)]]
        at += len(piece) + 1
        out: list[tuple[str, frozenset[str]]] = []
        for match in _SPACE.finditer(piece):
            if match.group().strip(" \t\r\f"):
                out.append((match.group(), kinds[match.start()]))
            elif out:
                out.append((" ", frozenset()))
        while out and out[-1][0] == " ":
            out.pop()
        if out:
            lines.append(("".join(c for c, _ in out), tuple(k for _, k in out)))
    return lines


def _without_whitespace_marks(lines: Sequence[Line]) -> list[Line]:
    return [
        (
            text,
            tuple(frozenset() if c in WHITESPACE else k for c, k in zip(text, kinds, strict=True)),
        )
        for text, kinds in lines
    ]


def read_lines(paragraphs: Sequence[Paragraph], heads: Sequence[str] = ()) -> list[Line]:
    """The paragraphs' lines and marks, as the narrative must draw them.

    ``heads`` gives each paragraph the text the narrative writes before it, unmarked: its list
    label and a space where the label is written as text, else nothing (all nothing by default).
    """
    characters: list[tuple[str, frozenset[str]]] = []
    for paragraph, head in zip(paragraphs, heads or [""] * len(paragraphs), strict=True):
        kinds: list[set[str]] = [set() for _ in paragraph.text]
        for mark in paragraph.marks:
            if mark.kind not in LEFT_OUT:
                for at in range(mark.start, mark.end):
                    # Capitals over a character they draw the same are no mark on it.
                    if not (mark.kind in CAPITALS and unchanged_by_capitals(paragraph.text[at])):
                        kinds[at].add(DRAWN_GREY if mark.kind in GREY else mark.kind)
        characters += [(c, frozenset[str]()) for c in head]
        characters += [(c, frozenset(k)) for c, k in zip(paragraph.text, kinds, strict=True)]
        characters.append(("\n", frozenset()))
    return _without_whitespace_marks(_shown(characters))


def drawn_lines(section: dict[str, Any]) -> list[Line]:
    """Chrome's lines and marks for a section, seen the same way."""
    characters: list[tuple[str, frozenset[str]]] = []
    for text, kinds in browser.browser_lines(section):
        characters += list(zip(text, kinds, strict=True))
        characters.append(("\n", frozenset()))
    return _without_whitespace_marks(_shown(characters))


def read_markers(paragraphs: Sequence[Paragraph]) -> list[str]:
    """The list label each paragraph draws, with the space after it, in order."""
    return [p.numbering.text + " " for p in paragraphs if p.numbering and p.numbering.text]


def _label_text(paragraph: Paragraph) -> str:
    """A list label the narrative writes as text, and the space after it."""
    assert paragraph.numbering is not None  # noqa: S101 - text_labels names labelled paragraphs
    return f"{paragraph.numbering.text} "


def read_pictures(paragraphs: Sequence[Paragraph]) -> list[list[int]]:
    """Each picture's size in pixels as the reader read its header, in order."""
    return [
        list(picture.pixels or (0, 0))
        for paragraph in paragraphs
        for picture in sorted(paragraph.pictures, key=lambda picture: picture.offset)
    ]


def check(body: Body, built: Mapping[str, Any], chrome: Path = browser.CHROME) -> dict[str, Any]:
    """Each carried section with a narrative: whether Chrome draws it as read, or where not.

    ``built`` is ``zone_a.word_epi.sections``'s result for ``body``. The result names this check's
    version and Chrome's, and gives each section's ``key``, ``agrees`` and ``where`` (a line and
    character, or the list markers, never the text).
    """
    carried = [s for s in built["sections"] if s["refusal"] is None and s["narrative"]]
    divs = [s["narrative"] for s in carried]
    shown = browser.browser_sections(divs, chrome) if divs else []
    markers = browser.browser_markers(divs, chrome) if divs else []
    verdicts: list[dict[str, Any]] = []
    for section, drawn, drawn_markers in zip(carried, shown, markers, strict=True):
        start, stop = section["paragraphs"]
        indices = [i for i in range(start, stop) if not blank(body.paragraphs[i])]
        paragraphs = [body.paragraphs[i] for i in indices]
        as_text = text_labels(range(start, stop), body)
        heads = [_label_text(body.paragraphs[i]) if i in as_text else "" for i in indices]
        where: str | None = None
        if drawn["error"] is not None:
            where = drawn["error"]
        else:
            mine, theirs = read_lines(paragraphs, heads), drawn_lines(drawn)
            if mine != theirs:
                where = browser.first_difference(theirs, mine)
            elif read_markers([body.paragraphs[i] for i in indices if i not in as_text]) != (
                drawn_markers
            ):
                where = "list markers differ"
            elif read_pictures(paragraphs) != [list(size) for size in drawn.get("pictures", [])]:
                where = "a picture Chrome did not decode to the size the reader read"
        verdicts.append({"key": section["key"], "agrees": where is None, "where": where})
    return {
        "checker": DRAWING_VERSION,
        "application": browser.chrome_version(chrome),
        "sections": verdicts,
    }


def refuse(built: Mapping[str, Any], verdict: Mapping[str, Any] | None) -> dict[str, Any]:
    """``built`` with each carried narrative that Chrome did not draw as read refused.

    ``verdict`` is ``check``'s for ``built``, or None where Chrome did not draw it: then every
    carried narrative is refused (``not-drawn``), since no narrative is carried undrawn.
    """
    agrees = {} if verdict is None else {v["key"]: v for v in verdict["sections"]}
    sections: list[dict[str, Any]] = []
    for section in built["sections"]:
        out = dict(section)
        if section["refusal"] is None and section["narrative"]:
            seen = agrees.get(section["key"])
            if seen is None:
                out["refusal"] = {"code": "not-drawn", "paragraph": None, "detail": "not drawn"}
            elif not seen["agrees"]:
                out["refusal"] = {
                    "code": "drawn-otherwise",
                    "paragraph": None,
                    "detail": seen["where"],
                }
            if out["refusal"] is not None:
                out["narrative"], out["page"] = None, None
        sections.append(out)
    return {
        **built,
        "drawing": None if verdict is None else {k: verdict[k] for k in ("checker", "application")},
        "sections": sections,
        "refused": sum(1 for s in sections if s["refusal"] is not None),
    }
