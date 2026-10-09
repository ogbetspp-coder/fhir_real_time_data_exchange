"""Read every .docx under a folder and tally what the reader does with them.

    uv run --frozen python scripts/survey.py path/to/folder
    uv run --frozen python scripts/survey.py --causes path/to/folder [--json OUT]

For a folder of labels of your own: it prints how many documents are read (``tracked`` where a
document holds tracked changes, read as two texts), how many are refused and for which reasons,
and how many carry counted list labels, and writes nothing. Each outcome is the certified result
the service would serve (``label_docx.read``). It
never prints a document's text, only file names, refusal codes and the reader's refusal details
(which name elements, fonts and codes). Add ``--files`` to list each file's outcome.

``--causes`` is a measurement, outside the certified path: for each refused document it lists
every reason the reader refuses it, not just the first (``causes``), then ranks the reasons by
the documents that would read were they lifted. Nothing it makes is a reading of a document.
"""

from __future__ import annotations

import argparse
import collections
import io
import json
import re
import statistics
import sys
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import TextIO

from label_docx import READER_VERSION, read
from label_docx.output import content
from label_docx.reader import (
    Comment,
    DocxRefusedError,
    Story,
    changed_drawing,
    read_document,
    tracked,
)

# The most reads ``causes`` makes of one document before it gives up (``capped``).
CAP = 120
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PARAGRAPH = f'<ns0:p xmlns:ns0="{W}"'.encode()
BODY = f"{{{W}}}body"  # never taken out: the copy would read for having nothing


def outcome(path: Path) -> tuple[str, str, bool]:
    """The refusal code and detail, or "read" or "tracked" and whether a list label counts."""
    value = json.loads(read(path.read_bytes())[0])
    if "refusal" in value:
        return value["refusal"]["code"], value["refusal"]["detail"], False
    tracked_ = "tracked" in value
    paragraphs = value["tracked"]["accepted"]["paragraphs"] if tracked_ else value["paragraphs"]
    counted = any(
        p["numbering"]
        and p["numbering"]["text"]
        and any(c.isalnum() for c in p["numbering"]["text"])
        for p in paragraphs
    )
    return "tracked" if tracked_ else "read", "", counted


def cause(code: str, detail: str) -> str:
    """A refusal as a cause: its code and detail, any view named, quoted values and numbers out."""
    detail = re.sub(r"^(accepted|original) view: ", "", detail)
    # A quoted value may be a document's own text (a field's result): never shown.
    detail = re.sub(r"'[^']*'", "'…'", re.sub(r'"[^"]*"', '"…"', detail))
    return f"{code}: {re.sub(r'[0-9]+', 'N', detail)}"[:120]


def _views(data: bytes) -> tuple[bytes, ...]:
    """The views the reader reads ``data`` as: none for one text, else accepted and original.

    The first steps of the result's (``output.read``), raising its first refusal: the
    document, and where it holds tracked changes, the changes.
    """
    try:
        document = read_document(data)
        stories: tuple[Story | Comment, ...] = (
            *document.headers,
            *document.footers,
            *document.comments,
        )
        if not any(s.refusal is not None and s.refusal[0] == "tracked-change" for s in stories):
            return ()
    except DocxRefusedError as refused:
        if refused.code != "tracked-change":
            raise
    return tracked(data)[:2]


def _body_paragraphs(data: bytes) -> list[ET.Element]:
    """The body's paragraphs of ``data`` in the order the reader reads them (none in drawings)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            body = ET.fromstring(package.read("word/document.xml")).find(BODY)
    except KeyError, ET.ParseError, zipfile.BadZipFile:
        return []  # a package the reader refuses as a package: no paragraph to place
    out: list[ET.Element] = []

    def walk(element: ET.Element) -> None:
        for child in element:
            if child.tag == f"{{{W}}}p":
                out.append(child)
            if child.tag.rsplit("}", 1)[-1] not in ("txbxContent", "drawing", "pict"):
                walk(child)

    if body is not None:
        walk(body)
    return out


def _suspects(refused: DocxRefusedError) -> list[bytes]:
    """Each element the reader held when it refused, innermost first, as its bytes.

    The elements in the frames of the refusal's traceback, paragraphs first, then the innermost
    frame's first and, within a frame, the smallest first; a frame's elements include the body
    paragraph it was reading (a ``paragraph`` the reader made, at its place in the frame's
    ``paragraphs``). The construct refused is among them.
    """
    frames = []
    trace = refused.__traceback__
    while trace is not None:
        frames.append(trace.tb_frame)
        trace = trace.tb_next
    read_ = next(
        (f.f_locals.get("data") for f in reversed(frames) if f.f_code.co_name == "read_document"),
        None,
    )
    sources = _body_paragraphs(read_) if isinstance(read_, bytes) else []
    out: list[bytes] = []
    for frame in reversed(frames):
        held = [v for v in frame.f_locals.values() if isinstance(v, ET.Element) and v.tag != BODY]
        made, made_all = frame.f_locals.get("paragraph"), frame.f_locals.get("paragraphs")
        if isinstance(made_all, list) and len(made_all) == len(sources):
            at = next((i for i, p in enumerate(made_all) if p is made), None)
            if at is not None:
                held.append(sources[at])
        for element in sorted(held, key=lambda e: sum(1 for _ in e.iter())):
            found = ET.tostring(element)
            if found not in out:
                out.append(found)
    # A paragraph first: a smaller element (a field's end, a row) taken out on its own leaves
    # its paragraph or table broken, a refusal the document does not have.
    return sorted(out, key=lambda e: not e.startswith(PARAGRAPH))


class _Scratch:
    """A copy of a package whose parts can lose an element, written again as a .docx."""

    def __init__(self, data: bytes) -> None:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as package:
                self.entries = [(i.filename, package.read(i)) for i in package.infolist()]
        except zipfile.BadZipFile, OSError, RuntimeError, ValueError:
            self.entries = []  # nothing to take out: the reader refuses it as a package
        self.parts: dict[str, ET.Element] = {}
        # Each cause and element taken out for it: met again, the next element held is taken.
        self.tried: set[tuple[str, bytes]] = set()
        for name, raw in self.entries:
            if name.endswith(".xml") and b"wordprocessingml" in raw:
                try:
                    self.parts[name] = ET.fromstring(raw)
                except ET.ParseError:
                    continue

    def take_out(self, suspect: bytes) -> bool:
        """Take every element equal to ``suspect`` out of the parts; whether any changed.

        A paragraph keeps only its bookmarks' starts and ends (a field may refer to one), and
        a table or a cell keeps an empty paragraph, so the structure round them stands;
        anything else goes.
        """
        tag = ET.fromstring(suspect).tag
        marks = (f"{{{W}}}bookmarkStart", f"{{{W}}}bookmarkEnd")
        changed = False
        for root in self.parts.values():
            parents = {child: parent for parent in root.iter() for child in parent}
            for element in [e for e in root.iter(tag) if e in parents]:
                if ET.tostring(element) != suspect:
                    continue
                parent = parents[element]
                if tag == f"{{{W}}}p":
                    kept = [e for e in element.iter() if e.tag in marks]
                    if len(kept) == len(element) and not element.attrib:
                        continue  # nothing left to take out
                    element[:] = kept
                    element.attrib.clear()
                elif tag == f"{{{W}}}tc":
                    element[:] = [ET.Element(f"{{{W}}}p")]
                    element.attrib.clear()
                elif tag == f"{{{W}}}tbl":
                    parent[list(parent).index(element)] = ET.Element(f"{{{W}}}p")
                else:
                    parent.remove(element)
                changed = True
        return changed

    def data(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
            for name, raw in self.entries:
                part = self.parts.get(name)
                package.writestr(name, raw if part is None else ET.tostring(part))
        return buffer.getvalue()


def causes(data: bytes) -> tuple[list[str], str]:
    """Every reason the reader refuses ``data``, in the order met, and how the search ended.

    After each refusal, the element refused (the innermost the reader held that is in the copy;
    failing that, the next) is taken out of a copy, and the copy read again: the document until
    the reader makes its views, then each view as a document of its own. The search ends when
    the copy reads and the check certifies it (``read``) or not (``check``), when the views read
    only once changed (``views``, which the check cannot be put to), when no element held is in
    the copy (``unplaced``), or after ``CAP`` reads (``capped``). Each cause is counted once,
    however often it is met. A measurement only: a copy is not the document, and a cause found
    after another is taken out may be one the copy has and the document has not.
    """
    found: list[str] = []
    reads = 0

    def lifted(scratch: _Scratch, refused: DocxRefusedError) -> bool:
        met = cause(refused.code, refused.detail)
        if met not in found:
            found.append(met)
        for suspect in _suspects(refused):
            if (met, suspect) not in scratch.tried:
                scratch.tried.add((met, suspect))
                if scratch.take_out(suspect):
                    return True
        return False

    source, current = _Scratch(data), data
    views: tuple[bytes, ...] | None = None
    while views is None:
        if reads == CAP:
            return found, "capped"
        reads += 1
        try:
            views = _views(current)
        except DocxRefusedError as refused:
            if not lifted(source, refused):
                return found, "unplaced"
            current = source.data()
    changed = False
    for view in views:
        scratch, now = _Scratch(view), view
        while True:
            if reads == CAP:
                return found, "capped"
            reads += 1
            try:
                content(read_document(now))
                break
            except DocxRefusedError as refused:
                changed = True
                if not lifted(scratch, refused):
                    return found, "unplaced"
                now = scratch.data()
    drawn = changed_drawing(current) if views else None
    if drawn is not None:
        met = cause("tracked-change", f"a change inside a drawing in {drawn}")
        return [*found, *([met] if met not in found else [])], "unplaced"
    if changed:
        return found, "views"
    value = json.loads(read(current)[0])
    if "refusal" not in value:
        return found, "read"
    met = cause(value["refusal"]["code"], value["refusal"]["detail"])
    return [*found, *([met] if met not in found else [])], "check"


def _causes_of(path: Path) -> tuple[str, list[str], str]:
    return (path.name, *causes(path.read_bytes()))


def rank(found: dict[str, list[str]]) -> list[tuple[str, int, int]]:
    """Greedy: the cause whose lifting reads the most refused documents next, and so on.

    Each row is the cause, the documents its lifting reads (every cause above it lifted), and
    the documents read in all once it is. Ties go to the cause blocking more documents, then to
    the first by name.
    """
    left = {name: set(c) for name, c in found.items() if c}
    lifted: set[str] = set()
    out: list[tuple[str, int, int]] = []
    done = 0
    while left:

        def reads(c: str) -> int:
            return sum(1 for v in left.values() if v - lifted <= {c})

        def blocks(c: str) -> int:
            return sum(1 for v in left.values() if c in v)

        best = max(
            sorted(set().union(*left.values()) - lifted), key=lambda c: (reads(c), blocks(c))
        )
        lifted.add(best)
        now = [name for name, c in left.items() if c <= lifted]
        done += len(now)
        out.append((best, len(now), done))
        for name in now:
            del left[name]
    return out


def report(results: list[tuple[str, list[str], str]], out: TextIO) -> None:
    """Counts only: files each cause blocks, the greedy ranking, causes per refused file."""
    refused = {name: c for name, c, _ in results if c}
    out.write(f"{READER_VERSION}: {len(results)} documents, {len(refused)} refused\n")
    ends = collections.Counter(end for name, c, end in results if name in refused)
    out.write("search ended: " + ", ".join(f"{k} {v}" for k, v in sorted(ends.items())) + "\n")
    blocks = collections.Counter(x for c in refused.values() for x in c)
    first = collections.Counter(c[0] for c in refused.values())
    out.write("files each cause blocks (and of them, those it is the first refusal of):\n")
    for c, n in sorted(blocks.items(), key=lambda x: (-x[1], x[0])):
        out.write(f"  {n:5} {first[c]:5}  {c}\n")
    out.write("lift next (files it reads, files read in all):\n")
    for c, now, done in rank(refused):
        out.write(f"  {now:5} {done:5}  {c}\n")
    counts = sorted(len(c) for c in refused.values())
    if counts:
        out.write(
            f"causes per refused file: median {statistics.median(counts)}, "
            + ", ".join(f"{k}: {v}" for k, v in sorted(collections.Counter(counts).items()))
            + "\n"
        )


def main() -> int:
    """Print the tally; 0 whatever the outcomes."""
    parser = argparse.ArgumentParser(description="Tally the reader's outcomes over a folder.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--files", action="store_true", help="list each file's outcome")
    parser.add_argument("--causes", action="store_true", help="every reason, not the first")
    parser.add_argument("--json", type=Path, help="with --causes: each file's causes, to here")
    parser.add_argument("--jobs", type=int, default=1, help="with --causes: processes")
    args = parser.parse_args()
    paths = sorted(args.folder.rglob("*.docx"))
    out = sys.stdout
    if args.causes:
        with ProcessPoolExecutor(args.jobs) as pool:
            surveyed = list(pool.map(_causes_of, paths))
        if args.json:
            args.json.write_text(json.dumps(surveyed, indent=1) + "\n", "utf-8")
        report(surveyed, out)
        return 0
    results = {path: outcome(path) for path in paths}
    codes = collections.Counter(code for code, _, _ in results.values())
    out.write(f"{READER_VERSION}: {len(paths)} documents\n")
    for code, count in codes.most_common():
        out.write(f"  {count:5}  {code}\n")
    out.write(f"  {sum(c for _, _, c in results.values()):5}  read, with numbered list labels\n")
    reasons = collections.Counter(
        (c, d) for c, d, _ in results.values() if c not in ("read", "tracked")
    )
    if reasons:
        out.write("refusals:\n")
        for (code, detail), count in reasons.most_common():
            out.write(f"  {count:5}  {code}: {detail}\n")
    if args.files:
        for path, (code, detail, _) in results.items():
            out.write(f"{path.relative_to(args.folder)}: {code} {detail}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
