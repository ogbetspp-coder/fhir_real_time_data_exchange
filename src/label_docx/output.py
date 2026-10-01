"""The reader's result as canonical JSON: the same input gives the same bytes, everywhere.

The document is an object with sorted keys, no insignificant whitespace, UTF-8 with no ASCII
escaping, and a final newline. Every value is a string, an integer, a boolean, null, an array or
an object; there are no floating-point numbers. For such values this is the JSON Canonicalization
Scheme (RFC 8785): keys are ASCII, so code-point and UTF-16 key order agree, and Python escapes
exactly the characters JCS escapes, in the same form. ``tests/test_output.py`` holds it to that.

Offsets (``marks[].start`` and ``end``) count Unicode code points of ``text``, not UTF-16 code
units or bytes: a consumer in JavaScript must index by code point.

A read::

    {"endnotes": [...], "footnotes": [...], "format": ..., "paragraphs": [...], "reader": ...,
     "source": {"bytes": n, "sha256": hex}}

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

A refusal::

    {"format": ..., "reader": ..., "refusal": {"code": ..., "detail": ...}, "source": {...}}

``paragraphs_sha256`` in ``tests`` and ``corpus/`` is the SHA-256 of ``canonical(paragraphs(...))``
alone, so a new reader version that reads the same way keeps the same digest.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

from label_docx.certify import DocxSource
from label_docx.reader import READER_VERSION, DocxRefusedError, Note, Paragraph, read_document

# The version of the shape above. A change to this file changes its hash in versions.lock.json.
# 1.1.0 adds numbering.text and numbering.suffix, the list label; 1.2.0 adds paragraphs' notes
# and the footnotes and endnotes; 1.3.0 adds paragraphs' pages, where Word draws a page number;
# 1.4.0 adds the certificate, and refuses a read the conservation check cannot account for.
FORMAT_VERSION = "label-docx-json/1.4.0"

type Json = str | int | bool | list[Json] | dict[str, Json] | None


def canonical(value: Json) -> bytes:
    """``value`` as canonical JSON bytes, with a final newline."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


def paragraph(item: Paragraph) -> dict[str, Json]:
    """One paragraph as JSON."""
    return {
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
        envelope["refusal"] = {"code": refused.code, "detail": refused.detail}
        return canonical(envelope), False
    envelope["paragraphs"] = paragraphs(list(document.body))
    envelope["footnotes"] = notes(document.footnotes)
    envelope["endnotes"] = notes(document.endnotes)
    return certified(envelope, lambda: DocxSource(data).certify(envelope))


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
