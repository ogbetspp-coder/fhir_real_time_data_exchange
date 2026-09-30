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
{"format":"label-docx-json/1.0.0","paragraphs":[{"markHidden":false,"marks":[{"end":5,"kind":"superscript","start":4}],"numbering":null,"style":"Normal","table":null,"text":"x 109/l"}],"reader":"docx-reader/1.2.0","source":{"bytes":18342,"sha256":"…"}}
```

or, refused, `"refusal":{"code":"tracked-change","detail":"ins"}` in place of `paragraphs`.

- `marks[].start`/`end` count **Unicode code points** of `text` (not UTF-16 units, not bytes).
- `table` is `[table, row, cell]` counted from zero in document order; a nested table's
  paragraphs carry the outermost cell.
- `numbering` is `{numId, level}`: the list the paragraph is in. The number Word draws is not in
  `text` (see "Not yet").
- `source.sha256` ties the result to the exact input bytes; `reader` and `format` tie it to the
  exact code (see "Versions").

## What it does not read (yet)

- **List numbers.** Word computes "4.8" or "•" from `numbering.xml` when it draws the page; it is
  not stored in the paragraph. The reader reports which list and level a paragraph is in, not
  the rendered number. SmPC section numbers are often typed, but where they are automatic they
  are not in `text`. Rendering them exactly is the next piece of work.
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
four EMA files to the same paragraphs, marks and structure as 1.1.0.
