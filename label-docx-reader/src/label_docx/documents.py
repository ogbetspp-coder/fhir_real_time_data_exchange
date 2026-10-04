"""Which reader reads a document: an ePI Bundle (JSON) or anything else, read as a .docx.

The choice is made from the bytes alone, the same way every time: a document whose first byte
after JSON whitespace is ``{`` is JSON, and goes to the ePI reader; every other document goes to
the .docx reader, which refuses what is not a .docx. Each kind's result names the reader and the
format that made it, so a receipt says which one read the document.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from label_docx import epi, epi_output, output, reader


@dataclass(frozen=True)
class Kind:
    """A kind of document: the reader and format versions that read it, and how."""

    name: str
    reader: str
    format: str
    read: Callable[[bytes], tuple[bytes, bool]]


DOCX = Kind("docx", reader.READER_VERSION, output.FORMAT_VERSION, output.read)
EPI = Kind("epi", epi.READER_VERSION, epi_output.FORMAT_VERSION, epi_output.read)


def kind(data: bytes) -> Kind:
    """The kind of ``data``: ePI if it is JSON, else .docx."""
    return EPI if data.lstrip(b" \t\r\n").startswith(b"{") else DOCX
