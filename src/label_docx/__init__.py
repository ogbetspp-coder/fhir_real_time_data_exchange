"""A deterministic, fail-closed reader for the text of a label's Word (.docx) body."""

from label_docx.output import FORMAT_VERSION, canonical, read
from label_docx.reader import (
    READER_VERSION,
    DocxRefusedError,
    Mark,
    Numbering,
    Paragraph,
    read_docx,
)

__all__ = [
    "FORMAT_VERSION",
    "READER_VERSION",
    "DocxRefusedError",
    "Mark",
    "Numbering",
    "Paragraph",
    "canonical",
    "read",
    "read_docx",
]
