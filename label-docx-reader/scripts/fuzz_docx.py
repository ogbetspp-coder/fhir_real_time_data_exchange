"""Generate .docx documents at random, for Microsoft Word to judge the reader on (macOS, Word).

    uv run --frozen python scripts/fuzz_docx.py --documents 40 --seed 1 FOLDER
    uv run --frozen python scripts/fuzz_docx.py --documents 6 --chapters 10 --seed 1 FOLDER
    uv run --frozen python scripts/fuzz_docx.py --documents 6 --chapters 10 --fields --seed 1 IN
    uv run --frozen python scripts/word_oracle.py update OUT IN/*.docx
    uv run --frozen python scripts/fuzz_docx.py --documents 6 --chapters 10 --stories --seed 1 F
    uv run --frozen python scripts/word_oracle.py compare FOLDER/*.docx

The numbering cases (``scripts/numbering_cases.py``) put one question each to Word; these mix
them. Each document combines what the reader computes rather than copies, where its rules are
Word's answers and nothing else: paragraph and character styles in ``basedOn`` chains, a table
style and the document defaults, each switching bold, italic, capitals and strike on or off;
direct formatting over them; lists of one to three definitions with random formats, level
texts, starts, restarts and overrides, shared between lists; and footnotes numbered by the
section's format and start. Tables have one to four rows, as table rows change how Word
counts. ``word_oracle.py compare`` then holds the reader to Word on every document: its list
labels, note marks, emphasis and print. A document the reader reads otherwise than Word is a
fault in the reader, made again from its seed and number.

Word takes about half a minute a document, most of it for the document rather than its size,
so ``--chapters N`` puts N generated documents' worth in one: each chapter with its own styles
and lists, under shared document defaults and footnotes. Word judges the whole document as
exactly as a small one.

``--fields`` adds what labels cross-refer with: captions numbered by SEQ, in each number format,
headings and STYLEREF to them, and REF and NOTEREF to bookmarked captions and note references.
Their results are placeholders; ``word_oracle.py update`` has Word update every field and save
the document, so the results are Word's own, in Word's own XML. The reader must then compute
each one as Word did: a result it calls stale there is a difference from Word.

``--stories`` makes each chapter a section with headers and footers of its own (default, first
page, even pages; some shared, some left to the previous section's), holding styled text, page
numbers and tables, and adds comments by several authors on the body's runs. Word's headers,
footers and comments are then compared with the reader's (``word.story_verdict``).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from label_docx import output
from numbering_cases import Case, abstract, bookmark, field, lvl, notes, num, package, para, words

TOGGLES = ("b", "i", "caps", "smallCaps", "strike", "dstrike")
FORMATS = ("decimal", "lowerLetter", "upperLetter", "lowerRoman", "upperRoman", "decimalZero")
NOTE_FORMATS = ("decimal", "lowerRoman", "upperRoman", "lowerLetter", "upperLetter", "chicago")
# Chapters drawn, at most, for one the reader reads.
_TRIES = 200
SEQ_FORMATS = ("", " \\* ARABIC", " \\* ROMAN", " \\* roman", " \\* ALPHABETIC", " \\* alphabetic")
AUTHORS = ("Reviewer", "QA", "Regulatory Affairs", "M. Example")
HEADINGS = (
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>'
)
WORDS = (
    "Store",
    "below",
    "25",
    "C",
    "Take",
    "one",
    "tablet",
    "daily",
    "with",
    "water",
    # What labels hold besides English: other scripts, accents, units and signs.
    "\u0394\u03b9\u03b1\u03c4\u03b7\u03c1\u03b5\u03af\u03c4\u03b5",
    "\u0422\u0430\u0431\u043b\u0435\u0442\u043a\u0430",
    "\u010desky",
    "\u00e9t\u00e9",
    "5\u00a0\u00b5g",
    "\u2264",
    "\u00b1",
    "37\u00a0\u00b0C",
    "\u00ae",
    "\u2122",
    "\u00bd",
)
# Elements that stand for a character, as labels hold them: no-break and soft hyphens, a tab,
# a line break, and Symbol characters (greater or equal, plus-minus, micro).
SPECIALS = (
    "<w:r><w:noBreakHyphen/></w:r>",
    "<w:r><w:softHyphen/></w:r>",
    "<w:r><w:tab/></w:r>",
    "<w:r><w:br/></w:r>",
    '<w:r><w:sym w:font="Symbol" w:char="F0B3"/></w:r>',
    '<w:r><w:sym w:font="Symbol" w:char="F0B1"/></w:r>',
    '<w:r><w:sym w:font="Symbol" w:char="F06D"/></w:r>',
)


class Document:
    """One generated document."""

    def __init__(
        self,
        rng: random.Random,
        chapter: int = 0,
        notes: list[int] | None = None,
        fields: bool = False,
        stories: bool = False,
    ) -> None:
        self.rng = rng
        self._last = 0  # the last list level drawn
        # A chapter's styles, table style and list ids are its own (chapter 0's as before).
        self.prefix = f"K{chapter}" if chapter else ""
        self.base = 10 * chapter
        self.paragraph_styles = [f"{self.prefix}P{i}" for i in range(rng.randint(1, 4))]
        self.character_styles = [f"{self.prefix}C{i}" for i in range(rng.randint(1, 3))]
        # The notes, bookmarks and comments so far, in every chapter: one count each, as one
        # document has.
        self.counter = [0, 0, 0] if notes is None else notes
        self.fields = fields
        self.stories = stories
        # This chapter's comments, as the comments part holds them.
        self.comments: list[str] = []
        # Bookmarks a REF or NOTEREF can name: captions, and note references.
        self.captions: list[str] = []
        self.marked_notes: list[str] = []

    def toggles(self, capitals: bool = True) -> str:
        """A random setting, on or off, of some of the toggles.

        Without capitals in a paragraph style: they reach the list label, which the reader
        refuses in capitals.
        """
        out = ""
        for name in TOGGLES if capitals else ("b", "i", "strike", "dstrike"):  # no caps, smallCaps
            roll = self.rng.random()
            if roll < 0.25:
                out += f"<w:{name}/>"
            elif roll < 0.4:
                out += f'<w:{name} w:val="0"/>'
            elif roll < 0.45:
                out += f'<w:{name} w:val="{self.rng.choice(["true", "1", "on", "false", "off"])}"/>'
        roll = self.rng.random()
        if roll < 0.15:
            align = self.rng.choice(["superscript", "subscript", "baseline"])
            out += f'<w:vertAlign w:val="{align}"/>'
        elif roll < 0.3:
            out += f'<w:u w:val="{self.rng.choice(["single", "double", "none"])}"/>'
        return out

    def defaults(self) -> str:
        """The document defaults."""
        defaults = f"<w:rPrDefault><w:rPr>{self.toggles(capitals=False)}</w:rPr></w:rPrDefault>"
        return f"<w:docDefaults>{defaults}</w:docDefaults>"

    def styles(self) -> str:
        """Paragraph and character styles in chains, and a table style."""
        rng = self.rng
        out = ""
        kinds = (("paragraph", self.paragraph_styles), ("character", self.character_styles))
        for kind, names in kinds:
            for index, name in enumerate(names):
                based = ""
                default = ' w:default="1"' if rng.random() < 0.2 else ""
                if index and rng.random() < 0.6:
                    based = f'<w:basedOn w:val="{rng.choice(names[:index])}"/>'
                out += (
                    f'<w:style w:type="{kind}"{default} w:styleId="{name}">'
                    f'<w:name w:val="{name}"/>{based}'
                    f"<w:rPr>{self.toggles(capitals=kind == 'character')}</w:rPr></w:style>"
                )
        name = f"{self.prefix}T"
        out += (
            f'<w:style w:type="table" w:styleId="{name}"><w:name w:val="{name}"/>'
            f"<w:rPr>{self.toggles(capitals=False)}</w:rPr></w:style>"
        )
        return out

    def numbering(self) -> tuple[str, str, list[int]]:
        """One to three list definitions, the lists naming them, and their ids."""
        rng = self.rng
        abstracts = ""
        count = rng.randint(1, 3)
        for key in range(1, count + 1):
            levels = []
            for level in range(3):
                fmt = rng.choice(FORMATS)
                texts = [f"%{level + 1}.", f"({'%' + str(level + 1)})", f"%{level + 1})"]
                if level:
                    texts.append(".".join(f"%{n + 1}" for n in range(level + 1)) + ".")
                extra = ""
                if level and rng.random() < 0.2:
                    # What Word writes: never (0), or after a level above the one directly above.
                    extra = f'<w:lvlRestart w:val="{rng.randint(0, level - 1)}"/>'
                # 0 only in decimal: in letters or roman the reader refuses it.
                start = rng.choice(
                    [None, 0, 1, 1, 1, 2, 5] if fmt.startswith("decimal") else [1, 1, 2, 5]
                )
                levels.append(lvl(level, fmt, rng.choice(texts), extra, start))
            abstracts += abstract(self.base + key, *levels)
        nums = ""
        ids = []
        for key in range(1, rng.randint(2, 4) + 1):
            override = ""
            if rng.random() < 0.3:
                level = rng.randint(0, 2)
                value = rng.randint(1, 9)
                override = (
                    f'<w:lvlOverride w:ilvl="{level}"><w:startOverride w:val="{value}"/>'
                    "</w:lvlOverride>"
                )
            nums += num(self.base + key, self.base + rng.randint(1, count), override)
            ids.append(self.base + key)
        return abstracts, nums, ids

    def run(self) -> str:
        """A run: a character style and direct toggles, maybe a footnote reference."""
        rng = self.rng
        style = ""
        if rng.random() < 0.4:
            style = f'<w:rStyle w:val="{rng.choice(self.character_styles)}"/>'
        properties = style + self.toggles()
        text = " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 3)))
        if rng.random() < 0.08:
            self.counter[0] += 1
            reference = f'<w:r><w:footnoteReference w:id="{self.counter[0]}"/></w:r>'
            if self.fields and rng.random() < 0.5:
                name = self.bookmark("_Note")
                self.marked_notes.append(name)
                reference = bookmark(self.counter[1], name, reference)
            return words(text) + reference
        prefix = f"<w:rPr>{properties}</w:rPr>" if properties else ""
        out = f'<w:r>{prefix}<w:t xml:space="preserve">{text} </w:t></w:r>'
        if self.stories and rng.random() < 0.06:
            self.counter[2] += 1
            key = self.counter[2]
            said = " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 4)))
            self.comments.append(
                f'<w:comment w:id="{key}" w:author="{rng.choice(AUTHORS)}" w:initials="X" '
                f'w:date="2026-01-02T03:04:05Z"><w:p><w:r><w:annotationRef/></w:r>'
                f"{words(said)}</w:p></w:comment>"
            )
            out = (
                f'<w:commentRangeStart w:id="{key}"/>{out}<w:commentRangeEnd w:id="{key}"/>'
                f'<w:r><w:commentReference w:id="{key}"/></w:r>'
            )
        return out

    def story(self) -> str:
        """A header's or footer's content: styled runs, maybe a page number, maybe a table."""
        rng = self.rng
        keep, self.stories = self.stories, False

        def line() -> str:
            pieces = [self.plain_run() for _ in range(rng.randint(1, 2))]
            if rng.random() < 0.4:
                pieces.append(words("Page ") + field("PAGE", "1"))
            style = rng.choice(self.paragraph_styles)
            return para(*pieces, props=f'<w:pStyle w:val="{style}"/>' if rng.random() < 0.5 else "")

        out = "".join(line() for _ in range(rng.randint(1, 2)))
        if rng.random() < 0.2:
            cells = "".join(f"<w:tc>{line()}</w:tc>" for _ in range(rng.randint(1, 2)))
            out += f"<w:tbl><w:tr>{cells}</w:tr></w:tbl>" + para(words("end"))
        self.stories = keep
        return out

    def plain_run(self) -> str:
        """A run as ``run`` makes, but never a note reference: headers hold none."""
        rng = self.rng
        style = ""
        if rng.random() < 0.4:
            style = f'<w:rStyle w:val="{rng.choice(self.character_styles)}"/>'
        properties = style + self.toggles()
        text = " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 3)))
        prefix = f"<w:rPr>{properties}</w:rPr>" if properties else ""
        return f'<w:r>{prefix}<w:t xml:space="preserve">{text} </w:t></w:r>'

    def bookmark(self, kind: str) -> str:
        """A new bookmark's name, its id the count so far."""
        self.counter[1] += 1
        return f"{kind}{self.prefix}x{self.counter[1]}"

    def field_paragraph(self) -> str | None:
        """A caption, a heading, or a paragraph with a REF, NOTEREF or STYLEREF; or None."""
        rng = self.rng
        roll = rng.random()
        if roll < 0.06:
            identifier = rng.choice(["Table", "Figure"])
            name = self.bookmark("_Ref")
            self.captions.append(name)
            number = field(f"SEQ {identifier}{rng.choice(SEQ_FORMATS)}", "0")
            return para(bookmark(self.counter[1], name, words(f"{identifier} "), number))
        if roll < 0.10:
            level = rng.randint(1, 2)
            return para(words(rng.choice(WORDS)), props=f'<w:pStyle w:val="Heading{level}"/>')
        if roll < 0.14 and self.captions:
            return para(words("See "), field(f"REF {rng.choice(self.captions)} \\h", "x"))
        if roll < 0.16:
            return para(field(f"STYLEREF {rng.randint(1, 2)}", "x"))
        if roll < 0.18 and self.marked_notes:
            name = rng.choice(self.marked_notes)
            return para(words("see note "), field(f"NOTEREF {name} \\h", "0"))
        return None

    def level(self) -> int:
        """The next list level: mostly one step from the last, as authors write lists."""
        last = self._last
        roll = self.rng.random()
        if roll < 0.1:
            nxt = self.rng.randint(0, 2)
        elif roll < 0.45:
            nxt = min(last + 1, 2)
        elif roll < 0.75:
            nxt = last
        else:
            nxt = max(last - 1, 0)
        self._last = nxt
        return nxt

    def paragraph(self, numbers: list[int]) -> str:
        """A paragraph, maybe styled, maybe in a list."""
        rng = self.rng
        props = ""
        if rng.random() < 0.5:
            props += f'<w:pStyle w:val="{rng.choice(self.paragraph_styles)}"/>'
        if rng.random() < 0.6:
            props += (
                f'<w:numPr><w:ilvl w:val="{self.level()}"/>'
                f'<w:numId w:val="{rng.choice(numbers)}"/></w:numPr>'
            )
        pieces = [self.run() for _ in range(rng.randint(1, 3))]
        if rng.random() < 0.15:
            pieces.insert(rng.randint(0, len(pieces)), rng.choice(SPECIALS))
        return para(*pieces, props=props)

    def body(self, numbers: list[int]) -> str:
        """Paragraphs, some in tables of one to four rows in the table style."""
        rng = self.rng
        # With fields, a chapter opens with a heading of each level, so STYLEREF finds one.
        out = para(words("Part"), props='<w:pStyle w:val="Heading1"/>') if self.fields else ""
        out += para(words("Section"), props='<w:pStyle w:val="Heading2"/>') if self.fields else ""
        for _ in range(rng.randint(12, 30)):
            extra = self.field_paragraph() if self.fields else None
            if extra is not None:
                out += extra
            elif rng.random() < 0.15:
                count = rng.randint(1, 2)
                rows = ""
                for _ in range(rng.randint(1, 4)):
                    cells = "".join(
                        "<w:tc>"
                        + "".join(self.paragraph(numbers) for _ in range(rng.randint(1, 2)))
                        + "</w:tc>"
                        for _ in range(count)
                    )
                    rows += f"<w:tr>{cells}</w:tr>"
                table = f'<w:tblPr><w:tblStyle w:val="{self.prefix}T"/></w:tblPr>'
                grid = "<w:tblGrid>" + '<w:gridCol w:w="2000"/>' * count + "</w:tblGrid>"
                out += f"<w:tbl>{table}{grid}{rows}</w:tbl>"
            else:
                out += self.paragraph(numbers)
        return out


def _certified(case: Case, fields: bool = False) -> bool:
    """Whether the reader reads ``case`` and the check certifies it.

    With fields, a result refused as stale is let through: Word will compute them.
    """
    value = json.loads(output.read(package(case))[0])
    return "refusal" not in value or (fields and value["refusal"]["code"] == "stale-field")


def _section(rng: random.Random, first: Document, parts: list[tuple[str, str, str]]) -> str:
    """A section's header and footer references: to new parts, to shared ones, or none."""
    out = ""
    for kind in ("header", "footer"):
        for type_ in ("default", "first", "even"):
            roll = rng.random()
            mine = [key for key, k, _ in parts if k == kind]
            if roll < 0.35:
                key = f"{kind[0]}{len(parts) + 1}"
                parts.append((key, kind, first.story()))
            elif roll < 0.5 and mine:
                key = rng.choice(mine)
            else:
                continue
            out += f'<w:{kind}Reference w:type="{type_}" r:id="{key}"/>'
    return out


def generated(
    rng: random.Random, chapters: int = 1, fields: bool = False, stories: bool = False
) -> Case:
    """One document of ``chapters`` chapters, as a numbering case for ``package``.

    With more than one chapter, each is one the reader reads and the check certifies on its
    own: one refused would refuse the whole document, and Word would have nothing to judge.
    """
    counter = [0, 0, 0]
    first = Document(rng, 0, counter)
    styles = first.defaults() + (HEADINGS if fields else "")
    parts: list[tuple[str, str, str]] = []
    comments: list[str] = []
    fmt = rng.choice(NOTE_FORMATS)
    start = rng.randint(1, 4)
    final = (
        f'<w:sectPr><w:footnotePr><w:numFmt w:val="{fmt}"/><w:numStart w:val="{start}"/>'
        "</w:footnotePr></w:sectPr>"
    )
    abstracts = nums = body = ""
    for chapter in range(chapters):
        for _ in range(_TRIES):
            before = list(counter)
            part = Document(rng, chapter, counter, fields, stories)
            definitions, lists, numbers = part.numbering()
            own_styles = part.styles()
            own_body = part.body(numbers)
            own_notes = notes("footnote", *range(before[0] + 1, counter[0] + 1))
            alone = Case(
                "chapter",
                definitions + lists,
                own_body,
                styles + own_styles,
                own_notes,
                stories=(("c1", "comments", "".join(part.comments)),) if part.comments else (),
            )
            if chapters == 1 or _certified(alone._replace(final=final), fields):
                break
            counter[:] = before
        else:
            raise SystemExit(f"no chapter the reader reads in {_TRIES} tries")
        abstracts, nums = abstracts + definitions, nums + lists
        styles += own_styles
        comments += part.comments
        if chapter and stories:
            # The paragraph between chapters closes the last one's section.
            closing = f"<w:sectPr>{_section(rng, first, parts)}</w:sectPr>"
            body += f"<w:p><w:pPr>{closing}</w:pPr></w:p>"
        elif chapter:
            # An empty paragraph between chapters, so no table runs on into the next.
            body += "<w:p/>"
        body += own_body
    if stories:
        final = final.replace("<w:sectPr>", f"<w:sectPr>{_section(rng, first, parts)}", 1)
        if comments:
            parts.append(("c1", "comments", "".join(comments)))
    footnotes = notes("footnote", *range(1, counter[0] + 1)) if counter[0] else ""
    return Case(
        "generated",
        abstracts + nums,
        body,
        styles,
        footnotes=footnotes,
        final=final,
        stories=tuple(parts),
    )


def documents(
    seed: int, count: int, chapters: int = 1, fields: bool = False, stories: bool = False
) -> list[bytes]:
    """``count`` documents of ``chapters`` chapters, the same ones for the same seed."""
    rng = random.Random(seed)
    return [package(generated(rng, chapters, fields, stories)) for _ in range(count)]


def main() -> int:
    """Write the documents into a folder."""
    parser = argparse.ArgumentParser(description="Generate .docx documents for Word to judge.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--documents", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--chapters", type=int, default=1, help="generated documents in each")
    parser.add_argument("--fields", action="store_true", help="captions and cross-references")
    parser.add_argument("--stories", action="store_true", help="headers, footers, comments")
    args = parser.parse_args()
    args.folder.mkdir(parents=True, exist_ok=True)
    made = documents(args.seed, args.documents, args.chapters, args.fields, args.stories)
    for index, data in enumerate(made):
        (args.folder / f"generated-{args.seed}-{index:03d}.docx").write_bytes(data)
    sys.stdout.write(f"wrote {args.documents} documents to {args.folder}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
