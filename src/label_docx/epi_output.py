"""The ePI reader's result as canonical JSON, in the form ``output`` gives a .docx's.

Canonical as ``output.canonical`` is (RFC 8785 for these values), and each paragraph has the
shape of a .docx paragraph (``output.paragraph``): a browser has no paragraph styles, note marks
or page numbers, so ``style`` is null and ``notes`` and ``pages`` are empty. Offsets count
Unicode code points.

A read::

    {"documentType": ..., "date": ..., "format": ..., "quirks": [...], "reader": ...,
     "sections": [...], "source": {"bytes": n, "sha256": hex}, "title": ...}

Each section, in the Composition's order, carries its ``code``, ``title``, ``paragraphs``, the
``notes`` the reader made on defects it read through, its ``refusal`` (null, or the ``code`` and
``detail`` of why the reader would not read it, and then no paragraphs) and its own
``sections``. A section the reader refuses does not stop the others: a consumer must look at
every section's ``refusal``, which ``refusedSections`` counts.

``certificate`` is the independent conservation check's account (``label_docx.certify``):
every character of every section read is the div's, in order, with each run of whitespace drawn
as at most one space; a read it cannot account for is refused as ``uncertified``.

A refusal of the whole document::

    {"format": ..., "reader": ..., "refusal": {"code": ..., "detail": ...}, "source": {...}}
"""

from __future__ import annotations

import hashlib

from label_docx.certify import EpiSource
from label_docx.epi import READER_VERSION, EpiRefusedError, Section, read_epi, walk
from label_docx.output import Json, canonical, certified, paragraphs

# The version of the shape above. A change to this file changes its hash in versions.lock.json.
# 1.1.0 adds the certificate, and refuses a read the conservation check cannot account for;
# 1.2.0 certifies with conservation-check/1.1.0; 1.3.0 with 1.2.0; 1.4.0 with 1.3.0; 1.5.0
# with 1.4.0; 1.6.0 with 1.5.0; 1.7.0 with 1.6.0; 1.8.0 with 1.7.0.
FORMAT_VERSION = "label-epi-json/1.8.0"


def section(item: Section) -> dict[str, Json]:
    """One section as JSON, with the sections inside it."""
    return {
        "code": item.code,
        "notes": list(item.notes),
        "paragraphs": paragraphs(list(item.paragraphs)),
        "refusal": (
            None
            if item.refusal is None
            else {"code": item.refusal.code, "detail": item.refusal.detail}
        ),
        "sections": [section(child) for child in item.sections],
        "title": item.title,
    }


def read(data: bytes) -> tuple[bytes, bool]:
    """The canonical JSON result of reading ``data``, and whether it was read (not refused)."""
    source: dict[str, Json] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    envelope: dict[str, Json] = {
        "format": FORMAT_VERSION,
        "reader": READER_VERSION,
        "source": source,
    }
    try:
        document = read_epi(data)
    except EpiRefusedError as refused:
        envelope["refusal"] = {"code": refused.code, "detail": refused.detail}
        return canonical(envelope), False
    envelope["title"] = document.title
    envelope["date"] = document.date
    envelope["documentType"] = document.document_type
    envelope["quirks"] = list(document.quirks)
    envelope["sections"] = [section(item) for item in document.sections]
    envelope["refusedSections"] = sum(1 for s in walk(document.sections) if s.refusal)
    return certified(envelope, lambda: EpiSource(data).certify(envelope))
