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
from collections.abc import Callable
from pathlib import Path

from label_docx.word import word_version, word_views
from numbering_cases import (
    Case,
    abstract,
    field,
    files,
    lvl,
    notes,
    num,
    package,
    para,
    words,
    write,
)

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


def table(*rows: str, props: str = "") -> str:
    """A one-column table of ``rows`` (each a row's ``w:tr`` content after its properties)."""
    grid = "<w:tblGrid><w:gridCol w:w='4000'/></w:tblGrid>"
    return f"<w:tbl><w:tblPr>{props}</w:tblPr>{grid}{''.join(rows)}</w:tbl>"


def row(*cells: str, props: str = "") -> str:
    """A row of cells, each cell's paragraphs given."""
    inner = "".join(f"<w:tc>{c}</w:tc>" for c in cells)
    return f"<w:tr>{f'<w:trPr>{props}</w:trPr>' if props else ''}{inner}</w:tr>"


BEGIN, SEPARATE, END = (
    f'<w:r><w:fldChar w:fldCharType="{k}"/></w:r>' for k in ("begin", "separate", "end")
)
LINK = ' HYPERLINK "https://www.ema.europa.eu" '


def code(text: str) -> str:
    """A run of field code."""
    return f'<w:r><w:instrText xml:space="preserve">{text}</w:instrText></w:r>'


def apart(change: Callable[..., str], *runs: str) -> str:
    """Each of ``runs`` in a change of its own, as Word writes changes made at different times."""
    return "".join(change(run, key=20 + i) for i, run in enumerate(runs))


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
    """A case with the list and the styles every case shares, unless it brings its own."""
    numbering = str(parts.pop("numbering", LIST))
    return Case(question, numbering, body, **{"styles": STYLES, **parts})  # type: ignore[arg-type]


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
        "A deleted paragraph mark before a table: the text joins the first cell.",
        para(words("before"), props=mark("del"))
        + "<w:tbl><w:tblGrid><w:gridCol w:w='4000'/></w:tblGrid><w:tr><w:tc><w:p>"
        + words("cell")
        + "</w:p></w:tc></w:tr></w:tbl>"
        + para(words("after")),
    ),
    "mark-deleted-before-nested-table": case(
        "A deleted paragraph mark before a table whose first cell starts with a table.",
        para(words("before"), props=mark("del"))
        + table(row(table(row(para(words("inner")))) + para(words("outer"))))
        + para(words("after")),
    ),
    "mark-deleted-before-table-styled": case(
        "A deleted mark before a table: whose properties the joined paragraph keeps.",
        para(words("a quote"), props=f'<w:pStyle w:val="Quote"/>{mark("del")}')
        + table(row(para(words(" in a heading"), props='<w:pStyle w:val="Heading1"/>')))
        + para(words("after")),
    ),
    "mark-deleted-last-in-cell": case(
        "The mark of a cell's last paragraph deleted: a cell's end cannot be joined.",
        table(
            row(
                para(words("first"), props=mark("del", 10)) + para(words("last"), props=mark("del"))
            )
        )
        + para(words("after")),
    ),
    "mark-deleted-last-in-body": case(
        "The body's last paragraph mark deleted.",
        para(words("one")) + para(words("last"), props=mark("del")),
    ),
    "mark-deleted-section-end": case(
        "A deleted mark that ends a section.",
        para(words("first section"), props=f"<w:sectPr/>{mark('del')}") + para(words("second")),
    ),
    "row-deleted": case(
        "A table row deleted.",
        table(
            row(para(words("kept"))),
            row(para(dele(words("gone"))), props=f'<w:del w:id="4" {WHO}/>'),
            row(para(words("last"))),
        )
        + para(words("after")),
    ),
    "format-table": case(
        "Table, row and cell properties changed: the original has the former ones.",
        table(
            row(
                para(words("cell")).replace(
                    "<w:p>",
                    '<w:tcPr><w:shd w:val="clear" w:fill="D9D9D9"/>'
                    f'<w:tcPrChange w:id="5" {WHO}><w:tcPr/></w:tcPrChange></w:tcPr><w:p>',
                    1,
                ),
                props=f'<w:cantSplit/><w:trPrChange w:id="6" {WHO}><w:trPr/></w:trPrChange>',
            ),
            props=f'<w:jc w:val="center"/><w:tblPrChange w:id="7" {WHO}><w:tblPr/></w:tblPrChange>',
        )
        + para(words("after")),
    ),
    "format-section": case(
        "Section properties changed: the original has the former ones.",
        para(words("body")),
        final='<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
        f'<w:sectPrChange w:id="8" {WHO}><w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        "</w:sectPr></w:sectPrChange></w:sectPr>",
    ),
    "table-deleted-whole": case(
        "Every row of a table deleted: whether the table goes, and how later tables count.",
        table(row(para(dele(words("gone"))), props=f'<w:del w:id="4" {WHO}/>'))
        + para(words("between"))
        + table(row(para(words("kept"))))
        + para(words("after")),
    ),
    "table-inserted-whole": case(
        "Every row of a table inserted: the original has no table there.",
        table(row(para(ins(words("new"))), props=f'<w:ins w:id="4" {WHO}/>'))
        + para(words("between"))
        + table(row(para(words("kept"))))
        + para(words("after")),
    ),
    "mark-deleted-before-table-first-row-deleted": case(
        "A deleted mark before a table whose first row is deleted: which paragraph it joins.",
        para(words("before"), props=mark("del"))
        + table(
            row(para(dele(words("first row"))), props=f'<w:del w:id="4" {WHO}/>'),
            row(para(words("second row"))),
        )
        + para(words("after")),
    ),
    "footnote-reference-deleted": case(
        "A footnote's reference deleted: whether the note goes with it.",
        para(
            words("Body"),
            dele(
                '<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr>'
                '<w:footnoteReference w:id="1"/></w:r>'
            ),
            words(" text."),
        ),
        footnotes=notes("footnote", 1),
    ),
    "footnote-reference-inserted": case(
        "A footnote's reference inserted: whether the original has the note.",
        para(
            words("Body"),
            ins(
                '<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr>'
                '<w:footnoteReference w:id="1"/></w:r>'
            ),
            words(" text."),
        ),
        footnotes=notes("footnote", 1),
    ),
    "content-control-emptied": case(
        "A content control whose whole content is inserted: what the original shows (the reader "
        "refuses it).",
        '<w:sdt><w:sdtPr><w:alias w:val="Name"/></w:sdtPr><w:sdtContent>'
        + para(ins(words("all new")))
        + "</w:sdtContent></w:sdt>"
        + para(words("after")),
    ),
    "format-section-note-numbers": case(
        "A section's footnote numbering changed: which marks each view draws.",
        para(words("Body"), '<w:r><w:footnoteReference w:id="1"/></w:r>'),
        footnotes=notes("footnote", 1),
        final='<w:sectPr><w:footnotePr><w:numFmt w:val="lowerRoman"/></w:footnotePr>'
        f'<w:sectPrChange w:id="8" {WHO}><w:sectPr><w:footnotePr><w:numFmt w:val="decimal"/>'
        "</w:footnotePr></w:sectPr></w:sectPrChange></w:sectPr>",
    ),
    "style-definition-changed": case(
        "A style's definition changed: whether Reject All restores it (the reader refuses it).",
        para(words("styled"), props='<w:pStyle w:val="Changed"/>'),
        styles=STYLES + '<w:style w:type="paragraph" w:styleId="Changed"><w:name w:val="Changed"/>'
        '<w:basedOn w:val="Normal"/><w:rPr><w:b/>'
        f'<w:rPrChange w:id="9" {WHO}><w:rPr><w:i/></w:rPr></w:rPrChange></w:rPr></w:style>',
    ),
    "style-paragraph-definition-changed": case(
        "A paragraph style's definition changed (its list and alignment): whether Reject All "
        "restores it.",
        para(words("styled"), props='<w:pStyle w:val="Listed"/>') + para(words("plain")),
        styles=STYLES + '<w:style w:type="paragraph" w:styleId="Listed"><w:name w:val="Listed"/>'
        '<w:basedOn w:val="Normal"/><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr>'
        f'<w:pPrChange w:id="9" {WHO}><w:pPr><w:jc w:val="center"/></w:pPr></w:pPrChange>'
        "</w:pPr></w:style>",
    ),
    "list-definition-changed": case(
        "A list level's definition changed (its format): whether Reject All restores it.",
        para(words("item"), props=numbered()) + para(words("item"), props=numbered()),
        numbering=abstract(
            1,
            '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="upperRoman"/>'
            '<w:lvlText w:val="%1."/><w:rPr><w:b/>'
            f'<w:rPrChange w:id="9" {WHO}><w:rPr><w:i/></w:rPr></w:rPrChange></w:rPr></w:lvl>',
        )
        + num(1, 1),
    ),
    "row-inserted": case(
        "A table row inserted: the original has no such row.",
        "<w:tbl><w:tblGrid><w:gridCol w:w='4000'/></w:tblGrid><w:tr><w:tc><w:p>"
        + words("kept")
        + "</w:p></w:tc></w:tr><w:tr><w:trPr>"
        + f'<w:ins w:id="4" {WHO}/></w:trPr><w:tc><w:p>'
        + ins(words("new row"))
        + "</w:p></w:tc></w:tr></w:tbl>"
        + para(words("after")),
    ),
    "field-inserted-apart": case(
        "A field inserted in pieces, each its own change: whether the original has none of it.",
        para(
            words("See "),
            apart(ins, BEGIN, code(LINK), SEPARATE, words("the site"), END),
            words("."),
        ),
    ),
    "field-deleted-apart": case(
        "A field deleted in pieces, each its own change: whether the accepted view has none of it.",
        para(
            words("See "),
            apart(dele, BEGIN, code(LINK), SEPARATE, words("the site"), END),
            words("."),
        ),
    ),
    "field-inserted-then-deleted-apart": case(
        "A field inserted and deleted in pieces: in neither view.",
        para(
            words("See "),
            apart(
                lambda run, key: ins(dele(run, key=key + 20), key=key),
                BEGIN,
                code(LINK),
                SEPARATE,
                words("the site"),
                END,
            ),
            words("."),
        ),
    ),
    "field-wrapped-around-text": case(
        "A field inserted around code and text already there: what the original shows of them.",
        para(
            words("See "),
            ins(BEGIN, code(' HYPERLINK "'), key=20),
            code("http"),
            ins(code("s"), key=21),
            code("://www.ema.europa.eu"),
            ins(code('" '), SEPARATE, key=22),
            words("www.ema"),
            ins(words(".europa"), key=23),
            words(".eu"),
            ins(END, key=24),
            words("."),
        ),
    ),
    "field-end-inserted": case(
        "Only a field's end inserted: what the original, a field with no end, shows.",
        para(
            words("See "),
            BEGIN,
            code(LINK),
            SEPARATE,
            words("the"),
            ins(words(" site"), END, key=20),
            words(" today."),
        ),
    ),
    "mark-deleted-before-table-deleted-whole": case(
        "An empty paragraph and the table after it deleted: which paragraph the mark joins.",
        para(props=mark("del"))
        + table(row(para(dele(words("gone"))), props=f'<w:del w:id="4" {WHO}/>'))
        + para(words("after")),
    ),
    "mark-and-text-deleted-before-table-deleted-whole": case(
        "A paragraph's text and mark and the table after it deleted: what the next keeps.",
        para(dele(words("a heading")), props=f'<w:pStyle w:val="Heading1"/>{mark("del")}')
        + table(row(para(dele(words("gone"))), props=f'<w:del w:id="4" {WHO}/>'))
        + para(words("after"), props='<w:pStyle w:val="Quote"/>'),
    ),
    "mark-inserted-before-table-inserted-whole": case(
        "A mark inserted after text, then a table inserted: what the original joins.",
        para(words("text"), props=f'<w:pStyle w:val="Quote"/>{mark("ins")}')
        + table(row(para(ins(words("new"))), props=f'<w:ins w:id="4" {WHO}/>'))
        + para(words(" after"), props='<w:pStyle w:val="Heading1"/>'),
    ),
    "mark-deleted-before-bookmark-end": case(
        "A deleted mark before a bookmark's end between paragraphs: where the end stands, joined.",
        para('<w:bookmarkStart w:id="5" w:name="Mark"/>', words("a"), props=mark("del"))
        + '<w:bookmarkEnd w:id="5"/>'
        + para(words("b")),
    ),
    "mark-inserted-before-bookmark-end": case(
        "An inserted mark before a bookmark's end between paragraphs: the original joins them.",
        para('<w:bookmarkStart w:id="5" w:name="Mark"/>', words("a"), props=mark("ins"))
        + '<w:bookmarkEnd w:id="5"/>'
        + para(ins(words("b"))),
    ),
    "mark-inserted-empty-before-table-inserted-whole": case(
        "An empty paragraph and a table after it inserted: the original has neither.",
        para(words("before"))
        + para(props=mark("ins"))
        + table(row(para(ins(words("new"))), props=f'<w:ins w:id="4" {WHO}/>'))
        + para(words("after")),
    ),
}


def wanted() -> dict[Path, bytes]:
    """Every file of the corpus set, with its bytes."""
    return files(
        FOLDER, CASES, "Synthetic tracked-change cases written by scripts/tracked_cases.py."
    )


def record_word() -> None:
    """Have Word make each case's views, into ``word/``, with their hashes."""
    folder = FOLDER / "word"
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / "sources.json"
    kept = json.loads(manifest_path.read_text("utf-8"))["sources"] if manifest_path.exists() else []
    case_hashes = {f"{n}.docx": hashlib.sha256(package(c)).hexdigest() for n, c in CASES.items()}
    # Word's views are kept for every case they were made of, as it is now: Word's files are
    # not the same bytes from run to run, so only new or changed cases are asked again.
    sources = [s for s in kept if case_hashes.get(s["case"]) == s.get("caseSha256")]
    asked = {s["case"] for s in sources}
    for name in CASES:
        if f"{name}.docx" in asked:
            continue
        for view, data in word_views(FOLDER / f"{name}.docx").items():
            path = folder / f"{name}.{view}.docx"
            path.write_bytes(data)
            sources.append(
                {
                    "case": f"{name}.docx",
                    "caseSha256": case_hashes[f"{name}.docx"],
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
    for stale in set(folder.glob("*.docx")) - {folder / s["file"] for s in sources}:
        stale.unlink()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")


def main() -> int:
    """Write the cases, or with --check report whether they are current, or with --word ask Word."""
    parser = argparse.ArgumentParser(description="Write or check corpus/tracked-cases.")
    parser.add_argument("--check", action="store_true", help="fail rather than write")
    parser.add_argument("--word", action="store_true", help="have Word make each case's views")
    args = parser.parse_args()
    status = write(FOLDER, wanted(), args.check)
    if args.word and not args.check:
        record_word()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
