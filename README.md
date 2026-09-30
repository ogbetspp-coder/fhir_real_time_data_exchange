# label-docx-reader

A deterministic, fail-closed reader for the text of a label's Word (.docx) body. It gives the
exact text the document holds, or it refuses the document and says why. It never guesses,
normalises, trims or repairs.

- **True to the source.** Characters are copied as stored. Tabs, breaks, non-breaking and soft
  hyphens become their characters; Symbol-font glyphs (the "≥" and "°" Word stores as
  `<w:sym w:font="Symbol" w:char="F0B3"/>`) become their Unicode characters through a closed
  table; a picture is U+FFFC where it stands. Formatting that changes meaning (superscript,
  subscript, underline, strike, caps, highlight, shading, hidden paragraph marks, faint text...)
  is reported as ranges over the text, never folded into it: `10` with a superscript `9` is
  `"109"` plus a superscript mark on the `9`.
- **List labels as Word draws them.** "4.8", "b)", "•" are computed from the numbering part by
  Word's rules, and reported beside the text (never inserted into it). The rules are held to
  Microsoft Word's own answers for every corpus document (`corpus/*/word.json`).
- **Nothing passed over.** Every run in the main document part is read exactly once, run content
  stands only inside runs, and there is no character data outside `<w:t>` and `<w:instrText>`.
  A document where any of that fails is refused (`stray-text`, `unread-content`).
- **Refuses rather than guesses.** Tracked changes, hidden text, fields Word recomputes (PAGE,
  DATE, IF...), text boxes, footnote references, embedded objects, math, dingbat fonts, unmapped
  symbols, conditional table formatting and more are refused with a code. The full list is the
  module docstring of [`src/label_docx/reader.py`](src/label_docx/reader.py).
- **Deterministic.** Canonical JSON output (RFC 8785 form for these values); byte-identical
  across processes, hash seeds and locales, and however the .docx is zipped.
- **No runtime dependencies.** The standard library only (`zipfile`, `xml.etree`).

## Use

```bash
uv run label-docx label.docx              # canonical JSON on standard output
uv run label-docx label.docx -o out.json  # or to a file
```

Exit status: `0` read, `2` refused, `1` the file could not be opened.

```python
from label_docx import read_docx, DocxRefusedError

# Each Paragraph has text, style, numbering, table, marks and mark_hidden.
try:
    paragraphs = read_docx(data)
except DocxRefusedError as refused:
    refused.code, refused.detail
```

### Output

```json
{"format":"label-docx-json/1.1.0","paragraphs":[{"markHidden":false,"marks":[{"end":5,"kind":"superscript","start":4}],"numbering":{"level":1,"numId":2,"suffix":"tab","text":"4.8"},"style":"Heading2","table":null,"text":"x 109/l"}],"reader":"docx-reader/1.3.0","source":{"bytes":18342,"sha256":"…"}}
```

or, refused, `"refusal":{"code":"tracked-change","detail":"ins"}` in place of `paragraphs`.

- `marks[].start`/`end` count **Unicode code points** of `text` (not UTF-16 units, not bytes).
- `table` is `[table, row, cell]` counted from zero in document order; a nested table's
  paragraphs carry the outermost cell.
- `numbering` is the list the paragraph is in (`numId`, `level`) and the label Word draws before
  it: `text` ("4.8", "b)", "•", or "" for a level that shows nothing) and `suffix` (`tab`,
  `space`, `nothing`, or `legacy` for a Word 6 level, where the gap is layout, not a character).
  `text` and `suffix` are null when `numId` is 0 (not in a list). A consumer that wants the line
  as Word shows it puts `numbering.text` before the paragraph's `text`.
- `source.sha256` ties the result to the exact input bytes; `reader` and `format` tie it to the
  exact code (see "Versions").

## List labels

Word does not store "4.8" in the paragraph; it computes it from `numbering.xml` each time it
draws the page. The reader computes it by Word's rules (the module docstring, "List labels"), and
every rule is Word's own answer, not a reading of the specification:

- Lists that name the same `abstractNum` share one count, so a second list continues the first
  unless it has a `startOverride`, which applies the first time that list reaches the level.
- A level counted before its higher level counts that level as started: a first item at level
  2 is `1.1.1`.
- A level with no `w:start` starts at 0. A full level override (`w:lvl` in `lvlOverride`) changes
  how the number looks, never where it starts.
- A list linked through a numbering style takes that style's levels but keeps its own count.

### Word as the reference

`scripts/numbering_cases.py` writes `corpus/numbering-cases/`, one small .docx per rule (and per
question the rules had to answer). `scripts/word_oracle.py record <set>` opens each document in
Microsoft Word (macOS), reads the label Word draws for every list item (`listString`), and writes
`word.json`; `tests/test_word_oracle.py` then holds the reader to those answers in CI, without
Word. Recorded with Microsoft Word 16.113.3 for macOS: the reader agrees on 27 of 28 cases and on
the four EMA files, and refuses the 28th on purpose (a numbering-style link with no link back,
where Word draws an empty label).

To check a label of your own against Word without it entering the repository:

```bash
uv run --frozen python scripts/word_oracle.py compare path/to/label.docx
```

It prints, per file, `agrees`, `differs at list item n` or the reader's refusal with Word's
labels, and never the document's text.

### What is refused

- `unsupported-numbering`: a format that depends on the language (ordinal, cardinal text) or is
  custom, a picture bullet, a Wingdings bullet (no closed mapping yet), a missing definition, a
  numbering-style link without its link back.
- `ambiguous-numbering`: a hidden label, or a numbered paragraph run on after a hidden mark
  (Word reports a label but not whether or where it is drawn); a label showing a level that only
  a level override defines.

## What it does not read

- **Headers, footers, footnotes, endnotes, comments.** Separate parts, not read. A footnote
  *reference* in the body is refused, so no footnote is lost silently.
- **Documents with tracked changes** are refused, not resolved. Accept or reject all changes in
  Word first; which text is "the label" is a decision the reader will not make.
- Some marks are conservative: a toggle property (caps, strike) set at any level of the style
  hierarchy is reported even where Word's toggle rules would cancel it. It can over-report a
  mark; it never under-reports one.

## How the claims are held

| Claim                                                            | Where                        |
| ---------------------------------------------------------------- | ---------------------------- |
| Each rule, read exactly or refused, in isolation                 | `tests/test_reader.py`       |
| No text passed over: the 13 cases 1.1.0 lost silently            | `tests/test_reader.py`       |
| The EMA QRD files keep their ≥, °, Symbol braces and pictures    | `tests/test_reader.py`       |
| List labels counted and drawn as Word does, or refused           | `tests/test_reader.py`       |
| Every corpus list label is the one Microsoft Word draws          | `tests/test_word_oracle.py`  |
| The numbering cases are what their script writes, byte for byte  | `tests/test_corpus.py`       |
| The EMA template's 7 Symbol bullets and 9 Word 6 dashes          | `tests/test_reader.py`       |
| Same bytes across processes, hash seeds and locales              | `tests/test_determinism.py`  |
| Same result however the parts are zipped                         | `tests/test_determinism.py`  |
| The output is canonical (RFC 8785 form); the CLI's exit codes    | `tests/test_output.py`       |
| Every corpus document reads to its locked digest                 | `tests/test_locks.py`        |
| A change to the reader or the format changes its version         | `tests/test_locks.py`        |
| The corpus files are the recorded byte copies                    | `tests/test_corpus.py`       |
| The interpreter's Unicode database is the pinned one (16.0.0)    | `tests/test_environment.py`  |

## Set-up and checks

Python 3.14 exactly (`>=3.14,<3.15`): the Unicode database ships with the interpreter, and the
reader's notion of "blank" depends on it. `uv` lives outside the environment it manages:

```bash
python3.14 -m venv .uv-bootstrap
.uv-bootstrap/bin/pip install "uv==0.12.17"
.uv-bootstrap/bin/uv sync --frozen

.uv-bootstrap/bin/uv run --frozen ruff check .
.uv-bootstrap/bin/uv run --frozen ruff format --check .
.uv-bootstrap/bin/uv run --frozen mypy --strict
.uv-bootstrap/bin/uv run --frozen python scripts/lock.py --check
.uv-bootstrap/bin/uv run --frozen pytest --cov
```

## Versions

Every result names `reader` (`docx-reader/x.y.z`) and `format` (`label-docx-json/x.y.z`).
`versions.lock.json` holds the SHA-256 of the file behind each version, and
`corpus/*/expected.json` the digest each corpus document reads to. Changing
`src/label_docx/reader.py` or `src/label_docx/output.py` fails the tests until the version is
bumped and `scripts/lock.py` run; a change in what any corpus document reads to shows up as a
diff in `expected.json` to review. A locked version is never re-locked to other code.

## Corpus

`corpus/<set>/` holds real documents with a `sources.json` recording where each came from and
its hash. Only public or synthetic documents go here: the four EMA QRD templates
(`corpus/ema-qrd/`) are EMA's published files, reproduced with acknowledgement as the EMA
permits. To add a set: the files, a `sources.json` in the same shape, then `scripts/lock.py`.

## Origin

Extracted from the EMA Flow repository at commit `d2d2d1f` (`zone-a/src/zone_a/docx/reader.py`,
`docx-reader/1.1.0`, SHA-256 `5e84e792…6f1a`, and its tests `zone-a/tests/test_docx_reader.py`).
1.2.0 adds the `stray-text` and `unread-content` refusals and changes nothing else: it reads the
four EMA files to the same paragraphs, marks and structure as 1.1.0. 1.3.0 draws list labels
(`label-docx-json/1.1.0` adds `numbering.text` and `numbering.suffix`) and accepts the font hint
`default`, which sends ambiguous characters to the `hAnsi` font. 1.4.0 replaces the reader's
assumptions about counting with Word's answers: it reads the cases 1.3.0 refused as ambiguous,
and draws the one label 1.3.0 got wrong (a level counted after a deeper one) as Word does.
