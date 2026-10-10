"""How a set of Word SmPCs fares, label by label: read, structured, carried, drawn, and agreeing.

    uv run --frozen python scripts/scoreboard.py FOLDER [--keys KEYS] [--no-drawing]
        [--view accepted|original] [--document smpc|pl] [--out FILE]

Each .docx in FOLDER is read with zone_a.certified, structured with zone_a.structure and built
with zone_a.word_epi; where Chrome is installed and ``--no-drawing`` is not given, each carried
narrative is drawn (zone_a.drawing). With ``--keys``, a folder of answer keys (an EMA ePI
Bundle named as the .docx, ``NAME.json``), each carried section is compared with the key's
section of the same EMA code, as the label reader reads it, line by line (list labels and text,
spaces collapsed); a key's sub-sections the template has no place for are read into their
parent. An Annex I holding several SmPCs (``zone_a.structure.smpcs``) is counted over them all
(``smpcs``), each structured and built on its own; it has no answer key, and one whose boundary
a person must settle needs a person (``smpc-boundary``). With ``--document pl`` each file is
read for its package leaflets instead (``zone_a.leaflet``), each structured and built on its own
and counted together (``leaflets``), with no answer key; a file whose leaflet root line is not
there once needs a person (``pl-root``).

A document with tracked changes is refused unless a view is named; with ``--view`` it is
measured by that view (``zone_a.certified.read_body``), as a person may import it (ADR 0006),
and the entry says so (``view``).

Nothing a label says is printed or written: only codes, counts, keys and line numbers, so it can
be run on documents that must not leave their environment. The result is JSON: one entry per
file and the totals.

For each release, the coverage of a corpus and what got worse since the last
(``docs/validation/coverage-scoreboard.md``)::

    uv run --frozen python scripts/scoreboard.py coverage --smpc SMPC --pl PL --out DIR
        [--root REPO] [--holdout MANIFEST] [--jobs 2]
    uv run --frozen python scripts/scoreboard.py regress BEFORE AFTER

``coverage`` reads every .docx of the SmPC and leaflet cuts (a tracked one by its accepted
view), builds it with no drawing, and writes ``DIR/sections.jsonl`` (one record per file, then
one per built section, keyed by the file's sha256, never its name) and ``DIR/summary.json``
(aggregates only). With ``--holdout`` (the manifest of the frozen hold-out) it writes the summary
alone, split by new and updated products, so a person tuning the rules never sees a file of it.
``regress`` compares two such folders and lists the files whose outcome got worse and the
sections carried before that are now refused, changed or gone; it exits 1 when there is any.
Both write the same bytes for the same input.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from label_docx import READER_VERSION, browser
from label_docx.epi_output import read as read_epi
from label_docx.reader import DocxRefusedError

from zone_a import drawing, leaflet, word_epi
from zone_a.canonical_json import canonical_json
from zone_a.certified import Body, read_body
from zone_a.fidelity.normalize import NORMALIZATION_VERSION
from zone_a.structure import STRUCTURE_VERSION, smpcs, structure

ROOT = Path(__file__).resolve().parents[2]
# Each document's registry and mapping, under a repository's root.
TEMPLATES = {
    "smpc": ("qrd/registry/cap-smpc-en-10.4.json", "fhir/mappings/cap-smpc-en.json"),
    "pl": ("qrd/registry/cap-pl-en-10.4.json", "fhir/mappings/cap-pl-en.json"),
}
_SPACE = re.compile("[ \t\n\u00a0]+")


def _line(label: str | None, text: str) -> str:
    return _SPACE.sub(" ", (f"{label} " if label else "") + text).strip()


def _key_lines(key: Mapping[str, Any], codes: set[str]) -> dict[str, list[str] | None]:
    """Each key section's lines by EMA code; None for a section the reader refused.

    A sub-section with no code of ours is read into its parent, with its title.
    """
    out: dict[str, list[str] | None] = {}

    def lines(section: Mapping[str, Any], inner: bool) -> list[str] | None:
        if section["refusal"] is not None:
            return None
        got = [_line(None, section["title"])] if inner else []
        for paragraph in section["paragraphs"]:
            numbering = paragraph["numbering"]
            got.append(_line(numbering["text"] if numbering else None, paragraph["text"]))
        for child in section["sections"]:
            if child["code"] not in codes:
                more = lines(child, True)
                if more is None:
                    return None
                got += more
        return [x for x in got if x]

    def visit(section: Mapping[str, Any]) -> None:
        out[section["code"]] = lines(section, False)
        for child in section["sections"]:
            visit(child)

    for section in key["sections"]:
        visit(section)
    return out


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _build(body: Body, document: str, root: Path = ROOT) -> dict[str, Any]:
    """The body structured and built, each part on its own: the outcome and what it holds.

    ``outcome`` is "parts-unclear" (an Annex I's SmPCs or the leaflets cannot be told apart),
    "needs-a-person" (``needs``: the sections the structure is not ready over),
    "document-refused" (``code``) or "built" (``built``: each part's ``word_epi.sections``).
    """
    smpc = document == "smpc"
    registry, mapping = (_load(root / at) for at in TEMPLATES[document])
    if smpc:
        parts, why = smpcs(body.paragraphs, registry, mapping)
    else:
        parts, why = leaflet.leaflets(body.paragraphs, registry)
    if why is not None:
        return {"outcome": "parts-unclear"}
    several = len(parts) > 1
    # One SmPC as before; several, each structured and built on its own, counted together.
    structures = [
        structure(body.paragraphs, registry, mapping, None, part if several else None)
        if smpc
        else leaflet.structure(body.paragraphs, registry, mapping, None, part)
        for part in parts
    ]
    summary = sum((Counter(s["summary"]) for s in structures), Counter[str]())
    out: dict[str, Any] = {
        "parts": len(parts),
        "structure": {k: v for k, v in summary.items() if v},
    }
    if not all(s["ready"] for s in structures):
        needs = [
            s["key"]
            for structured in structures
            for s in structured["sections"]
            if s["status"] in ("missing", "duplicate", "order", "no-code")
        ]
        return out | {"outcome": "needs-a-person", "needs": needs}
    try:
        built = [word_epi.sections(body, s, registry) for s in structures]
    except word_epi.RefusedError as refused:
        return out | {"outcome": "document-refused", "code": refused.code}
    return out | {"outcome": "built", "built": built}


def score(
    path: Path,
    keys: Path | None,
    chrome: Path | None,
    view: str | None = None,
    document: str = "smpc",
) -> dict[str, Any]:
    """One label's entry (the module docstring)."""
    data = path.read_bytes()
    entry: dict[str, Any] = {"file": path.name, "sha256": hashlib.sha256(data).hexdigest()}
    try:
        body = read_body(data)
    except DocxRefusedError as refused:
        if refused.code != "tracked-change" or view is None:
            return entry | {"outcome": "reader-refused", "code": refused.code}
        try:
            body = read_body(data, view)
        except DocxRefusedError as again:
            return entry | {"outcome": "reader-refused", "code": again.code, "view": view}
        entry["view"] = view
    made = _build(body, document)
    if made["outcome"] == "parts-unclear":
        why = "pl-root" if document == "pl" else "smpc-boundary"
        return entry | {"outcome": "needs-a-person", "sections": [why]}
    entry["structure"] = made["structure"]
    several = made["parts"] > 1
    if several:
        entry["smpcs" if document == "smpc" else "leaflets"] = made["parts"]
    if made["outcome"] == "needs-a-person":
        return entry | {"outcome": "needs-a-person", "sections": made["needs"]}
    if made["outcome"] == "document-refused":
        return entry | {"outcome": "document-refused", "code": made["code"]}
    built: dict[str, Any] = {"sections": []}
    for one in made["built"]:
        drawn = one if chrome is None else drawing.refuse(one, drawing.check(body, one, chrome))
        built["sections"] += drawn["sections"]
    carried = [s for s in built["sections"] if s["refusal"] is None]
    entry["outcome"] = "built"
    entry["sections"] = len(built["sections"])
    entry["carried"] = len(carried)
    entry["refused"] = dict(
        Counter(s["refusal"]["code"] for s in built["sections"] if s["refusal"])
    )
    entry["drawn"] = chrome is not None
    key_path = None if keys is None or several else keys / f"{path.stem}.json"
    if key_path is not None and key_path.exists():
        key = json.loads(read_epi(key_path.read_bytes())[0])
        theirs = _key_lines(key, {s["code"] for s in built["sections"]})
        agree = Counter[str]()
        differ: list[dict[str, Any]] = []
        for section in carried:
            start, stop = section["paragraphs"]
            mine = [
                _line(p.numbering.text if p.numbering else None, p.text)
                for p in body.paragraphs[start:stop]
                if not word_epi.blank(p)
            ]
            mine = [x for x in mine if x]
            key_section = theirs.get(section["code"], "missing")
            if key_section == "missing":
                agree["no key section"] += 1
            elif key_section is None:
                agree["key section refused"] += 1
            elif key_section == mine:
                agree["same"] += 1
            else:
                agree["differs"] += 1
                at = next(
                    (i for i, (a, b) in enumerate(zip(key_section, mine, strict=False)) if a != b),
                    min(len(key_section), len(mine)),
                )
                differ.append({"key": section["key"], "line": at})
        entry["key"] = {"result": dict(agree), "differs": differ}
    return entry


# ---- coverage and regression (the module docstring) --------------------------------------------

# File outcomes, best first: a worse one is further on (``regress``).
BUCKETS = (
    "whole",
    "built-section-refused",
    "document-refused",
    "needs-a-person",
    "parts-unclear",
    "reader-refused",
)
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def cause(code: str, detail: object) -> str:
    """A refusal as a cause: its code and detail, quoted values, code points and numbers out.

    A reader's detail may quote a document's own text (a field's result); a builder's names a
    mark kind or a code point. Neither is kept.
    """
    detail = re.sub(r"'[^']*'", "'…'", re.sub(r'"[^"]*"', '"…"', str(detail)))
    detail = re.sub(r"U\+[0-9A-Fa-f]+", "U+N", detail)
    return f"{code}: {re.sub('[0-9]+', 'N', detail)}"[:80]


# The builder's detail forms (zone_a.word_epi), after ``cause``: a fixed phrase, or a reader's
# kind (a mark's, an anchored object's, a picture's reason) as one token. Any other detail is
# summarised as "other", so a free-text detail never reaches a summary.
_TOKEN = "[a-z][A-Za-z]*(?:[-+][A-Za-z0-9]+)*"
_KNOWN = re.compile(
    rf"(?:formatting|heading-formatting|anchored-object|narrative): {_TOKEN}"
    rf"|picture: a picture not carried: {_TOKEN}|list-label: a label followed by {_TOKEN}"
    rf"|table-grid: no grid on record: {_TOKEN}|script: U\+N raised or lowered"
    r"|[a-z-]+: (?:a comment|a hidden paragraph mark|a soft hyphen|a tab|U\+N or U\+N"
    r"|a bullet glyph after a line break|a label with no text|a picture (?:without its bytes or"
    r" its size|over N MiB|Word draws at no size|drawn larger or out of proportion)"
    r"|an underline that can change the text|raised and lowered at once|a table in a table"
    r"|cells and grid differ|a row of no cells that leaves columns out"
    r"|a row of exact height: Word clips its text|a merge under no cell of its columns"
    r"|text in a merged cell|a bullet no HTML list draws, in a cell"
    r"|a label that joins the number after it|a table in two sections|lists at two levels"
    r"|the narrative does not read as the page|Word draws it; the read does not yet say how"
    r"|a heading in a table)"
)


def public(found: str) -> str:
    """A cause as a summary may show it: a known form, else its code and "other"."""
    return found if _KNOWN.fullmatch(found) else found.split(":", 1)[0] + ": other"


_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


def plain_chars(data: bytes) -> int | None:
    """The characters of word/document.xml as Word shows it; None where there is no such part.

    Its ``w:t``: not ``w:delText`` (deleted text) nor ``w:instrText`` (a field's code, where the
    result is ``w:t``), and of markup compatibility's choices only ``mc:Choice``, which Word
    draws, not ``mc:Fallback``. A file the reader refuses has no text as the reader reads it:
    this stands for it.
    """
    # ponytail: text moved away (w:moveFrom) is counted; close enough for a denominator.

    def count(element: ET.Element) -> int:
        if element.tag == f"{_MC}Fallback":
            return 0
        own = len(element.text or "") if element.tag == f"{_W}t" else 0
        return own + sum(count(child) for child in element)

    try:
        return count(ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml")))
    except zipfile.BadZipFile, KeyError, ET.ParseError, ValueError:
        return None


def blockers(body: Body, section: Mapping[str, Any]) -> list[str]:
    """Every cause of a refused section the builder's checks find, not just the first.

    Each paragraph's check and each mark on its own, the heading's, its anchored objects and its
    list levels; with the section's own refusal always among them.
    """
    # ponytail: a paragraph's first check refusal hides its next, and what the narrative and
    # its tables refuse (table-shape, row-height...) is seen only when it is the section's
    # refusal; so the unlock order is optimistic. Lift each check to report all if it misleads.
    paragraphs, heading = body.paragraphs, section["heading"]
    start, stop = section["paragraphs"]
    found = {cause(section["refusal"]["code"], section["refusal"]["detail"])}
    for i in (heading, *range(start, stop)):
        found |= {cause("anchored-object", a.kind) for a in paragraphs[i].anchored}
    if paragraphs[heading].table is not None:
        found.add(cause("heading-in-table", "a heading in a table"))
    checks: list[Callable[[], object]] = [partial(word_epi._heading, heading, paragraphs[heading])]
    for i in range(start, stop):
        p = paragraphs[i]
        checks += [partial(word_epi._check, i, p, body.images), partial(word_epi._marks, i, p)]
        checks += [partial(word_epi._marks, i, dataclasses.replace(p, marks=(m,))) for m in p.marks]
    for check in checks:
        try:
            check()
        except word_epi.RefusedError as refused:
            found.add(cause(refused.code, refused.detail))
    labelled = [paragraphs[i] for i in range(start, stop) if word_epi._label(paragraphs[i])]
    if len({p.numbering.level for p in labelled if p.numbering}) > 1:
        found.add(cause("list-level", "lists at two levels"))
    return sorted(found)


def _indices(section: Mapping[str, Any]) -> set[int]:
    start, stop = section["paragraphs"]
    return {section["heading"], *range(start, stop)}


def measure(job: tuple[str, str, str]) -> list[dict[str, Any]]:
    """One file's records: the file's, then each built section's (the module docstring)."""
    path, document, root = job
    data = Path(path).read_bytes()
    file: dict[str, Any] = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "document": document,
        "section": None,
    }
    try:
        try:
            body, file["view"] = read_body(data, "accepted"), "accepted"
        except ValueError:  # one text: no view to name
            body, file["view"] = read_body(data), None
    except DocxRefusedError as refused:
        return [
            file
            | {
                "outcome": "reader-refused",
                "code": refused.code,
                "cause": cause(refused.code, refused.detail),
                "chars": plain_chars(data),
                "carriedChars": 0,
            }
        ]
    file["chars"] = sum(len(p.text) for p in body.paragraphs)
    file["carriedChars"] = 0
    made = _build(body, document, Path(root))
    if made["outcome"] == "needs-a-person":
        return [file | {"outcome": "needs-a-person", "needs": sorted(set(made["needs"]))}]
    if made["outcome"] == "document-refused":
        return [file | {"outcome": "document-refused", "code": made["code"]}]
    if made["outcome"] != "built":
        return [file | {"outcome": made["outcome"]}]
    records: list[dict[str, Any]] = []
    converted: set[int] = set()  # a paragraph once, though parts share their root heading
    for part, built in enumerate(made["built"]):
        for s in built["sections"]:
            if s["refusal"] is None:
                converted |= _indices(s)
            record: dict[str, Any] = {
                "sha256": file["sha256"],
                "document": document,
                "part": part,
                "section": s["key"],
                "chars": sum(len(body.paragraphs[i].text) for i in _indices(s)),
            }
            if s["refusal"] is None:
                drawn = canonical_json([s["narrative"], s["page"]]).encode("utf-8")
                record |= {"outcome": "carried", "contentSha256": hashlib.sha256(drawn).hexdigest()}
            else:
                record |= {
                    "outcome": "refused",
                    "code": s["refusal"]["code"],
                    "blockers": blockers(body, s),
                }
            records.append(record)
    carried = [r for r in records if r["outcome"] == "carried"]
    file |= {
        "outcome": "whole" if len(carried) == len(records) else "built-section-refused",
        "parts": made["parts"],
        "sections": len(records),
        "carried": len(carried),
        "carriedChars": sum(len(body.paragraphs[i].text) for i in converted),
    }
    return [file, *records]


def _top(counter: Counter[str], n: int = 15) -> list[list[Any]]:
    return [[k, v] for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def unlock(blocked: Sequence[Sequence[str]], steps: int = 20) -> list[dict[str, Any]]:
    """Greedy: the cause whose lifting makes the most files whole next, step by step.

    ``newlyWhole`` is how many of the files with a section refused the causes so far, together,
    would make whole. Ties go to the cause in the most files, then the first by name.
    """
    sets = [set(b) for b in blocked]
    per = Counter(c for s in sets for c in s)
    lifted: set[str] = set()
    order: list[dict[str, Any]] = []
    for _ in range(min(steps, len(per))):
        best = max(
            sorted(set(per) - lifted),
            key=lambda c: (sum(1 for s in sets if c in s and s <= lifted | {c}), per[c]),
        )
        lifted.add(best)
        order.append({"cause": best, "newlyWhole": sum(1 for s in sets if s <= lifted)})
    return order


def summarize(records: Sequence[Mapping[str, Any]], greedy: bool = True) -> dict[str, Any]:
    """The aggregates of some records: counts and codes, nothing of a file on its own."""
    files = [r for r in records if r["section"] is None]
    sections = [r for r in records if r["section"] is not None]
    outcomes = Counter(f["outcome"] for f in files)
    chars = sum(f["chars"] for f in files if f["chars"] is not None)
    converted = sum(f["carriedChars"] for f in files)
    carried = sum(1 for s in sections if s["outcome"] == "carried")
    out: dict[str, Any] = {
        "files": len(files),
        "buckets": {b: outcomes[b] for b in BUCKETS},
        "sections": {
            "carried": carried,
            "total": len(sections),
            "share": round(carried / len(sections), 4) if sections else None,
        },
        "characters": {
            "converted": converted,
            "total": chars,
            "coverage": round(converted / chars, 4) if chars else None,
            # Reader-refused files with no document part to count: in no total.
            "unmeasurable": sum(1 for f in files if f["chars"] is None),
        },
        "codes": {
            "reader": _top(Counter(f["code"] for f in files if f["outcome"] == "reader-refused")),
            "document": _top(
                Counter(f["code"] for f in files if f["outcome"] == "document-refused")
            ),
            "needs": _top(
                Counter(k for f in files if f["outcome"] == "needs-a-person" for k in f["needs"])
            ),
            "section": _top(Counter(s["code"] for s in sections if s["outcome"] == "refused")),
        },
    }
    if greedy:
        blocked: dict[tuple[str, str], set[str]] = {}
        for s in sections:
            if s["outcome"] == "refused":
                blocked.setdefault((s["sha256"], s["document"]), set()).update(s["blockers"])
        out["unlock"] = unlock([sorted({public(c) for c in v}) for _, v in sorted(blocked.items())])
    return out


def versions(root: Path) -> dict[str, str]:
    """What measured: the reader's, the builder's and the norm's versions, and the commit."""
    git = ["git", "-C", str(root)]
    head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    dirty = subprocess.run(
        [*git, "status", "--porcelain"], capture_output=True, text=True, check=False
    )
    commit = head.stdout.strip() or "unknown"
    return {
        "builder": word_epi.WORD_EPI_VERSION,
        "commit": commit + ("-dirty" if dirty.stdout.strip() else ""),
        "fidelityNorm": NORMALIZATION_VERSION,
        "leafletStructurer": leaflet.LEAFLET_VERSION,
        "reader": READER_VERSION,
        "structurer": STRUCTURE_VERSION,
    }


def quietly(job: tuple[str, str, str]) -> list[dict[str, Any]] | None:
    """``measure``, or None where it fails: nothing of the failure, not even its file's path."""
    try:
        return measure(job)
    except Exception:  # noqa: BLE001 - a hold-out failure is counted, never shown
        return None


# A hold-out group of fewer files than this is reported as "<5" only: its aggregates would be
# nearly a file's own results.
MIN_CELL = 5


def _cell(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    files = sum(1 for r in records if r["section"] is None)
    return summarize(records, greedy=False) if files >= MIN_CELL else {"files": f"<{MIN_CELL}"}


def coverage(arguments: argparse.Namespace) -> int:
    """Writes the coverage records and summary (the module docstring)."""
    root = arguments.root.resolve()
    folders = (arguments.smpc, arguments.pl)
    if arguments.holdout is None and any(
        (f / "manifest.json").exists() or (f.parent / "manifest.json").exists() for f in folders
    ):
        raise SystemExit("a hold-out folder (a manifest.json beside it): measure it --holdout")
    jobs = [
        (str(path), document, str(root))
        for document, folder in zip(("smpc", "pl"), folders, strict=True)
        for path in sorted(folder.glob("*.docx"))
    ]
    with ProcessPoolExecutor(arguments.jobs) as pool:
        got = list(pool.map(measure if arguments.holdout is None else quietly, jobs))
    failed = sum(1 for m in got if m is None)
    kept = [(job, m) for job, m in zip(jobs, got, strict=True) if m is not None]
    kept.sort(key=lambda jm: (jm[0][1], jm[1][0]["sha256"]))
    records = [r for _, m in kept for r in m]
    arguments.out.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"versions": versions(root)}
    if arguments.holdout is None:
        for document in ("smpc", "pl"):
            summary[document] = summarize([r for r in records if r["document"] == document])
        summary["all"] = summarize(records)
        lines = "".join(canonical_json(r) + "\n" for r in records)
        (arguments.out / "sections.jsonl").write_text(lines, encoding="utf-8")
    else:
        manifest = {f["file"]: f["kind"] for f in _load(arguments.holdout)["files"]}
        if set(manifest.values()) - {"new", "update"}:
            raise SystemExit("a hold-out manifest kind other than new or update")
        if any(Path(path).name not in manifest for path, _, _ in jobs):
            raise SystemExit("a hold-out file the manifest does not list")
        kinds = {m[0]["sha256"]: manifest[Path(job[0]).name] for job, m in kept}
        summary["holdout"] = {
            kind: _cell([r for r in records if kinds[r["sha256"]] == kind])
            for kind in ("new", "update")
        } | {"all": _cell(records), "failed": failed}
    text = canonical_json(summary) + "\n"
    (arguments.out / "summary.json").write_text(text, encoding="utf-8")
    return 0


def _records(folder: Path) -> list[dict[str, Any]]:
    text = (folder / "sections.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def regress(before: Path, after: Path) -> dict[str, Any]:
    """What got worse from ``before`` to ``after`` (two ``coverage`` folders), by sha256."""

    def files(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        return {(r["sha256"], r["document"]): r for r in records if r["section"] is None}

    def sections(records: list[dict[str, Any]]) -> dict[tuple[str, str, int, str], dict[str, Any]]:
        return {
            (r["sha256"], r["document"], r["part"], r["section"]): r
            for r in records
            if r["section"] is not None
        }

    old, new = _records(before), _records(after)
    worse = []
    now_files = files(new)
    for key, was in sorted(files(old).items()):
        now = now_files.get(key)
        if now is None:
            worse.append(
                {"sha256": key[0], "document": key[1], "before": was["outcome"], "after": None}
            )
            continue
        rank = (BUCKETS.index(was["outcome"]), -was.get("carried", 0))
        if (BUCKETS.index(now["outcome"]), -now.get("carried", 0)) > rank:
            worse.append(
                {
                    "sha256": key[0],
                    "document": key[1],
                    "before": was["outcome"],
                    "after": now["outcome"],
                }
            )
    lost = []
    later = sections(new)
    for where, was in sorted(sections(old).items()):
        if was["outcome"] != "carried":
            continue
        now = later.get(where)
        if now is None:
            change = "gone"
        elif now["outcome"] != "carried":
            change = f"refused: {now['code']}"
        elif now["contentSha256"] != was["contentSha256"]:
            change = "changed"
        else:
            continue
        lost.append(
            {
                "sha256": where[0],
                "document": where[1],
                "part": where[2],
                "section": where[3],
                "change": change,
            }
        )
    return {
        "versions": {
            "before": _load(before / "summary.json")["versions"],
            "after": _load(after / "summary.json")["versions"],
        },
        "worseFiles": worse,
        "sections": lost,
    }


def main(argv: list[str] | None = None) -> int:
    """Writes the scoreboard.

    Returns:
        The exit status: 0 when written.
    """
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["coverage"]:
        parser = argparse.ArgumentParser(prog="scoreboard.py coverage", description=__doc__)
        parser.add_argument("--smpc", type=Path, required=True, help="the SmPC cuts' folder")
        parser.add_argument("--pl", type=Path, required=True, help="the leaflet cuts' folder")
        parser.add_argument("--out", type=Path, required=True, help="the folder written")
        parser.add_argument("--root", type=Path, default=ROOT, help="the repository measured")
        parser.add_argument("--holdout", type=Path, help="the hold-out's manifest: aggregates only")
        parser.add_argument("--jobs", type=int, default=2, help="worker processes (default 2)")
        return coverage(parser.parse_args(argv[1:]))
    if argv[:1] == ["regress"]:
        parser = argparse.ArgumentParser(prog="scoreboard.py regress", description=__doc__)
        parser.add_argument("before", type=Path)
        parser.add_argument("after", type=Path)
        arguments = parser.parse_args(argv[1:])
        try:
            found = regress(arguments.before, arguments.after)
        except (OSError, ValueError, KeyError, TypeError) as error:
            # 2, not 1: 1 says only that something regressed.
            sys.stderr.write(f"regress: unreadable input ({type(error).__name__}): {error}\n")
            return 2
        sys.stdout.write(canonical_json(found) + "\n")
        return 1 if found["worseFiles"] or found["sections"] else 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--keys", type=Path, help="answer keys: EMA ePI Bundles named as the files")
    parser.add_argument("--no-drawing", action="store_true", help="do not ask Chrome")
    parser.add_argument(
        "--view", choices=("accepted", "original"), help="measure a tracked document by this view"
    )
    parser.add_argument("--out", type=Path, help="write here instead of standard output")
    parser.add_argument(
        "--document", choices=("smpc", "pl"), default="smpc", help="the part each file is read for"
    )
    arguments = parser.parse_args(argv)
    chrome = None if arguments.no_drawing else browser.find_chrome()
    entries = [
        score(p, arguments.keys, chrome, arguments.view, arguments.document)
        for p in sorted(arguments.folder.glob("*.docx"))
    ]
    built = [e for e in entries if e["outcome"] == "built"]
    totals: dict[str, Any] = {
        "files": len(entries),
        "outcomes": dict(Counter(e["outcome"] for e in entries)),
        "sections": sum(e["sections"] for e in built),
        "carried": sum(e["carried"] for e in built),
        "refused": dict(sum((Counter(e["refused"]) for e in built), Counter[str]())),
        "readerRefusals": dict(
            Counter(e["code"] for e in entries if e["outcome"] == "reader-refused")
        ),
        "key": dict(
            sum((Counter(e["key"]["result"]) for e in built if "key" in e), Counter[str]())
        ),
    }
    text = (
        canonical_json({"builder": word_epi.WORD_EPI_VERSION, "totals": totals, "files": entries})
        + "\n"
    )
    if arguments.out is not None:
        arguments.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
