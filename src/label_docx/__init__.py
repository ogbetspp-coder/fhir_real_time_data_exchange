"""A deterministic, fail-closed reader for the text of a label's Word (.docx) body."""

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
