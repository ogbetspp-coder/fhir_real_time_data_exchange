"""The reader's result as canonical JSON: the same input gives the same bytes, everywhere.

The document is an object with sorted keys, no insignificant whitespace, UTF-8 with no ASCII
escaping, and a final newline. Every value is a string, an integer, a boolean, null, an array or
an object; there are no floating-point numbers. For such values this is the JSON Canonicalization
Scheme (RFC 8785): keys are sorted by UTF-16 code units, and Python escapes exactly the
characters JCS escapes, in the same form. ``tests/test_output.py`` holds it to that.

Offsets (``marks[].start`` and ``end``) count Unicode code points of ``text``, not UTF-16 code
units or bytes: a consumer in JavaScript must index by code point.

A paragraph (``paragraph``; ``reader.Paragraph`` says what each means): ``text``, ``marks``
(``start``, ``end``, ``kind``), ``style`` (the style id as written, or null), ``markHidden``,
``table`` (``[table, row, cell]``, or null; see ``tables``), ``numbering`` (null, or ``numId``,
``level``, ``text`` and ``suffix``; ``level`` is Word's ``ilvl``, from 0), and ``notes``,
``pages``, ``comments``, ``pictures`` and ``anchored`` as below.

Each paragraph's ``pictures`` (a .docx's, in the body, notes, headers, footers and comments)
says what each U+FFFC of its ``text`` stands for (``reader.Picture``; "Pictures" in the
reader's docstring), in order: ``offset`` (where it stands in ``text``), ``kind`` (``picture``
or ``shape``), ``part`` (the image part's name, or null), ``sha256`` (of the part's bytes),
``type`` (``png`` or ``jpeg`` by the bytes' signature, or null), ``pixels`` (``[width,
height]`` from the image's header, or null), ``extent`` (``[cx, cy]`` in EMU from
``wp:extent``, or null), ``crop`` (``a:srcRect``'s ``l``, ``t``, ``r`` and ``b`` in thousandths
of a percent, or null where there is none) and ``reason``: the first of
``reader.PICTURE_REASONS`` found against it (``shape``, ``vml``, ``field``, ``linked``,
``no-part``, ``not-png-or-jpeg``, ``bad-image-header``, ``colour``, ``animated``,
``orientation``, ``bad-number``, ``cropped``, ``rotated``, ``flipped``, ``line-height``,
``row-height``, ``border``, ``effects``), or null where none is. Null does not say Word draws
these bytes so: it says nothing on the reader's closed list was found ("Pictures" in its
docstring), a list that rests on what is known of Word, not on Word's drawing; the builder and
the browser hold the rest. The image's bytes are not in the result: a consumer reads ``part``
from the source and holds it to ``sha256``. Whether Word draws it larger than its ``pixels``
(9525 EMU a pixel at 96 dpi) is the consumer's to judge. No picture refuses a read.

Each .docx paragraph's ``anchored`` places each object anchored to it, which Word draws apart
from the text (``reader.Anchored``; "Anchored" in the reader's docstring), in order: ``offset``
(where its anchor stands in ``text``), ``kind`` (``picture`` or ``shape``, holding no text;
``text-box``, or ``shapes`` for a group or canvas, holding text) and ``read`` (whether its own
text is read: always false yet). An object's text is in no paragraph; the certificate counts it.

A read::

    {"certificate": {...}, "comments": [...], "endnotes": [...], "footers": [...],
     "footnotes": [...], "format": ..., "headers": [...], "paragraphs": [...], "reader": ...,
     "refusedParts": n, "source": {"bytes": n, "sha256": hex}, "tables": [...]}

``tables`` lists each body table (``reader.Table``; "Tables" in the reader's docstring), in
document order, a nested table as its own entry after the table holding it: ``parent`` (the
``[table, row, cell]`` a nested table stands in, else null), ``grid`` and ``reason``. ``grid`` is
``columns`` (its ``gridCol`` count) and ``rows``, each with ``before`` and ``after`` (grid columns
left out, ``gridBefore`` and ``gridAfter``) and ``cells``, each with ``column`` (the first grid
column it covers, from 0), ``span`` (``gridSpan``) and ``merge`` (``vMerge`` as stored: null,
``restart`` or ``continue``), and ``exactHeight`` (whether the row's height may be exact: its own
``trHeight`` ``hRule="exact"``, or one its table's style sets); ``reason`` is then null. Where
Word's grid is not on record, ``grid`` is null and ``reason`` one of ``reader.REASONS``:
``no-grid``, ``two-grids``, ``bad-number``,
``h-merge``, ``bad-merge``, ``bad-span``, ``row-off-grid``. The text is read either way. A body
paragraph's ``table`` is ``[table, row, cell]`` of its own table (a nested table's, not the
outermost's), ``cell`` counting the row's ``<w:tc>`` cells, not grid columns. Tables in notes,
headers, footers and comments are not listed; their paragraphs' ``table`` is the outermost
table's cell, tables counted in that story.

``headers`` and ``footers`` list each header or footer part the sections refer to, once, in the
order referred to: its ``part`` name, its ``uses`` (each ``section``, counted from 0, and the
reference's ``type``: ``default``, ``first`` or ``even``; which one Word shows on a page is
layout) and its ``paragraphs``. ``comments`` lists each comment as stored, with its ``id``,
``author``, ``initials`` and ``date`` as written (null where absent) and its ``paragraphs``. A
header, footer or comment the reader cannot read exactly is refused on its own: its ``refusal``
gives the code and detail and it has no paragraphs, the rest is read, and ``refusedParts``
counts them (the receipt then says ``read-in-part``). A header or footer no section shows (a
first page's own where no section has a different first page, an even pages' own where the
settings do not set them apart) has the ``refusal`` code ``never-shown`` and no paragraphs: Word
never draws it, so it is not refused, and ``refusedParts`` does not count it;
each paragraph's ``comments`` gives where a comment's mark stands (``offset``) and which comment
it is (``id``).

Each paragraph's ``notes`` lists its footnote and endnote marks (``offset``, ``kind``, ``id``,
``mark``); ``footnotes`` and ``endnotes`` list the notes in the order the body refers to them,
each with its ``id``, ``mark`` and ``paragraphs``. Each paragraph's ``pages`` lists the offsets
where Word draws a page number (a table of contents' PAGEREF, a PAGE field): Word sets it from
the page layout when it prints, so its value is never in ``text`` and never known.

``certificate`` is the independent conservation check's account of the read
(``label_docx.certify``): how many characters the source's text holds, how many the output
holds, and how many were set aside and why (field code, page numbers, page breaks, hidden
whitespace, ``floatingObjects``: every object anchored to a paragraph, one each; and of those,
``unreadObjects``, the ones holding text, with ``unreadObjectCharacters``, their text's
characters in every branch), with the parts holding text the reader does not read. The check found
every character of the output in the source, in order, and every source character in the output or
set aside; a read it cannot account for is refused as ``uncertified``.

A document with tracked changes::

    {"certificate": {"views": {...}, "accepted": {...}, "original": {...}}, "format": ...,
     "reader": ..., "source": {...},
     "tracked": {"accepted": {...}, "original": {...}, "changes": [...]}}

``tracked.accepted`` is the document with every change accepted, ``tracked.original`` with every
change rejected, each with ``paragraphs``, the notes, headers, footers, comments,
``refusedParts`` and ``tables`` as a read above; there is no ``paragraphs`` outside them, so the
caller names the view it takes. ``changes`` lists each change as stored: ``part``, ``kind``
(``insert``, ``delete``, ``move-from``, ``move-to``, each also with ``-paragraph-mark``;
``insert-row``, ``delete-row``; ``format``, ``format-paragraph``, ``format-table``,
``format-row``, ``format-cell``, ``format-section``), ``id``, ``author`` and ``date`` (null where
absent).
Each view is certified as a read is; ``certificate.views`` is the check's account of the views
themselves (``certify_tracked``). A document with a change inside a drawing, whose views are
read, is refused (``tracked-change``): the change is listed but in neither view's text.

A refusal::

    {"format": ..., "reader": ..., "refusal": {"code": ..., "detail": ...}, "source": {...}}
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

from label_docx.certify import DocxSource, certify_tracked
from label_docx.reader import (
    READER_VERSION,
    Comment,
    Document,
    DocxRefusedError,
    Note,
    Paragraph,
    Story,
    Table,
    changed_drawing,
    read_document,
    tracked,
)

# The version of the shape above, and of the check that certifies it: versions.lock.json ties
# it to both files (tests/test_locks.py).
FORMAT_VERSION = "label-docx-json/1.20.0"

type Json = str | int | bool | list[Json] | dict[str, Json] | None


def canonical(value: Json) -> bytes:
    """``value`` as canonical JSON bytes, with a final newline."""
    text = json.dumps(_jcs_order(value), ensure_ascii=False, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


def _jcs_order(value: Json) -> Json:
    """``value`` with every object's keys in RFC 8785 order: by UTF-16 code units.

    Code-point order differs from it only for a key outside the BMP against one from U+E000 up;
    part names in ``notRead`` can be any Unicode.
    """
    if isinstance(value, dict):
        return {k: _jcs_order(value[k]) for k in sorted(value, key=lambda k: k.encode("utf-16-be"))}
    if isinstance(value, list):
        return [_jcs_order(item) for item in value]
    return value


def paragraph(item: Paragraph) -> dict[str, Json]:
    """One paragraph as JSON."""
    return {
        "comments": [{"id": c.id, "offset": c.offset} for c in item.comments],
        "markHidden": item.mark_hidden,
        "marks": [{"end": m.end, "kind": m.kind, "start": m.start} for m in item.marks],
        "notes": [
            {"id": n.id, "kind": n.kind, "mark": n.mark, "offset": n.offset} for n in item.notes
        ],
        "pages": list(item.pages),
        "numbering": (
            None
            if item.numbering is None
            else {
                "level": item.numbering.level,
                "numId": item.numbering.num_id,
                "suffix": item.numbering.suffix,
                "text": item.numbering.text,
            }
        ),
        "style": item.style,
        "table": None if item.table is None else list(item.table),
        "text": item.text,
    }


def paragraphs(items: list[Paragraph]) -> list[Json]:
    """Every paragraph as JSON, in document order."""
    return [paragraph(item) for item in items]


def docx_paragraphs(items: list[Paragraph]) -> list[Json]:
    """A .docx's paragraphs as JSON, in document order, each with ``pictures`` and ``anchored``."""
    return [
        {
            **paragraph(item),
            "anchored": [
                {"kind": a.kind, "offset": a.offset, "read": a.read} for a in item.anchored
            ],
            "pictures": [
                {
                    "crop": None if p.crop is None else dict(zip("ltrb", p.crop, strict=True)),
                    "extent": None if p.extent is None else list(p.extent),
                    "kind": p.kind,
                    "offset": p.offset,
                    "part": p.part,
                    "pixels": None if p.pixels is None else list(p.pixels),
                    "reason": p.reason,
                    "sha256": p.sha256,
                    "type": p.type,
                }
                for p in item.pictures
            ],
        }
        for item in items
    ]


def notes(items: tuple[Note, ...]) -> list[Json]:
    """Footnotes or endnotes as JSON, in the order the body refers to them."""
    return [
        {"id": n.id, "mark": n.mark, "paragraphs": docx_paragraphs(list(n.paragraphs))}
        for n in items
    ]


def _refusal(refusal: tuple[str, str] | None) -> Json:
    return None if refusal is None else {"code": refusal[0], "detail": refusal[1]}


def stories(items: tuple[Story, ...]) -> list[Json]:
    """Headers or footers as JSON, in the order the sections refer to them."""
    return [
        {
            "part": s.part,
            "uses": [{"section": section, "type": kind} for section, kind in s.uses],
            "paragraphs": docx_paragraphs(list(s.paragraphs)),
            "refusal": _refusal(s.refusal),
        }
        for s in items
    ]


def comments(items: tuple[Comment, ...]) -> list[Json]:
    """Comments as JSON, in the order stored."""
    return [
        {
            "author": c.author,
            "date": c.date,
            "id": c.id,
            "initials": c.initials,
            "paragraphs": docx_paragraphs(list(c.paragraphs)),
            "refusal": _refusal(c.refusal),
        }
        for c in items
    ]


def read(data: bytes) -> tuple[bytes, bool]:
    """The canonical JSON result of reading ``data``, and whether it was read (not refused)."""
    source: dict[str, Json] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    envelope: dict[str, Json] = {
        "format": FORMAT_VERSION,
        "reader": READER_VERSION,
        "source": source,
    }
    try:
        document = read_document(data)
    except DocxRefusedError as refused:
        if refused.code == "tracked-change":
            return _tracked(data, envelope)
        envelope["refusal"] = {"code": refused.code, "detail": refused.detail}
        return canonical(envelope), False
    parts: tuple[Story | Comment, ...] = (*document.headers, *document.footers, *document.comments)
    if any(item.refusal is not None and item.refusal[0] == "tracked-change" for item in parts):
        return _tracked(data, envelope)
    envelope.update(content(document))
    return certified(envelope, lambda: DocxSource(data).certify(envelope))


def tables(items: tuple[Table, ...]) -> list[Json]:
    """The body's tables as JSON: each one's grid, or why it has none, in document order."""
    return [
        {
            "grid": None
            if t.grid is None
            else {
                "columns": t.grid.columns,
                "rows": [
                    {
                        "after": r.after,
                        "before": r.before,
                        "cells": [
                            {"column": c.column, "merge": c.merge, "span": c.span} for c in r.cells
                        ],
                        "exactHeight": r.exact,
                    }
                    for r in t.grid.rows
                ],
            },
            "parent": None if t.parent is None else list(t.parent),
            "reason": t.reason,
        }
        for t in items
    ]


def content(document: Document) -> dict[str, Json]:
    """A document's text as JSON: paragraphs, notes, headers, footers, comments; body tables."""
    return {
        "tables": tables(document.tables),
        "paragraphs": docx_paragraphs(list(document.body)),
        "footnotes": notes(document.footnotes),
        "endnotes": notes(document.endnotes),
        "headers": stories(document.headers),
        "footers": stories(document.footers),
        "comments": comments(document.comments),
        "refusedParts": sum(
            1
            for item in (*document.headers, *document.footers, *document.comments)
            if item.refusal and item.refusal[0] != "never-shown"
        ),
    }


def _tracked(data: bytes, envelope: dict[str, Json]) -> tuple[bytes, bool]:
    """A document with tracked changes: both its views, read and certified, and the changes."""
    try:
        accepted, original, changes = tracked(data)
        views = {"accepted": accepted, "original": original}
        texts: dict[str, dict[str, Json]] = {}
        for view, view_data in views.items():
            try:
                texts[view] = content(read_document(view_data))
            except DocxRefusedError as refused:
                raise DocxRefusedError(refused.code, f"{view} view: {refused.detail}") from refused
        changed = changed_drawing(data)
        if changed is not None:
            raise DocxRefusedError("tracked-change", f"a change inside a drawing in {changed}")
    except DocxRefusedError as refused:
        envelope["refusal"] = {"code": refused.code, "detail": refused.detail}
        return canonical(envelope), False
    envelope["tracked"] = {
        "changes": [
            {"author": c.author, "date": c.date, "id": c.id, "kind": c.kind, "part": c.part}
            for c in changes
        ],
        **texts,
    }
    return certified(
        envelope,
        lambda: {
            "views": certify_tracked(data, views),
            **{v: DocxSource(views[v]).certify(texts[v]) for v in views},
        },
    )


def certified(
    envelope: dict[str, Json], certify: Callable[[], dict[str, Json]]
) -> tuple[bytes, bool]:
    """``envelope`` with its certificate, or refused as ``uncertified`` if the check fails.

    The check (``label_docx.certify``) reads the source again on its own; a result it cannot
    account for character by character is never served, whatever the reason.
    """
    try:
        envelope["certificate"] = certify()
    except Exception as error:  # noqa: BLE001 - any failure of the check is a refusal
        refused = {k: envelope[k] for k in ("format", "reader", "source")}
        refused["refusal"] = {"code": "uncertified", "detail": str(error) or type(error).__name__}
        return canonical(refused), False
    return canonical(envelope), True
