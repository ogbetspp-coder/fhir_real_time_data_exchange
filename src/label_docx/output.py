"""The reader's result as canonical JSON: the same input gives the same bytes, everywhere.

The document is an object with sorted keys, no insignificant whitespace, UTF-8 with no ASCII
escaping, and a final newline. Every value is a string, an integer, a boolean, null, an array or
an object; there are no floating-point numbers. For such values this is the JSON Canonicalization
Scheme (RFC 8785): keys are ASCII, so code-point and UTF-16 key order agree, and Python escapes
exactly the characters JCS escapes, in the same form. ``tests/test_output.py`` holds it to that.

Offsets (``marks[].start`` and ``end``) count Unicode code points of ``text``, not UTF-16 code
units or bytes: a consumer in JavaScript must index by code point.

A read::

    {"format": ..., "paragraphs": [...], "reader": ..., "source": {"bytes": n, "sha256": hex}}

A refusal::

    {"format": ..., "reader": ..., "refusal": {"code": ..., "detail": ...}, "source": {...}}

``paragraphs_sha256`` in ``tests`` and ``corpus/`` is the SHA-256 of ``canonical(paragraphs(...))``
alone, so a new reader version that reads the same way keeps the same digest.
"""

from __future__ import annotations

import hashlib
import json

from label_docx.reader import READER_VERSION, DocxRefusedError, Paragraph, read_docx

# The version of the shape above. A change to this file changes its hash in versions.lock.json.
FORMAT_VERSION = "label-docx-json/1.0.0"

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
        "numbering": (
            None
            if item.numbering is None
            else {"level": item.numbering.level, "numId": item.numbering.num_id}
        ),
        "style": item.style,
        "table": None if item.table is None else list(item.table),
        "text": item.text,
    }


def paragraphs(items: list[Paragraph]) -> list[Json]:
    """Every paragraph as JSON, in document order."""
    return [paragraph(item) for item in items]


def read(data: bytes) -> tuple[bytes, bool]:
    """The canonical JSON result of reading ``data``, and whether it was read (not refused)."""
    source: dict[str, Json] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    envelope: dict[str, Json] = {
        "format": FORMAT_VERSION,
        "reader": READER_VERSION,
        "source": source,
    }
    try:
        envelope["paragraphs"] = paragraphs(read_docx(data))
    except DocxRefusedError as refused:
        envelope["refusal"] = {"code": refused.code, "detail": refused.detail}
        return canonical(envelope), False
    return canonical(envelope), True
