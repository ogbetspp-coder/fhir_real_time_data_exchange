"""A deterministic, fail-closed reader for the text of a label: a Word .docx or an EMA ePI.

``read`` reads a .docx only (an ePI's bytes are refused as ``invalid-package``); for either,
``documents.kind(data).read(data)`` picks the reader by the bytes, and ``epi_output.read`` reads
an ePI.
"""

from label_docx.output import FORMAT_VERSION, canonical, read
from label_docx.reader import (
    READER_VERSION,
    Document,
    DocxRefusedError,
    Mark,
    Note,
    NoteReference,
    Numbering,
    Paragraph,
    read_document,
    read_docx,
)

__all__ = [
    "FORMAT_VERSION",
    "READER_VERSION",
    "Document",
    "DocxRefusedError",
    "Mark",
    "Note",
    "NoteReference",
    "Numbering",
    "Paragraph",
    "canonical",
    "read",
    "read_document",
    "read_docx",
]
