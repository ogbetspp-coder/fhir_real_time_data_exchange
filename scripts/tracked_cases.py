"""Write, or check, the synthetic tracked-change corpus (corpus/tracked-cases), and Word's views.

    uv run --frozen python scripts/tracked_cases.py           # write the .docx files
    uv run --frozen python scripts/tracked_cases.py --check   # fail if they would change
    uv run --frozen python scripts/tracked_cases.py --word    # have Word make each case's views

Each case is a small .docx with tracked changes, built to put one rule of accepting or rejecting
them to Word: which properties two joined paragraphs keep, how a list counts once an item is
gone, what a changed font was. ``--word`` has Microsoft Word accept every change of each case
and save it, then reject every change and save it, into ``word/`` (``NAME.accepted.docx``,
``NAME.original.docx``); ``tests/test_tracked.py`` holds the reader's views of each case to its
reading of Word's files. Word's files are not the same bytes from run to run, so the corpus keeps
the ones Word wrote, with their hashes (``word/sources.json``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from label_docx.word import word_version, word_views
from numbering_cases import Case, abstract, field, lvl, notes, num, package, para, words

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "corpus" / "tracked-cases"
WHO = 'w:author="Author" w:date="2026-01-01T00:00:00Z"'


def ins(*pieces: str, key: int = 1) -> str:
    """Inserted runs."""
    return f'<w:ins w:id="{key}" {WHO}>{"".join(pieces)}</w:ins>'


def dele(*pieces: str, key: int = 2) -> str:
    """Deleted runs: each ``w:t`` of ``pieces`` becomes ``w:delText``, as Word writes them."""
    text = "".join(pieces).replace("<w:t ", "<w:delText ").replace("</w:t>", "</w:delText>")
    text = text.replace("<w:instrText ", "<w:delInstrText ").replace(
        "</w:instrText>", "</w:delInstrText>"
    )
    return f'<w:del w:id="{key}" {WHO}>{text}</w:del>'


def mark(kind: str, key: int = 9) -> str:
    """The paragraph mark's properties, the mark inserted or deleted (or moved)."""
    return f'<w:rPr><w:{kind} w:id="{key}" {WHO}/></w:rPr>'


def numbered(level: int = 0, key: int = 1) -> str:
    """Paragraph properties putting the paragraph in list ``key`` at ``level``."""
    return f'<w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{key}"/></w:numPr>'


LIST = abstract(1, lvl(0), lvl(1, fmt="lowerLetter", text="%2)")) + num(1, 1)
STYLES = (
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
    "</w:style>"
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
    '<w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/></w:rPr>'
    "</w:style>"
    '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/>'
    '<w:basedOn w:val="Normal"/><w:rPr><w:i/></w:rPr></w:style>'
)
SYMBOL = '<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol" w:hint="default"/>'


def case(question: str, body: str, **parts: object) -> Case:
    """A case with the list and the styles every case shares."""
    return Case(question, LIST, body, styles=STYLES, **parts)  # type: ignore[arg-type]


CASES: dict[str, Case] = {
    "text-inserted-deleted": case(
        "A word replaced: one view has each.",
        para(words("Store below "), dele(words("25")), ins(words("30")), words(" °C.")),
    ),
    "mark-deleted-styles": case(
        "A deleted paragraph mark: whose properties the joined paragraph keeps.",
        para(words("First, a quote"), props=f'<w:pStyle w:val="Quote"/>{mark("del")}')
        + para(words(" joined to a heading."), props='<w:pStyle w:val="Heading1"/>'),
    ),
    "mark-inserted-styles": case(
        "An inserted paragraph mark: the original joins the two paragraphs.",
        para(words("A heading"), props=f'<w:pStyle w:val="Heading1"/>{mark("ins")}')
        + para(ins(words("then a new quote.")), props='<w:pStyle w:val="Quote"/>'),
    ),
    "mark-deleted-list": case(
        "A list item joined to the next one: how the list counts after it.",
        para(words("one"), props=numbered() + mark("del"))
        + para(words(" and two"), props=numbered())
        + para(words("three"), props=numbered())
        + para(words("four"), props=numbered()),
    ),
    "mark-deleted-list-to-plain": case(
        "A list item joined to a plain paragraph: the joined one is not in the list.",
        para(words("one"), props=numbered())
        + para(words("two"), props=numbered() + mark("del"))
        + para(words(" plain"))
        + para(words("three"), props=numbered()),
    ),
    "item-deleted-whole": case(
        "A list item deleted, text and mark: the items after it count one less.",
        para(words("one"), props=numbered())
        + para(dele(words("two")), props=numbered() + mark("del"))
        + para(words("three"), props=numbered(1))
        + para(words("four"), props=numbered()),
    ),
    "item-inserted-whole": case(
        "A list item inserted, text and mark: the items after it count one more.",
        para(words("one"), props=numbered() + mark("ins"))
        + para(ins(words("new")), props=numbered())
        + para(words("two"), props=numbered()),
    ),
    "marks-deleted-chain": case(
        "Three paragraphs joined by two deleted marks.",
        para(words("a"), props=f'<w:pStyle w:val="Heading1"/>{mark("del")}')
        + para(words("b"), props=f'<w:pStyle w:val="Quote"/>{mark("del", 10)}')
        + para(words("c")),
    ),
    "moved-text": case(
        "Text moved from one paragraph to another.",
        para(
            f'<w:moveFromRangeStart w:id="5" {WHO} w:name="move1"/>',
            f'<w:moveFrom w:id="6" {WHO}>{words("moved ")}</w:moveFrom>',
            '<w:moveFromRangeEnd w:id="5"/>',
            words("stays."),
        )
        + para(
            words("Here: "),
            f'<w:moveToRangeStart w:id="7" {WHO} w:name="move1"/>',
            f'<w:moveTo w:id="8" {WHO}>{words("moved ")}</w:moveTo>',
            '<w:moveToRangeEnd w:id="7"/>',
        ),
    ),
    "format-bold": case(
        "Bold applied: the original has the former properties.",
        para(
            f'<w:r><w:rPr><w:b/><w:rPrChange w:id="3" {WHO}><w:rPr/></w:rPrChange></w:rPr>'
            "<w:t>now bold</w:t></w:r>",
            words(" text"),
        ),
    ),
    "format-symbol-font": case(
        "A run's font changed from Symbol: the original's m is drawn as mu, 50 \u03bcg not 50 mg.",
        para(
            words("50 "),
            f'<w:r><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>'
            f'<w:rPrChange w:id="3" {WHO}><w:rPr>{SYMBOL}</w:rPr></w:rPrChange></w:rPr>'
            "<w:t>m</w:t></w:r>",
            words("g"),
        ),
    ),
    "format-paragraph-style": case(
        "A paragraph's style changed: the original has the former style.",
        para(
            words("was a quote"),
            props=f'<w:pStyle w:val="Heading1"/><w:pPrChange w:id="3" {WHO}><w:pPr>'
            '<w:pStyle w:val="Quote"/></w:pPr></w:pPrChange>',
        ),
    ),
    "format-paragraph-numbering": case(
        "A paragraph put in a list: the original is not in it, and the list counts without it.",
        para(words("one"), props=numbered())
        + para(
            words("now two"),
            props=f'{numbered()}<w:pPrChange w:id="3" {WHO}><w:pPr/></w:pPrChange>',
        )
        + para(words("then"), props=numbered()),
    ),
    "field-deleted-whole": case(
        "A whole field deleted.",
        para(words("See "), dele(field("DOCPROPERTY Title", "the title")), words(".")),
    ),
    "field-separator-deleted": case(
        "Only a field's separator deleted (the reader refuses the change).",
        para(
            words("See "),
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> DOCPROPERTY Title </w:instrText></w:r>',
            dele('<w:r><w:fldChar w:fldCharType="separate"/></w:r>'),
            words("the title"),
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>',
        ),
    ),
    "inserted-then-deleted": case(
        "Text one author inserted and another deleted: in neither view.",
        para(words("a"), ins(dele(words("b")), words("c")), words("d")),
    ),
    "breaks-and-tabs-deleted": case(
        "A tab and a line break deleted with text.",
        para(words("a"), dele("<w:r><w:tab/></w:r>", words("b"), "<w:r><w:br/></w:r>"), words("c")),
    ),
    "footnote-text-changed": case(
        "A change inside a footnote.",
        para(words("Body"), '<w:r><w:footnoteReference w:id="1"/></w:r>'),
        footnotes=notes("footnote")
        + '<w:footnote w:id="1"><w:p><w:r><w:footnoteRef/></w:r>'
        + words(" note ")
        + dele(words("old"))
        + ins(words("new"))
        + "</w:p></w:footnote>",
    ),
    "header-text-changed": case(
        "A change in a header only.",
        para(words("Body")),
        final='<w:sectPr xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships"><w:headerReference w:type="default" r:id="rIdH1"/></w:sectPr>',
        stories=(
            ("rIdH1", "header", para(words("Header "), ins(words("new")), dele(words("old")))),
        ),
    ),
    "mark-deleted-before-table": case(
        "A deleted paragraph mark before a table (the reader refuses it).",
        para(words("before"), props=mark("del"))
        + "<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid><w:tr><w:tc><w:p>"
        + words("cell")
        + "</w:p></w:tc></w:tr></w:tbl>"
        + para(words("after")),
    ),
    "row-inserted": case(
        "A table row inserted (the reader refuses it).",
        "<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid><w:tr><w:tc><w:p>"
        + words("kept")
        + "</w:p></w:tc></w:tr><w:tr><w:trPr>"
        + f'<w:ins w:id="4" {WHO}/></w:trPr><w:tc><w:p>'
        + ins(words("new row"))
        + "</w:p></w:tc></w:tr></w:tbl>"
        + para(words("after")),
    ),
}


def wanted() -> dict[Path, bytes]:
    """Every file of the corpus set, with its bytes."""
    out: dict[Path, bytes] = {}
    sources = []
    for name, item in CASES.items():
        data = package(item)
        out[FOLDER / f"{name}.docx"] = data
        sources.append(
            {
                "name": item.question,
                "file": f"{name}.docx",
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    manifest = {
        "schemaVersion": "1.0.0",
        "note": "Synthetic tracked-change cases written by scripts/tracked_cases.py.",
        "sources": sources,
    }
    out[FOLDER / "sources.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    return out


def record_word() -> None:
    """Have Word make each case's views, into ``word/``, with their hashes."""
    folder = FOLDER / "word"
    folder.mkdir(parents=True, exist_ok=True)
    sources = []
    for name in CASES:
        for view, data in word_views(FOLDER / f"{name}.docx").items():
            path = folder / f"{name}.{view}.docx"
            path.write_bytes(data)
            sources.append(
                {
                    "case": f"{name}.docx",
                    "view": view,
                    "file": path.name,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                }
            )
        sys.stdout.write(f"{name}: Word's views saved\n")
    manifest = {
        "schemaVersion": "1.0.0",
        "application": word_version(),
        "note": "Each case with every change accepted, and every change rejected, by Word.",
        "sources": sources,
    }
    (folder / "sources.json").write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")


def main() -> int:
    """Write the cases, or with --check report whether they are current, or with --word ask Word."""
    parser = argparse.ArgumentParser(description="Write or check corpus/tracked-cases.")
    parser.add_argument("--check", action="store_true", help="fail rather than write")
    parser.add_argument("--word", action="store_true", help="have Word make each case's views")
    args = parser.parse_args()
    files = wanted()
    present = set(FOLDER.glob("*.docx")) if FOLDER.exists() else set()
    stale = [p for p, data in files.items() if not p.exists() or p.read_bytes() != data]
    extra = sorted(present - set(files))
    if args.check:
        for path in [*stale, *extra]:
            sys.stderr.write(f"out of date: {path.relative_to(ROOT)}\n")
        return 1 if stale or extra else 0
    FOLDER.mkdir(parents=True, exist_ok=True)
    for path in extra:
        path.unlink()
    for path in stale:
        path.write_bytes(files[path])
        sys.stdout.write(f"wrote {path.relative_to(ROOT)}\n")
    if args.word:
        record_word()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
