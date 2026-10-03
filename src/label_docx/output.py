"""The reader's result as canonical JSON: the same input gives the same bytes, everywhere.

The document is an object with sorted keys, no insignificant whitespace, UTF-8 with no ASCII
escaping, and a final newline. Every value is a string, an integer, a boolean, null, an array or
an object; there are no floating-point numbers. For such values this is the JSON Canonicalization
Scheme (RFC 8785): keys are ASCII, so code-point and UTF-16 key order agree, and Python escapes
exactly the characters JCS escapes, in the same form. ``tests/test_output.py`` holds it to that.

Offsets (``marks[].start`` and ``end``) count Unicode code points of ``text``, not UTF-16 code
units or bytes: a consumer in JavaScript must index by code point.

A read::

    {"certificate": {...}, "comments": [...], "endnotes": [...], "footers": [...],
     "footnotes": [...], "format": ..., "headers": [...], "paragraphs": [...], "reader": ...,
     "refusedParts": n, "source": {"bytes": n, "sha256": hex}}

``headers`` and ``footers`` list each header or footer part the sections refer to, once, in the
order referred to: its ``part`` name, its ``uses`` (each ``section``, counted from 0, and the
reference's ``type``: ``default``, ``first`` or ``even``; which one Word shows on a page is
layout) and its ``paragraphs``. ``comments`` lists each comment as stored, with its ``id``,
``author``, ``initials`` and ``date`` as written (null where absent) and its ``paragraphs``. A
header, footer or comment the reader cannot read exactly is refused on its own: its ``refusal``
gives the code and detail and it has no paragraphs, the rest is read, and ``refusedParts``
counts them (the receipt then says ``read-in-part``);
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
whitespace), with the parts holding text the reader does not read. The check found every
character of the output in the source, in order, and every source character in the output or
set aside; a read it cannot account for is refused as ``uncertified``.

A document with tracked changes::

    {"certificate": {"views": {...}, "accepted": {...}, "original": {...}}, "format": ...,
     "reader": ..., "source": {...},
     "tracked": {"accepted": {...}, "original": {...}, "changes": [...]}}

``tracked.accepted`` is the document with every change accepted, ``tracked.original`` with every
change rejected, each with ``paragraphs``, the notes, headers, footers, comments and
``refusedParts`` as a read above; there is no ``paragraphs`` outside them, so the caller names
the view it takes. ``changes`` lists each change as stored: ``part``, ``kind`` (``insert``,
``delete``, ``move-from``, ``move-to``, each also with ``-paragraph-mark``; ``insert-row``,
``delete-row``; ``format``, ``format-paragraph``, ``format-table``, ``format-row``,
``format-cell``, ``format-section``), ``id``, ``author`` and ``date`` (null where absent).
Each view is certified as a read is; ``certificate.views`` is the check's account of the views
themselves (``certify_tracked``).

A refusal::

    {"format": ..., "reader": ..., "refusal": {"code": ..., "detail": ...}, "source": {...}}

``paragraphs_sha256`` in ``tests`` and ``corpus/`` is the SHA-256 of ``canonical(paragraphs(...))``
alone, so a new reader version that reads the same way keeps the same digest.
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
    read_document,
    tracked,
)

# The version of the shape above, and of the check that certifies it: versions.lock.json ties
# it to both files (tests/test_locks.py).
FORMAT_VERSION = "label-docx-json/1.12.0"

type Json = str | int | bool | list[Json] | dict[str, Json] | None


def canonical(value: Json) -> bytes:
    """``value`` as canonical JSON bytes, with a final newline."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


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


def notes(items: tuple[Note, ...]) -> list[Json]:
    """Footnotes or endnotes as JSON, in the order the body refers to them."""
    return [
        {"id": n.id, "mark": n.mark, "paragraphs": paragraphs(list(n.paragraphs))} for n in items
    ]


def _refusal(refusal: tuple[str, str] | None) -> Json:
    return None if refusal is None else {"code": refusal[0], "detail": refusal[1]}


def stories(items: tuple[Story, ...]) -> list[Json]:
    """Headers or footers as JSON, in the order the sections refer to them."""
    return [
        {
            "part": s.part,
            "uses": [{"section": section, "type": kind} for section, kind in s.uses],
            "paragraphs": paragraphs(list(s.paragraphs)),
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
            "paragraphs": paragraphs(list(c.paragraphs)),
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


def content(document: Document) -> dict[str, Json]:
    """A document's text as JSON: paragraphs, notes, headers, footers and comments."""
    return {
        "paragraphs": paragraphs(list(document.body)),
        "footnotes": notes(document.footnotes),
        "endnotes": notes(document.endnotes),
        "headers": stories(document.headers),
        "footers": stories(document.footers),
        "comments": comments(document.comments),
        "refusedParts": sum(
            1 for item in (*document.headers, *document.footers, *document.comments) if item.refusal
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
