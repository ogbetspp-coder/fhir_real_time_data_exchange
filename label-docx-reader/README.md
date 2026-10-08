# label-docx-reader

Reads a medicinal product label (a Word `.docx`: SmPC, leaflet, Module 3; or an EMA ePI, FHIR
JSON) to its exact text as canonical JSON, or refuses it with a reason. It never guesses,
normalises or repairs.

## Guarantees

- **Exact text.** Characters as stored. Tabs, breaks and special hyphens become their
  characters; Symbol-font glyphs ("≥", "°", "μ") and Wingdings bullets ("▪") map through closed
  tables. A picture in line with the text is U+FFFC, as is a shape in alternate content that
  holds no text (any other shape is refused); a floating one is not in the text, as in Word, but
  placed where it is anchored, and the certificate counts it. A floating text box, group or
  canvas is placed too, its text set aside unread, where nothing in it is counted or referred to
  elsewhere (a field, a note, a list item, a bookmark, a comment, a tracked change); else it is
  refused. Each U+FFFC says what it stands for: the image part, its SHA-256,
  type, pixels, extent and crop, and the first reason found that its bytes may not be the
  picture Word draws. Formatting that changes meaning (bold, italic, super/subscript,
  underline, strike, caps, highlight, shading, faint text) is reported as marks over the text,
  never folded in.
- **What Word shows.** List labels ("4.8", "b)", "•"), footnote marks and computed fields (SEQ,
  STYLEREF, REF, NOTEREF) follow Word's rules, each Word's own answer to a test document
  (`corpus/*/word.json`). A DOCVARIABLE (Veeva Vault's anchor at a heading) is read where its
  stored result is its variable's value, so Word shows it alike before and after an update.
  Body, notes, headers, footers and comments are read.
- **Tracked changes: two texts, never one.** A tracked document is read with every change
  accepted and with every change rejected, plus the list of changes. There is no default text;
  the caller chooses.
- **Refuses rather than guesses.** Hidden text, fields Word recomputes (DATE, IF...), text boxes
  in line or holding what is counted elsewhere, equations, embedded objects, dingbat fonts and more are refused with a code. The full lists:
  the docstrings of [`reader.py`](src/label_docx/reader.py) and [`epi.py`](src/label_docx/epi.py).
- **Certified.** An independent check ([`certify.py`](src/label_docx/certify.py)) re-reads the
  source with its own walk and rules (for a .docx through the same standard-library XML parser,
  each text element read again by its own tokenizer) and accounts for every character of every
  result; a result it cannot account for is refused (`uncertified`). See
  [docs/conservation.md](docs/conservation.md).
- **Deterministic.** Same input, same bytes: tested across fresh processes, hash seeds and
  locales, and with the same parts zipped differently (`test_determinism.py`).
- **No runtime dependencies.** Python 3.14 standard library only.

## Use

```bash
uv run --frozen label-docx label.docx       # JSON on stdout
uv run --frozen label-docx-service serve --store DIR          # http://127.0.0.1:8080, demo page at /
uv run --frozen label-docx-service ingest --store DIR FILE... # the same, without HTTP
uv run --frozen label-docx-service verify --store DIR         # re-read every kept document, compare
```

`label-docx` and `ingest` exit 0 when read, 1 on an error (a file that cannot be opened), 2 when
refused, and 3 when read in part (an ePI with sections, or a .docx with headers, footers or
comments, the reader refused on their own; the receipt's outcome `read-in-part`). `ingest` exits
2 if any file was refused, else 3 if any was read in part.

```python
from label_docx.documents import kind  # chooses the reader from the bytes, as the CLI does

result, was_read = kind(data).read(data)  # canonical JSON bytes, and whether it was read
```

`label_docx.read` reads a .docx only: it refuses an ePI as `invalid-package`. An ePI alone:
`label_docx.epi_output.read`.

| Request                                   | Answer                                                    |
| ----------------------------------------- | --------------------------------------------------------- |
| `POST /v1/documents` (.docx or ePI)       | the receipt: 201 the first time, 200 after, same bytes    |
| `GET /v1/documents/<sha256>`              | the result, its `Outcome` header the receipt's; 409 if Word or Chrome shows it otherwise, or (`require`) has not checked it |
| `GET /v1/documents/<sha256>/source`       | the bytes as ingested                                     |
| `GET /v1/documents/<sha256>/verification` | Word's or Chrome's verdicts                               |
| `GET /v1/health`                          | reader, format, Python and Unicode versions               |

An error answers `{"code": …, "error": …}`: `code` is one of `label_docx.service.ERRORS` (for
example `no-such-document`, `too-large`, `shown-otherwise`), for a program to act on, and `error`
says it in words.

`--browser` (ePI, default `auto`) and `--word` (.docx, macOS with Word, default `off`) hold each
ingested document to the application: `auto` where installed, `on` always, `require` always and
serve nothing it has not checked, `off` never. A verdict is kept once per application version
and verifier version (the code that asks and judges); `require` counts only the current
verifier's agreement. A result the application shows otherwise, or that two answers at once
judge differently, is never served.

The store is write-once and content-addressed: a document is named by its SHA-256, each reader
version's result is kept beside the earlier ones, and nothing kept is rewritten. Bound to a
loopback address, the service answers only loopback host names and takes no document posted
from another web page.

## Output

```json
{"certificate": {…}, "comments": [], "endnotes": [], "footers": [], "footnotes": [],
 "format": "label-docx-json/1.20.0", "headers": [],
 "paragraphs": [{"anchored": [{"kind": "text-box", "offset": 2, "read": false}],
   "comments": [], "markHidden": false,
   "marks": [{"end": 5, "kind": "superscript", "start": 4}], "notes": [], "numbering": null,
   "pages": [], "pictures": [{"crop": null, "extent": [76200, 76200], "kind": "picture",
     "offset": 8, "part": "word/media/image1.png", "pixels": [8, 8], "reason": null,
     "sha256": "…", "type": "png"}],
   "style": "Heading2", "table": [0, 1, 0], "text": "x 109/l \ufffc"}],
 "reader": "docx-reader/1.34.0", "refusedParts": 0, "source": {"bytes": 1083, "sha256": "…"},
 "tables": [{"grid": {"columns": 2, "rows": [
   {"after": 0, "before": 0, "cells": [{"column": 0, "merge": null, "span": 2}],
    "exactHeight": false},
   {"after": 1, "before": 0, "cells": [{"column": 0, "merge": null, "span": 1}],
    "exactHeight": false}]},
   "parent": null, "reason": null}]}
```

- Offsets (`marks`, `notes`, `pages`, `comments`) count Unicode code points of `text`.
- `numbering` is the list label Word draws before the paragraph (`text`, `suffix`); it is not
  part of `text`. `suffix` is `tab`, `space` or `nothing`, or `legacy` after a Word 6 label where
  Word writes a tab but its drawing of the gap is not on record. Only for a Word 6 label does
  `tab` also say that Word draws at least a space there; after any other label it says only
  what Word writes. Its `level` counts from 0 in a .docx (Word's `ilvl`) and from 1 in an ePI (the
  lists around the item). `table` is `[table, row, cell]`, counted from 0, `cell` among the
  row's cells.
- `tables` lists each body table, in document order, a nested table as its own entry with its
  `parent` cell, and its `grid`: `columns` (`gridCol`), and each row's `before` and `after`
  (grid columns left out) and `cells`, each with its first grid `column`, its `span`
  (`gridSpan`) and its `merge` (`vMerge` as stored: null, `restart` or `continue`), and whether
  the row's height may be exact (`exactHeight`: its own `trHeight` of rule `exact`, or one its
  table's style sets), where Word clips what does not fit. Where Word's grid is not on record, `grid` is null and `reason` says why: `no-grid`, `two-grids`,
  `bad-number`, `h-merge` (a legacy horizontal merge), `bad-merge`, `bad-span` or `row-off-grid`
  (a row that does not fill the grid exactly). The text is read either way. A body paragraph's
  `table` indexes `tables` (its own table, nested or not). Tables in notes, headers, footers and
  comments are not listed; there `table` is the outermost table's cell.
- `pictures` says, for each U+FFFC of a paragraph's `text` (body, notes, headers, footers,
  comments), in order, what it stands for: its `offset`, `kind` (`picture` or `shape`), the image
  `part` its relationship names and the `sha256` of its bytes, its `type` by signature alone
  (`png` or `jpeg`), its `pixels` from the image's header, its `extent` in EMU (`wp:extent`) and
  its `crop` (`a:srcRect`, thousandths of a percent). The bytes are not in the result: read the
  part from the source and hold it to `sha256`. `reason` is the first found of `shape`, `vml`,
  `field` (in a field's result, which Word prints again), `linked`, `no-part`,
  `not-png-or-jpeg`, `bad-image-header`, `colour` (colour a browser manages), `animated`,
  `orientation` (Exif turns it), `bad-number`, `cropped`, `rotated`, `flipped`, `line-height`
  and `row-height` (Word clips it to an exact line or row), `border` (on its run) and `effects`
  (anything else on a closed list: recolouring, transparency, SVG, an outline, a shape other than
  a rectangle, a shadow, an effect extent under 0...; what Word draws nothing of, an effect
  extent's space, a fill turning with an unturned shape, a hidden shadow, an unfilled line's join
  and ends, is not, as Word's drawing of `corpus/drawing-cases` has it). Null means only that nothing on those closed lists was found: the
  lists rest on what is known of Word, not on Word's drawing, and the ePI builder and the browser
  hold the rest. Whether Word draws it larger than its pixels is the caller's to judge (9525 EMU
  a pixel at 96 dpi). No picture refuses a read; the lists: "Pictures" in
  [`reader.py`](src/label_docx/reader.py).
- `anchored` places each object anchored to the paragraph, which Word draws apart from the
  text (body, notes, headers, footers, comments), in order: its `offset` (where its anchor
  stands in `text`), its `kind` (`picture` or `shape`, holding no text; `text-box`, or `shapes`
  for a group or canvas, holding text) and `read`, whether its own text is read: never yet,
  so its text is in no paragraph, and the certificate counts it (`setAside`: `unreadObjects`,
  `unreadObjectCharacters`, every branch of alternate content). One is set aside only where
  nothing in it is counted or referred to elsewhere (a field, a footnote or endnote mark, a list
  item, a bookmark, a comment, a section, a tracked change) and it stands in no field; any
  other is refused as before. The lists: "Anchored" in [`reader.py`](src/label_docx/reader.py).
- A refusal has `"refusal": {"code": …, "detail": …}` in place of the text.
- A tracked document has `"tracked": {"accepted": {…}, "original": {…}, "changes": […]}` in place
  of the text; each view has the shape above.
- Word draws caps marks by its own rule: "5 µg" in capitals is "5 µG", never "5 ΜG".

Every key: the docstrings of [`output.py`](src/label_docx/output.py) and
[`epi_output.py`](src/label_docx/epi_output.py).

## How it is checked

| Claim                                                         | Held by                                    |
| ------------------------------------------------------------- | ------------------------------------------ |
| Each rule reads exactly or refuses                            | `test_reader.py`, `test_epi.py`, `test_tracked.py` |
| Labels, notes, fields, text, headers, footers, bold, italic, caps and strike are what Word shows | Word's recorded answers (`test_word_oracle.py`) |
| A picture carried as its pixels, a theme's shading resolved, a pattern's theme colour and a tab's leader are as Word draws them | Word's recorded drawing (`test_word_drawn.py`) |
| Tracked views are Word's Accept All / Reject All (42 of 50 cases; 8 refused) | Word's own files (`test_tracked.py`) |
| ePI sections are what Chrome shows                            | Chrome's recorded answers (`test_browser_oracle.py`) |
| Every result read is certified; seeded changes to each corpus result read are caught | `test_certify.py` |
| Each fault put into the checker is caught by its tests, or recorded as unable to change a result | the mutation record (`test_checker_mutants.py`): 4,253 of 4,361 faults killed, 108 recorded as unable to change a result, none unexplained |
| Up to two seeded edits of each kind in `scripts/mutate.py` to the `document.xml` of each corpus .docx not refused: one to what the reader reports changes the result or is refused; others (font size, bookkeeping) change nothing | `test_mutations.py` |
| Same bytes across processes, hash seeds, locales and zip layouts; seeded damage to four corpus files never crashes it | `test_determinism.py`, `test_robustness.py` |

Word is asked about emphasis paragraph by paragraph, for the body: whether all of a paragraph's
letters are bold, italic, in capitals or struck through, its white space's formatting aside.
Word's "no" agrees with a paragraph partly so, and a paragraph with a note reference, a page
number or a hidden paragraph mark is not held to it. Superscript, subscript, underline, highlight,
shading, faint, raised or lowered and right-to-left text are not asked of Word: unit tests hold
them, and the conservation check's own copy of Word's rules holds superscript, subscript and
underline (R-35). Word's text shows every Symbol character (`w:sym`) as "(": there the reader's
character is held only to be one of the Symbol table's, as many as the body has (R-38); which
one, the conservation check holds to the table.

Every requirement and its tests: [docs/requirements.md](docs/requirements.md). To check
confidential documents without writing or printing their text:

```bash
uv run --frozen python scripts/survey.py FOLDER              # read or refused, and why
uv run --frozen python scripts/word_oracle.py compare *.docx  # against Word (macOS)
```

## Corpus

Public or synthetic documents only, each set with its `sources.json`; `expected.json` locks what
each document reads to, `word.json` and `browser.json` hold Word's and Chrome's answers, and
`word-gaps.json` and `word-drawn.json` what Word drew (`scripts/word_gaps.py`,
`scripts/word_drawn.py record`, macOS with Word).

| Set               | Documents | What                                                              |
| ----------------- | --------: | ----------------------------------------------------------------- |
| `ema-qrd`         |         4 | EMA QRD files the rules were first written from                   |
| `ema-templates`   |        18 | EMA product-information templates                                 |
| `numbering-cases` |       155 | one Word rule each, synthetic                                     |
| `drawing-cases`   |        32 | what Word draws for pictures, theme shading and tab leaders, synthetic |
| `fda-templates`   |         3 | FDA prescribing information, medication guide and patient insert templates |
| `word-authored`   |         2 | written by Word itself (a table of contents)                      |
| `tracked-cases`   |        39 | tracked changes, with Word's Accept All and Reject All files      |
| `ema-epi`         |       108 | every ePI the EMA API listed on 2026-10-01: 3,355 sections, 3,258 read, 97 refused |

## Development

Python 3.14 exactly (its Unicode database decides what is whitespace). The five checks, as CI
runs them:

```bash
python3.14 -m venv .uv-bootstrap && .uv-bootstrap/bin/pip install "uv==0.12.17"
.uv-bootstrap/bin/uv sync --locked
.uv-bootstrap/bin/uv run --frozen ruff check . && .uv-bootstrap/bin/uv run --frozen ruff format --check .
.uv-bootstrap/bin/uv run --frozen mypy --strict
.uv-bootstrap/bin/uv run --frozen python scripts/lock.py --check
.uv-bootstrap/bin/uv run --frozen pytest --cov -n auto   # on every core
```

Rules for changing the code: [AGENTS.md](AGENTS.md). Extracted from EMA Flow
(`zone-a/src/zone_a/docx/reader.py` at docx-reader/1.1.0, since replaced by this reader).
