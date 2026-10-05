"""Labels read by the label reader (``label-docx-reader/``), through its certified reads only.

Zone A reads a .docx with ``label_docx.output.read`` and an ePI with
``label_docx.epi_output.read``: each returns the reader's result as canonical JSON, which the
reader's independent conservation check (``label_docx.certify``) has accounted for character by
character, or a refusal. A result the check cannot account for is refused there
(``uncertified``), so it reaches Zone A as a refusal like any other. The values Zone A checks
(``Paragraph``, ``Section``, ``Document``) are rebuilt here from that JSON, field for field: what
is checked is what was certified.

A .docx with tracked changes reads to two texts, every change accepted and every change rejected,
with no default between them; Zone A takes neither and refuses it (``tracked-change``). Nothing in
Zone A reads a footnote's or endnote's text, so a body that refers to one is refused
(``note-reference``); nor a page number's, which the text leaves out (``page-number``).
"""

from __future__ import annotations

import json
from typing import Any

from label_docx.epi import Document, EpiRefusedError, Section, SectionRefusal
from label_docx.epi_output import read as read_epi_json
from label_docx.output import read as read_docx_json
from label_docx.reader import (
    CommentReference,
    DocxRefusedError,
    Mark,
    NoteReference,
    Numbering,
    Paragraph,
)


def _paragraph(value: dict[str, Any]) -> Paragraph:
    numbering = value["numbering"]
    table = value["table"]
    return Paragraph(
        text=value["text"],
        style=value["style"],
        numbering=None
        if numbering is None
        else Numbering(
            numbering["numId"], numbering["level"], numbering["text"], numbering["suffix"]
        ),
        table=None if table is None else (table[0], table[1], table[2]),
        marks=tuple(Mark(m["start"], m["end"], m["kind"]) for m in value["marks"]),
        mark_hidden=value["markHidden"],
        notes=tuple(
            NoteReference(n["offset"], n["kind"], n["id"], n["mark"]) for n in value["notes"]
        ),
        pages=tuple(value["pages"]),
        comments=tuple(CommentReference(c["offset"], c["id"]) for c in value["comments"]),
    )


def read_docx(data: bytes) -> list[Paragraph]:
    """The body paragraphs of a .docx, as the reader certified them.

    A footnote or endnote's text is in the reader's result but not in these paragraphs, and
    nothing here reads it, so a body that refers to one is refused rather than read without it;
    so is a body with a page number (PAGE, PAGEREF...), whose digits the text leaves out.

    Raises:
        DocxRefusedError: The reader refused the document (``uncertified`` among the codes), it
            has tracked changes, or its body refers to a footnote or endnote or holds a page
            number.
    """
    result, was_read = read_docx_json(data)
    value = json.loads(result)
    if not was_read:
        raise DocxRefusedError(value["refusal"]["code"], value["refusal"]["detail"])
    if "tracked" in value:
        raise DocxRefusedError("tracked-change", "two texts (changes accepted, rejected)")
    if any(paragraph["notes"] for paragraph in value["paragraphs"]):
        raise DocxRefusedError("note-reference", "a footnote or endnote, whose text is not read")
    if any(paragraph["pages"] for paragraph in value["paragraphs"]):
        # The text keeps only the place of a page number: Word prints one there.
        raise DocxRefusedError("page-number", "a page number, which the text leaves out")
    return [_paragraph(paragraph) for paragraph in value["paragraphs"]]


def _section(value: dict[str, Any]) -> Section:
    refusal = value["refusal"]
    return Section(
        code=value["code"],
        title=value["title"],
        paragraphs=tuple(_paragraph(paragraph) for paragraph in value["paragraphs"]),
        refusal=None if refusal is None else SectionRefusal(refusal["code"], refusal["detail"]),
        sections=tuple(_section(child) for child in value["sections"]),
        notes=tuple(value["notes"]),
    )


def read_epi(data: bytes) -> Document:
    """The sections of an EMA ePI document Bundle, as the reader certified them.

    A section the reader refused is in the document with its ``refusal``, as the reader gives it.

    Raises:
        EpiRefusedError: The reader refused the document (``uncertified`` among the codes).
    """
    result, was_read = read_epi_json(data)
    value = json.loads(result)
    if not was_read:
        raise EpiRefusedError(value["refusal"]["code"], value["refusal"]["detail"])
    return Document(
        title=value["title"],
        date=value["date"],
        document_type=value["documentType"],
        sections=tuple(_section(section) for section in value["sections"]),
        quirks=tuple(value["quirks"]),
    )
