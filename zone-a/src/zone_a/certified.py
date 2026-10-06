"""Labels read by the label reader (``label-docx-reader/``), through its certified reads only.

Zone A reads a .docx with ``label_docx.output.read`` and an ePI with
``label_docx.epi_output.read``: each returns the reader's result as canonical JSON, which the
reader's independent conservation check (``label_docx.certify``) has accounted for character by
character, or a refusal. A result the check cannot account for is refused there
(``uncertified``), so it reaches Zone A as a refusal like any other. The values Zone A checks
(``Paragraph``, ``Section``, ``Document``) are rebuilt here from that JSON, field for field: what
is checked is what was certified.

A .docx with tracked changes reads to two texts, every change accepted and every change rejected,
with no default between them; Zone A takes the one a person names (``read_body``'s ``view``) and
refuses it when none is named (``tracked-change``). Nothing in
Zone A reads a footnote's or endnote's text, so a body that refers to one is refused
(``note-reference``); nor a page number's, which the text leaves out (``page-number``).
"""

from __future__ import annotations

import hashlib
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from typing import Any, Final

from label_docx.epi import Document, EpiRefusedError, Section, SectionRefusal
from label_docx.epi_output import read as read_epi_json
from label_docx.output import read as read_docx_json
from label_docx.reader import (
    Anchored,
    CommentReference,
    DocxRefusedError,
    Mark,
    NoteReference,
    Numbering,
    Paragraph,
    Picture,
    W,
    tracked,
)


def _pair(value: list[int] | None) -> tuple[int, int] | None:
    return None if value is None else (value[0], value[1])


def _crop(value: dict[str, int] | None) -> tuple[int, int, int, int] | None:
    return None if value is None else (value["l"], value["t"], value["r"], value["b"])


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
        # A .docx paragraph's; an ePI's has none.
        pictures=tuple(
            Picture(
                x["offset"],
                x["kind"],
                x["part"],
                x["sha256"],
                x["type"],
                _pair(x["pixels"]),
                _pair(x["extent"]),
                _crop(x["crop"]),
                x["reason"],
            )
            for x in value.get("pictures", [])
        ),
        anchored=tuple(
            Anchored(a["offset"], a["kind"], a["read"]) for a in value.get("anchored", [])
        ),
    )


@dataclass(frozen=True)
class Body:
    """A .docx body as the reader certified it.

    ``tables`` are the reader's table entries as JSON (each table's grid, or why there is none);
    each paragraph's ``anchored`` places the floating objects Word draws apart from its text;
    ``layout`` names what else Word draws that the read does not yet report (``layout``, below);
    ``images`` are the bytes of each body picture the reader found nothing against (``reason``
    null), by their SHA-256, as the package stores them. Floating objects in headers, footers,
    notes and comments are not here: they are not the body's.
    """

    paragraphs: tuple[Paragraph, ...]
    tables: tuple[dict[str, Any], ...]
    layout: tuple[str, ...] = field(default=())
    images: dict[str, bytes] = field(default_factory=dict)
    # For a document with tracked changes, the view a person chose ("accepted", every change
    # accepted, or "original", every one rejected) and how many changes the document holds.
    view: str | None = None
    changes: int = 0


# Elements whose drawing the read does not yet report (ADR 0006 P1), by local name, and what each
# is: a table positioned off the text flow, a positioned paragraph (a frame, a drop capital), and
# a table drawn right to left.
_LAYOUT: Final = {
    "tblpPr": "floating-table",
    "framePr": "frame",
    "bidiVisual": "right-to-left-table",
}
_DRAWN_AS_TEXT: Final = frozenset({"t", "sym", "noBreakHyphen", "drawing", "pict", "object"})


def layout(data: bytes) -> tuple[str, ...]:
    """What Word draws in the package that the reader's text does not yet say, by name.

    Not a reading: a scan of every XML part under ``word/`` for the elements of ``_LAYOUT`` (a
    part that does not parse is ``unreadable-part``), for a page or column break with a drawn
    character right before and right after it in its paragraph, where Word draws the two on
    different pages or columns and the text, which leaves the break out, runs them together
    ("10" and "5 mg" read "105 mg"), and for character scaling (``w:w``) that may stretch a
    body picture. Conservative by design, until the reader reports these itself, but for what
    cannot reach the body's text:

    - a frame (``framePr``) in a paragraph style no body paragraph uses, through ``basedOn``
      (one naming no style uses the default paragraph style); in a table style, the defaults or
      a list level it counts, as in the body;
    - Word's page-number frame in a header or footer, which stays on its own line there:
      anchored to its text, of no height and no vertical alignment, at most 20 twips below it;
    - character scaling in a list level (a label holds no picture) or on a body run holding no
      picture; on a run holding one, in a style or the defaults, it counts.
    """
    found: set[str] = set()
    pictures = scaled = False
    framed_styles: set[str] = set()
    styles: dict[str, ET.Element] = {}
    default_style: str | None = None
    used: set[str | None] = set()
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        for name in package.namelist():
            if not (name.startswith("word/") and name.endswith(".xml")):
                continue
            try:
                root = ET.fromstring(package.read(name))
            except ET.ParseError:
                found.add("unreadable-part")  # Word's own parts: none the reader read is so
                continue
            stem = name.removeprefix("word/").removesuffix(".xml").rstrip("0123456789")
            for element in root.iter():
                local = element.tag.rsplit("}", 1)[-1]
                if local == "framePr":
                    if stem == "styles" or (stem in ("header", "footer") and _own_line(element)):
                        continue  # a style's: counted below where the body uses it
                    found.add("frame")
                elif local in _LAYOUT:
                    found.add(_LAYOUT[local])
                elif local == "w" and _scales(element) and stem != "numbering":
                    # A run's own scaling counts only for a picture in that run.
                    scaled = scaled or stem != "document" or _holds_picture(root, element)
                elif local == "p" and _break_between_words(element):
                    found.add("page-break")
            if stem == "document":
                pictures = any(e.tag.rsplit("}", 1)[-1] in ("drawing", "pict") for e in root.iter())
                for paragraph in root.iter(f"{{{W}}}p"):
                    named = paragraph.find(f"{{{W}}}pPr/{{{W}}}pStyle")
                    used.add(None if named is None else named.get(f"{{{W}}}val"))
            elif stem == "styles":
                for style in root.iter(f"{{{W}}}style"):
                    key = style.get(f"{{{W}}}styleId", "")
                    styles[key] = style
                    kind, default = style.get(f"{{{W}}}type"), style.get(f"{{{W}}}default")
                    if kind == "paragraph" and default in ("1", "true", "on"):
                        default_style = key
                    if next(style.iter(f"{{{W}}}framePr"), None) is not None:
                        framed_styles.add(key)
                        if kind != "paragraph":
                            found.add("frame")  # a table style's: not traced to its tables
                defaults = root.find(f"{{{W}}}docDefaults")
                if (
                    defaults is not None
                    and next(defaults.iter(f"{{{W}}}framePr"), None) is not None
                ):
                    found.add("frame")
    for start in {default_style if k is None else k for k in used}:
        current: str | None = start
        seen: set[str] = set()
        while current is not None and current in styles and current not in seen:
            if current in framed_styles:
                found.add("frame")
            seen.add(current)
            based = styles[current].find(f"{{{W}}}basedOn")
            current = None if based is None else based.get(f"{{{W}}}val")
    if pictures and scaled:
        found.add("character-scale")
    return tuple(sorted(found))


def _scales(element: ET.Element) -> bool:
    """Whether a ``w:w`` scales characters (any value but 100)."""
    return any(k.endswith("}val") and v != "100" for k, v in element.attrib.items())


def _holds_picture(root: ET.Element, scaling: ET.Element) -> bool:
    """Whether the run whose properties hold ``scaling`` holds a picture, or it is no run's."""
    for run in root.iter(f"{{{W}}}r"):
        properties = run.find(f"{{{W}}}rPr")
        if properties is not None and scaling in list(properties):
            return any(c.tag.rsplit("}", 1)[-1] in ("drawing", "pict", "object") for c in run)
    return True  # a paragraph mark's, or elsewhere: as before


def _own_line(frame: ET.Element) -> bool:
    """Whether a frame stays on its own line: Word's page-number frame in a header or footer.

    Anchored to its text, of no height and no vertical alignment, at most 20 twips below it.
    """
    values = {k.rsplit("}", 1)[-1]: v for k, v in frame.attrib.items()}
    y = values.get("y", "0")
    return (
        values.get("vAnchor") == "text"
        and not {"h", "hRule", "yAlign"} & values.keys()
        and y.isdigit()
        and int(y) <= 20
    )


def _break_between_words(paragraph: ET.Element) -> bool:
    """A page or column break with a drawn, non-white character on each side, in reading order."""
    pieces: list[str] = []  # each drawn piece of the paragraph, or "\f" for a page or column break
    for element in paragraph.iter():
        local = element.tag.rsplit("}", 1)[-1]
        if local == "br":
            kind = next((v for k, v in element.attrib.items() if k.endswith("}type")), None)
            pieces.append("\f" if kind in ("page", "column") else "\n")
        elif local in ("tab", "ptab"):
            pieces.append("\t")
        elif local in _DRAWN_AS_TEXT:
            pieces.append(element.text or "" if local == "t" else "x")
    text = "".join(pieces)
    return any(
        0 < at < len(text) - 1 and not text[at - 1].isspace() and not text[at + 1].isspace()
        for at, character in enumerate(text)
        if character == "\f"
    )


VIEWS: Final = ("accepted", "original")


def read_body(data: bytes, view: str | None = None) -> Body:
    """The body of a .docx, as the reader certified it.

    A document with tracked changes has two texts, each certified: every change accepted and
    every change rejected. It is read only by the ``view`` a person names (owner decision
    2026-10-06, ADR 0006), which the body records with the number of changes; with none it is
    refused. A footnote or endnote's text is in the reader's result but not in these paragraphs,
    and nothing here reads it, so a body that refers to one is refused rather than read without
    it; so is a body with a page number (PAGE, PAGEREF...), whose digits the text leaves out.

    Raises:
        DocxRefusedError: The reader refused the document (``uncertified`` among the codes), it
            has tracked changes and no view is named, or its body refers to a footnote or endnote
            or holds a page number.
        ValueError: A view is named that is not one of ``VIEWS``, or for a document with one
            text.
    """
    if view is not None and view not in VIEWS:
        raise ValueError(f"no view {view!r}: {' or '.join(VIEWS)}")
    result, was_read = read_docx_json(data)
    value = json.loads(result)
    if not was_read:
        raise DocxRefusedError(value["refusal"]["code"], value["refusal"]["detail"])
    changes = 0
    if "tracked" in value:
        if view is None:
            raise DocxRefusedError(
                "tracked-change", "two texts (changes accepted, rejected): a person names one"
            )
        changes = len(value["tracked"]["changes"])
        value = value["tracked"][view]
        accepted, original, _ = tracked(data)
        drawn = accepted if view == "accepted" else original
    elif view is not None:
        raise ValueError("a view of a document with no tracked changes")
    else:
        drawn = data
    if any(paragraph["notes"] for paragraph in value["paragraphs"]):
        raise DocxRefusedError("note-reference", "a footnote or endnote, whose text is not read")
    if any(paragraph["pages"] for paragraph in value["paragraphs"]):
        # The text keeps only the place of a page number: Word prints one there.
        raise DocxRefusedError("page-number", "a page number, which the text leaves out")
    paragraphs = tuple(_paragraph(paragraph) for paragraph in value["paragraphs"])
    images: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        for paragraph in paragraphs:
            for picture in paragraph.pictures:
                if picture.reason is None and picture.part is not None:
                    image = package.read(picture.part)
                    # The reader certified the part's hash; held to it again here, as read now.
                    if hashlib.sha256(image).hexdigest() != picture.sha256:
                        raise DocxRefusedError("uncertified", f"{picture.part} is not as certified")
                    images[hashlib.sha256(image).hexdigest()] = image
    return Body(
        paragraphs=paragraphs,
        tables=tuple(value["tables"]),
        layout=layout(drawn),
        images=images,
        view=view,
        changes=changes,
    )


def read_docx(data: bytes) -> list[Paragraph]:
    """The body paragraphs of a .docx, as the reader certified them (``read_body``)."""
    return list(read_body(data).paragraphs)


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
