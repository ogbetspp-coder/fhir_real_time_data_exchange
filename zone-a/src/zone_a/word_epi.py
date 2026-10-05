r"""A Word label's SmPC as ePI sections: each one's narrative and page text (ADR 0006).

Input: a body the label reader certified (``zone_a.certified.read_body``) and its structure
(``zone_a.structure``), which must be ``ready``. Output: each section that has a heading, in the
template's order (a parent before its children, an empty parent included), with its title (its
heading line), and either its narrative and page text or the reason it is refused. Nothing is
added, reordered or reworded, and a person is never asked to fill a gap: what these rules cannot
carry exactly is refused, section by section.

A section's text is the body paragraphs after its heading up to the next heading of any section,
or the SmPC's end. The paragraphs before the root title (``ANNEX I``) are not in the SmPC.

Two outputs, written by separate code from the same read, so that the fidelity check
(``docs/fidelity-normalization.md``) compares two things, not one:

- ``narrative`` (decision 3): an XHTML div. A paragraph is a ``p``; a run of list paragraphs is a
  ``ul`` or an ``ol``; a table is a ``table`` of ``tr`` and ``td`` with ``colspan`` and
  ``rowspan`` (each merged cell's rows worked out from Word's grid); a line break is ``br``;
  bold, italic, superscript and subscript are ``b``, ``i``, ``sup`` and ``sub``. The div must
  pass the fidelity scanner (``zone_a.fidelity.xhtml``).
- ``page`` (decision 2): the section's text as section 7 of the fidelity specification writes a
  page. It begins with a line break, as the scanner's text does, and each paragraph is a line:
  the list label Word draws and a space (in a table cell a bullet is left out), then the text,
  with a raised or lowered digit or sign written as its superscript or subscript code point; a
  line break inside a paragraph stays a line break (with a tab before a line that starts with a
  bullet glyph, which is then content), and one in a cell is a space. A table is U+FDD0, a line
  per row of Word's grid, read row by row (U+FDD2, then each slot: U+0009 U+FDD3 U+0009 and the
  cell's text where a cell starts, U+0009 U+FDD4 U+0009 where a cell to the left spans it, U+0009
  U+FDD5 U+0009 where a merged cell above covers it), and U+FDD1. Every line ends in U+000A.

What is carried, a closed list. A section is refused on the first paragraph that holds anything
else, with the code in parentheses:

- marks: bold, italic, superscript and subscript (not both at once: ``script``). An underline is
  left out where it cannot change what the text says (``zone_a.underline``, and a hyphen inside
  an underlined word), else (``underline``). The QRD template's own grey over the 4.8 reporting
  statement is left out where the registry names it exactly (same section, same text, same
  range), and so are capitals and small capitals over text that capitals draw the same ("4.");
  any other mark, capitals elsewhere, strike-through, highlight, shading, faint, raised or lowered
  by position, or right-to-left text, is refused (``formatting``);
- raised or lowered text: letters, the digits and signs of the specification's fold tables, and
  other punctuation and symbols that no rule there reads as a number or a sign (categories Po,
  So, Pi, Pf, Pc, Zs), and lowered only, infinity and one half (as fidelity-norm/3.1.0 keeps
  them in ``sub``); anything else (``script``);
- lists: a bullet "•", or an ordinal with a full stop whose labels are exactly the sequence an
  ``ol`` of one type draws (decimal, letters, roman), followed by a tab or a space
  (``list-label``); one list level in the section, since nesting is not carried
  (``list-level``); no paragraph that draws a label and holds no text (``empty-numbered``);
- tables: one level, with Word's grid on record, no grid columns left out at a row's ends, and
  every vertically merged cell under a cell of the same columns that starts or continues the
  merge, with no text of its own (``table-grid``, ``table-shape``, ``nested-table``); a table
  wholly inside one section (``table-across-sections``);
- text: no soft hyphen (``soft-hyphen``), tab (``tab``: Word draws it as a jump to a tab stop),
  picture (``picture``: not yet carried), line or paragraph separator (``line-separator``), or
  line that starts with a bullet glyph after a line break (``bullet-after-break``: section 3 step
  4 would read it as a list bullet);
- no comment (``comment``) and no hidden paragraph mark (``hidden-mark``, the paragraph runs on
  into the next).

A paragraph of only whitespace with no label is drawn as nothing and left out of both; a section
of such paragraphs has no narrative and the empty page. The heading itself is held to the text
rules above and may carry only bold, italic, an underline that cannot change it and capitals over
text they draw the same (``heading-formatting``), since its line is the section's title.

A document is refused whole where Word draws something the read does not say: a floating picture
or shape (``floating-object``, counted by the certificate but not placed), or one of
``Body.layout`` (``floating-table``, ``frame``, ``right-to-left-table``, a ``page-break`` between
two words).
"""

from __future__ import annotations

import dataclasses
import functools
import itertools
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from label_docx.reader import Mark, Paragraph

from zone_a.certified import Body
from zone_a.fidelity.normalize import NormalizationError, normalize_text
from zone_a.fidelity.xhtml import XhtmlError, list_marker, xhtml_to_text
from zone_a.structure import line
from zone_a.underline import is_underline_letter, underline_changes

# The narrative builder's and the page serialiser's version: one, as they are one closed list.
WORD_EPI_VERSION: Final = "word-epi/1.0.0"

CARRIED: Final = {"bold": "b", "italic": "i", "superscript": "sup", "subscript": "sub"}
# Section 3 step 4's bullet glyphs: a list bullet in page text, removed at a line start.
BULLETS: Final = frozenset("\u2022\u2023\u25a0\u25a1\u25aa\u25ab\u25cb\u25cf\u25e6")
# The only bullet an unstyled ``ul`` draws at the top level.
DISC: Final = "\u2022"
# Section 3 step 5's whitespace: drawn as nothing at a paragraph's ends.
WHITESPACE: Final = frozenset(
    "\t\n\r \u00a0\u2000\u2001\u2002\u2003\u2004\u2005\u2007\u2008\u2028\u2029\u3000"
)
# The template's grey, as the registry keeps it (``zone_a.qrd.registry``): highlight or shading.
GREY: Final = frozenset({"highlight-lightGray", "shading-D9D9D9"})
ROOT: Final = '<div xmlns="http://www.w3.org/1999/xhtml" lang="en" xml:lang="en">'


class RefusedError(Exception):
    """What these rules cannot carry exactly. ``paragraph`` indexes the body, where there is one."""

    def __init__(self, code: str, paragraph: int | None, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.paragraph = paragraph
        self.detail = detail


@dataclass(frozen=True)
class _Grey:
    """The registry's own grey over a statement: its section, its text and where."""

    section: str
    text: str
    start: int
    end: int


def _greys(registry: Mapping[str, Any]) -> list[_Grey]:
    out: list[_Grey] = []
    for section in registry["sections"]:
        for item in section["items"]:
            text = "".join(p["value"] for p in item.get("pattern", []) if p["kind"] == "text")
            for mark in item.get("marks", []):
                if mark["kind"] not in GREY:
                    raise ValueError(f"the registry keeps {mark['kind']}, not the template's grey")
                out.append(_Grey(section["key"], text, mark["start"], mark["end"]))
    return out


def _label(paragraph: Paragraph) -> str:
    """The list label Word draws before the paragraph, or ``""``."""
    return (paragraph.numbering.text or "") if paragraph.numbering else ""


def blank(paragraph: Paragraph) -> bool:
    """Drawn as nothing: no label, and only whitespace in the text."""
    return not _label(paragraph) and all(c in WHITESPACE for c in paragraph.text)


# Section 3 step 5's whitespace but the line feed: what step 4 reads past at a line's start.
_INLINE_WHITESPACE: Final = "".join(sorted(WHITESPACE - {"\n"}))
# What may separate a list label from the paragraph: a tab or a space, as an HTML list draws one.
_SUFFIXES: Final = frozenset({"tab", "space"})


def _check(index: int, paragraph: Paragraph) -> None:
    """The refusals of the module docstring that do not depend on marks.

    A paragraph drawn as nothing may hold a tab; its marks are judged as any others are.
    """
    text = paragraph.text.replace("\t", " ") if blank(paragraph) else paragraph.text
    if paragraph.comments:
        raise RefusedError("comment", index, "a comment")
    if paragraph.mark_hidden:
        raise RefusedError("hidden-mark", index, "a hidden paragraph mark")
    if "\u00ad" in text:
        raise RefusedError("soft-hyphen", index, "a soft hyphen")
    if "\t" in text:
        raise RefusedError("tab", index, "a tab")
    if "\ufffc" in text:
        raise RefusedError("picture", index, "a picture")
    if "\u2028" in text or "\u2029" in text:
        # What Word draws for a line or paragraph separator is not on record.
        raise RefusedError("line-separator", index, "U+2028 or U+2029")
    for rest in text.split("\n")[1:]:
        if rest.lstrip(_INLINE_WHITESPACE)[:1] in BULLETS:
            raise RefusedError("bullet-after-break", index, "a bullet glyph after a line break")
    if _label(paragraph) and all(c in WHITESPACE for c in text):
        raise RefusedError("empty-numbered", index, "a label with no text")
    if _label(paragraph) and paragraph.numbering and paragraph.numbering.suffix not in _SUFFIXES:
        # "1." with nothing after it runs into "5 mg": Word draws "1.5 mg".
        raise RefusedError("list-label", index, f"a label followed by {paragraph.numbering.suffix}")


_HYPHENS: Final = frozenset("-\u2010\u2011")
CAPITALS: Final = frozenset({"caps", "smallCaps"})


def unchanged_by_capitals(text: str) -> bool:
    """No character has another form in capitals ("4.", "ABC"): capitals draw it the same.

    Judged by Python's own upper case; "\u00b5" (which Word keeps and Python does not) and "\u00df"
    are changed, and refused.
    """
    return all(character.upper() == character for character in text)


def _underline_changes(text: str, start: int, end: int) -> bool:
    """``zone_a.underline``'s rule, with one more allowance: a hyphen inside an underlined word.

    A hyphen (U+002D, U+2010, U+2011) between two letters, with the line running under the letters
    on both sides ("Long-term" underlined whole), cannot read as "=": it is checked as the gap
    between two underlined pieces. A hyphen at the line's edge, or any other dash, is judged by
    the rule itself, which refuses it.
    """
    cuts = [
        at
        for at in range(start + 1, end - 1)
        if text[at] in _HYPHENS
        and is_underline_letter(text[at - 1])
        and is_underline_letter(text[at + 1])
    ]
    edges = [start, *(x for at in cuts for x in (at, at + 1)), end]
    return any(underline_changes(text, a, b) for a, b in zip(edges[::2], edges[1::2], strict=True))


def _marks(index: int, paragraph: Paragraph, section: str, greys: Sequence[_Grey]) -> list[Mark]:
    """The paragraph's carried marks; refuses one that is neither carried nor left out."""
    out: list[Mark] = []
    for mark in paragraph.marks:
        if mark.kind in CARRIED:
            out.append(mark)
        elif mark.kind == "underline":
            if _underline_changes(paragraph.text, mark.start, mark.end):
                raise RefusedError("underline", index, "an underline that can change the text")
        elif mark.kind in CAPITALS and unchanged_by_capitals(paragraph.text[mark.start : mark.end]):
            pass
        elif not (
            mark.kind in GREY
            and any(
                (section == g.section or section.startswith(g.section + "."))
                and paragraph.text == g.text
                and (mark.start, mark.end) == (g.start, g.end)
                for g in greys
            )
        ):
            raise RefusedError("formatting", index, mark.kind)
    raised = {i for m in out if m.kind == "superscript" for i in range(m.start, m.end)}
    if any(i in raised for m in out if m.kind == "subscript" for i in range(m.start, m.end)):
        raise RefusedError("script", index, "raised and lowered at once")
    return out


def _segments(indices: Sequence[int], body: Body) -> Iterator[tuple[int | None, list[int]]]:
    """The paragraphs in runs: outside a table (``None``), or in one outermost table."""
    run: list[int] = []
    current: int | None = None
    for i in indices:
        where = body.paragraphs[i].table
        top = None if where is None else _top(where[0], body)
        if run and top != current:
            yield current, run
            run = []
        current = top
        run.append(i)
    if run:
        yield current, run


def _top(table: int, body: Body) -> int:
    """The outermost table around ``table``."""
    parent = body.tables[table]["parent"]
    while parent is not None:
        table = parent[0]
        parent = body.tables[table]["parent"]
    return table


@dataclass(frozen=True)
class _Cell:
    column: int
    span: int
    rows: int  # how many rows it spans down
    paragraphs: tuple[int, ...]


def _grid(table: int, members: Sequence[int], body: Body) -> tuple[int, list[list[_Cell]]]:
    """The table's grid width and, row by row, the cells that start in it."""
    entry = body.tables[table]
    first = members[0]
    by_cell: dict[tuple[int, int], list[int]] = {}
    for i in members:
        where = body.paragraphs[i].table
        if where is None or where[0] != table:
            raise RefusedError("nested-table", i, "a table in a table")
        by_cell.setdefault((where[1], where[2]), []).append(i)
    grid = entry["grid"]
    if grid is None:
        raise RefusedError("table-grid", first, f"no grid on record: {entry['reason']}")
    on_grid = {(r, c) for r, row in enumerate(grid["rows"]) for c in range(len(row["cells"]))}
    if set(by_cell) != on_grid:
        raise RefusedError("table-shape", first, "cells and grid differ")
    # A cell as it is built: [column, span, rows, paragraphs]. ``merging`` maps a column to the
    # cell whose vertical merge a cell there may continue.
    starts: list[list[list[Any]]] = []
    merging: dict[int, list[Any]] = {}
    for r, row in enumerate(grid["rows"]):
        if row["before"] or row["after"]:
            raise RefusedError("table-shape", first, "grid columns left out at a row's end")
        here: list[list[Any]] = []
        still: dict[int, list[Any]] = {}
        for c, cell in enumerate(row["cells"]):
            paragraphs = tuple(by_cell[r, c])
            if cell["merge"] == "continue":
                above = merging.get(cell["column"])
                if above is None or above[1] != cell["span"]:
                    raise RefusedError("table-shape", first, "a merge under no cell of its columns")
                if not all(blank(body.paragraphs[i]) for i in paragraphs):
                    raise RefusedError("table-shape", paragraphs[0], "text in a merged cell")
                above[2] += 1
                still[cell["column"]] = above
                continue
            start = [cell["column"], cell["span"], 1, paragraphs]
            here.append(start)
            if cell["merge"] == "restart":
                still[cell["column"]] = start
        merging = still
        starts.append(here)
    return grid["columns"], [[_Cell(*cell) for cell in here] for here in starts]


# ---- narrative ---------------------------------------------------------------------------------


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_NESTING: Final = ("bold", "italic", "superscript", "subscript")  # sup and sub hold no element


def _inline(text: str, marks: Sequence[Mark]) -> str:
    """The text as XHTML: each run of one set of marks in its elements, a line break as br."""
    cuts = sorted({0, len(text), *(m.start for m in marks), *(m.end for m in marks)})
    out: list[str] = []
    for start, end in itertools.pairwise(cuts):
        kinds = [
            k for k in _NESTING if any(m.kind == k and m.start <= start < m.end for m in marks)
        ]
        piece = "<br/>".join(_escape(part) for part in text[start:end].split("\n"))
        for kind in reversed(kinds):
            piece = f"<{CARRIED[kind]}>{piece}</{CARRIED[kind]}>"
        out.append(piece)
    return "".join(out)


@functools.cache
def _ordinals(kind: str) -> dict[str, int]:
    """Every marker an ``ol`` of this type draws (without ". "), and its ordinal."""
    span = range(-999, 10000) if kind == "1" else range(1, 4000)
    return {list_marker(kind, n)[:-2]: n for n in span}


def _list(index: int, labels: Sequence[str]) -> str:
    """The start tag of the list whose drawn markers are exactly ``labels``."""
    if all(label == DISC for label in labels):
        return "<ul>"
    # Every type whose markers these are; several can be (a lone "i." is roman 1 or letter 9),
    # and each draws the same, so the one with the smallest start is taken.
    found = sorted(
        (start, n, kind)
        for n, kind in enumerate(("1", "a", "A", "i", "I"))
        if labels[0].endswith(".")
        and (start := _ordinals(kind).get(labels[0][:-1])) is not None
        and all(list_marker(kind, start + i) == label + " " for i, label in enumerate(labels))
    )
    if found:
        start, _, kind = found[0]
        return (
            "<ol"
            + ("" if kind == "1" else f' type="{kind}"')
            + ("" if start == 1 else f' start="{start}"')
            + ">"
        )
    raise RefusedError("list-label", index, "labels no unstyled HTML list draws")


def _flow(indices: Sequence[int], body: Body, marks: Mapping[int, list[Mark]]) -> str:
    """Paragraphs outside a table, or in one cell, as XHTML blocks."""
    out: list[str] = []
    items: list[int] = []

    def close() -> None:
        if items:
            tag = _list(items[0], [_label(body.paragraphs[i]) for i in items])
            out.append(tag)
            out.extend(f"<li>{_inline(body.paragraphs[i].text, marks[i])}</li>" for i in items)
            out.append("</ul>" if tag == "<ul>" else "</ol>")
            items.clear()

    for i in indices:
        paragraph = body.paragraphs[i]
        if not _label(paragraph):
            close()
            out.append(f"<p>{_inline(paragraph.text, marks[i])}</p>")
            continue
        if items:
            previous = body.paragraphs[items[-1]].numbering
            assert previous is not None  # noqa: S101 - a list item has a label
            assert paragraph.numbering is not None  # noqa: S101
            if previous.num_id != paragraph.numbering.num_id:
                close()
        items.append(i)
    close()
    return "".join(out)


def narrative(indices: Sequence[int], body: Body, marks: Mapping[int, list[Mark]]) -> str:
    """The section's paragraphs as an XHTML div (the module docstring)."""
    out = [ROOT]
    for table, run in _segments(indices, body):
        if table is None:
            out.append(_flow([i for i in run if not blank(body.paragraphs[i])], body, marks))
            continue
        out.append("<table>")
        for row in _grid(table, run, body)[1]:
            out.append("<tr>")
            for cell in row:
                attributes = "" if cell.span == 1 else f' colspan="{cell.span}"'
                attributes += "" if cell.rows == 1 else f' rowspan="{cell.rows}"'
                drawn = [i for i in cell.paragraphs if not blank(body.paragraphs[i])]
                out.append(f"<td{attributes}>{_flow(drawn, body, marks)}</td>")
            out.append("</tr>")
        out.append("</table>")
    out.append("</div>")
    return "".join(out)


# ---- page --------------------------------------------------------------------------------------

# Section 7's fold tables: a raised or lowered digit or sign as its script code point. Written
# here from the specification's text, not taken from the scanner, so the two are compared.
_PLUS: Final = "+\ufe62\uff0b\u2795"
_MINUS: Final = "-\u2212\u2010\u2011\u2012\u2013\u2014\u2015\u02d7\ufe58\ufe63\uff0d\u2796"


def _fold(digits: str, signs: str) -> dict[str, str]:
    table = dict(zip("0123456789", digits, strict=True))
    table.update(dict.fromkeys(_PLUS, signs[0]))
    table.update(dict.fromkeys(_MINUS, signs[1]))
    table.update(zip("=()", signs[2:], strict=True))
    return table


_RAISED: Final = _fold(
    "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079", "\u207a\u207b\u207c\u207d\u207e"
)
_LOWERED: Final = _fold(
    "\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089", "\u208a\u208b\u208c\u208d\u208e"
)
# Kept as they are in raised or lowered text: letters (not the script letters of U+2070 to
# U+209F), and punctuation and symbols no rule reads as a number, a bracket or a sign; lowered
# only, infinity and one half, as fidelity-norm/3.1.0 keeps them in ``sub`` (its lowered-half
# rule, in the scanner, then decides the half).
_KEPT: Final = frozenset({"Po", "So", "Pi", "Pf", "Pc", "Zs"})
_KEPT_LOWERED: Final = frozenset("\u221e\u00bd")


def _script(index: int, character: str, table: Mapping[str, str]) -> str:
    folded = table.get(character)
    if folded is not None:
        return folded
    category = unicodedata.category(character)
    if (
        category in _KEPT
        or (category[0] == "L" and not 0x2070 <= ord(character) <= 0x209F)
        or (table is _LOWERED and character in _KEPT_LOWERED)
    ):
        return character
    raise RefusedError("script", index, f"U+{ord(character):04X} raised or lowered")


def _line(index: int, paragraph: Paragraph, marks: Sequence[Mark], in_cell: bool) -> str:
    """The paragraph as page text: its label and a space, then its text, folded."""
    text = list(paragraph.text)
    for mark in marks:
        table = _RAISED if mark.kind == "superscript" else _LOWERED
        if mark.kind in ("superscript", "subscript"):
            for at in range(mark.start, mark.end):
                text[at] = _script(index, paragraph.text[at], table)
    label = _label(paragraph)
    head = "" if not label or (in_cell and label in BULLETS) else label + " "
    lines = "".join(text).split("\n")
    if in_cell:
        return head + " ".join(lines)
    # A line that goes on after a break and starts with a bullet glyph is no list item.
    return head + "\n".join(
        [lines[0]]
        + ["\t" + x if x.lstrip(_INLINE_WHITESPACE)[:1] in BULLETS else x for x in lines[1:]]
    )


def page(indices: Sequence[int], body: Body, marks: Mapping[int, list[Mark]]) -> str:
    """The section's page text (the module docstring)."""
    out: list[str] = []
    for table, run in _segments(indices, body):
        if table is None:
            out += [
                _line(i, body.paragraphs[i], marks[i], in_cell=False) + "\n"
                for i in run
                if not blank(body.paragraphs[i])
            ]
            continue
        # The slots straight from Word's grid, each row on its own, by code apart from the
        # builder's (which works out each merged cell's rows): the fidelity check then compares
        # the two readings of the grid.
        by_cell: dict[tuple[int, int], list[int]] = {}
        for i in run:
            where = body.paragraphs[i].table
            assert where is not None  # noqa: S101 - a table's run
            by_cell.setdefault((where[1], where[2]), []).append(i)
        out.append("\ufdd0\n")
        for r, row in enumerate(body.tables[table]["grid"]["rows"]):
            slots: list[str] = []
            for c, cell in enumerate(row["cells"]):
                if cell["merge"] == "continue":
                    slots += ["\t\ufdd5\t"] * cell["span"]
                    continue
                text = " ".join(
                    _line(i, body.paragraphs[i], marks[i], in_cell=True)
                    for i in by_cell[r, c]
                    if not blank(body.paragraphs[i])
                )
                slots += ["\t\ufdd3\t" + text] + ["\t\ufdd4\t"] * (cell["span"] - 1)
            out.append("\ufdd2" + "".join(slots) + "\n")
        out.append("\ufdd1\n")
    # Like the scanner's text, a page begins with a line break: the start of a text is no line
    # start (section 3 step 4), and a list bullet on the first line is one.
    return "\n" + "".join(out) if out else ""


# ---- sections ----------------------------------------------------------------------------------


def _section(
    key: str, indices: range, body: Body, greys: Sequence[_Grey]
) -> tuple[str | None, str]:
    """The section's narrative (None where it draws nothing) and page; or RefusedError."""
    paragraphs = body.paragraphs
    for edge, beyond in ((indices.start, indices.start - 1), (indices.stop - 1, indices.stop)):
        if (
            indices
            and 0 <= beyond < len(paragraphs)
            and paragraphs[edge].table is not None
            and paragraphs[beyond].table is not None
            and _top(paragraphs[edge].table[0], body) == _top(paragraphs[beyond].table[0], body)  # type: ignore[index]
        ):
            raise RefusedError("table-across-sections", edge, "a table in two sections")
    marks: dict[int, list[Mark]] = {}
    for i in indices:
        _check(i, paragraphs[i])
        marks[i] = _marks(i, paragraphs[i], key, greys)
    # One list level in a section: a list inside a list would be drawn as one flat list.
    levels = [(paragraphs[i].numbering.level, i) for i in indices if _label(paragraphs[i])]  # type: ignore[union-attr]
    for level, i in levels:
        if level != levels[0][0]:
            raise RefusedError("list-level", i, "lists at two levels")
    if all(blank(paragraphs[i]) for i in indices):
        return None, ""
    div = narrative(indices, body, marks)
    text = page(indices, body, marks)
    try:
        scanned = normalize_text(xhtml_to_text(div))
        paged = normalize_text(text)
    except (XhtmlError, NormalizationError) as error:
        raise RefusedError("narrative", None, error.code) from error
    if scanned != paged:  # a fault in this module, never in the label: refused all the same
        raise RefusedError("page-differs", None, "the narrative does not read as the page")
    return div, text


def _heading(index: int, paragraph: Paragraph) -> None:
    """The heading's own refusals: its line is the section's title, plain text.

    A tab is a gap the title reads as a space; any mark but bold, italic, an underline that cannot
    change the text and capitals over text they draw the same, and a label run into the text,
    would draw the title otherwise.
    """
    _check(index, dataclasses.replace(paragraph, text=paragraph.text.replace("\t", " ")))
    for mark in paragraph.marks:
        text = paragraph.text[mark.start : mark.end]
        if not (
            mark.kind in ("bold", "italic")
            or (mark.kind in CAPITALS and unchanged_by_capitals(text))
            or (
                mark.kind == "underline"
                and not _underline_changes(paragraph.text, mark.start, mark.end)
            )
        ):
            raise RefusedError("heading-formatting", index, mark.kind)


def sections(
    body: Body, structured: Mapping[str, Any], registry: Mapping[str, Any]
) -> dict[str, Any]:
    """Each section with a heading, in the template's order, as JSON values.

    Raises:
        RefusedError: The document has a floating picture or shape (``floating-object``), or
            something else Word draws that the read does not yet say (``Body.layout``: a
            ``floating-table``, a ``frame``, a ``right-to-left-table``, a ``page-break`` between
            two words).
        ValueError: The structure is not ready, or not of this body.
    """
    if not structured["ready"]:
        raise ValueError("the structure is not ready")
    if body.floating:
        raise RefusedError("floating-object", None, f"{body.floating} floating objects")
    if body.layout:
        raise RefusedError(body.layout[0], None, "Word draws it; the read does not yet say how")
    paragraphs = body.paragraphs
    end = len(paragraphs) if structured["end"] is None else structured["end"]
    headings = sorted(s["heading"] for s in structured["sections"] if s["heading"] is not None)
    if headings and headings[-1] >= end:
        raise ValueError("a heading outside the body's SmPC")
    greys = _greys(registry)
    out: list[dict[str, Any]] = []
    for section in structured["sections"]:
        heading = section["heading"]
        if heading is None:
            continue
        stop = next((h for h in headings if h > heading), end)
        indices = range(heading + 1, stop)
        entry: dict[str, Any] = {
            "key": section["key"],
            "parent": section["parent"],
            "code": section["code"],
            "title": line(paragraphs[heading]),
            "heading": heading,
            "paragraphs": [indices.start, indices.stop],
            "narrative": None,
            "page": None,
            "refusal": None,
        }
        try:
            if paragraphs[heading].table is not None:
                raise RefusedError("heading-in-table", heading, "a heading in a table")
            _heading(heading, paragraphs[heading])
            entry["narrative"], entry["page"] = _section(section["key"], indices, body, greys)
        except RefusedError as refused:
            entry["refusal"] = {
                "code": refused.code,
                "paragraph": refused.paragraph,
                "detail": refused.detail,
            }
        out.append(entry)
    return {
        "builder": WORD_EPI_VERSION,
        "structurer": structured["structurer"],
        "registryVersion": registry["registryVersion"],
        "sections": out,
        "refused": sum(1 for entry in out if entry["refusal"] is not None),
    }
