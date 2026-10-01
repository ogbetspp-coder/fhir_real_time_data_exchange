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
- **Footnotes and endnotes**, with the marks Word draws ("1", "iv", "*") beside the text, and
  each note's own paragraphs read by the same rules.
- **Caption numbers and chapter references** (`SEQ`, `STYLEREF`: "Table 3-1"), computed as
  Word prints them and read only where the stored result is that number. Word shows a stale
  caption on screen and prints a different one, so such a document is refused (`stale-field`).
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
from label_docx import read_document, DocxRefusedError

# Each Paragraph has text, style, numbering, table, marks, mark_hidden and notes.
try:
    document = read_document(data)  # .body, .footnotes, .endnotes; read_docx(data) is the body

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
- `notes` lists the paragraph's footnote and endnote marks: `offset` (where the mark stands in
  `text`, in code points), `kind`, `id`, and `mark` (what Word draws; null for a custom mark,
  whose characters are in `text`). The top-level `footnotes` and `endnotes` list the notes in the
  order the body refers to them, each with `id`, `mark` and `paragraphs`; a note's first
  paragraph carries its own mark at the start, as Word draws it.
- `source.sha256` ties the result to the exact input bytes; `reader` and `format` tie it to the
  exact code (see "Versions").

## The ingestion service

`label-docx-service` keeps documents and what the reader made of them in a write-once,
content-addressed store, and answers over HTTP:

```bash
uv run label-docx-service serve --store /path/to/store            # http://127.0.0.1:8080
curl --data-binary @label.docx http://127.0.0.1:8080/v1/documents    # the receipt
curl http://127.0.0.1:8080/v1/documents/<sha256>                     # the result
uv run label-docx-service ingest --store /path/to/store *.docx     # without HTTP
uv run label-docx-service verify --store /path/to/store            # audit every result
```

| Request                              | Answer                                                  |
| ------------------------------------ | ------------------------------------------------------- |
| `POST /v1/documents` (the .docx)     | the receipt: 201 the first time, 200 after, same bytes  |
| `GET /v1/documents/<sha256>`         | the reader's result: read, or refused with the reason   |
| `GET /v1/documents/<sha256>/source`  | the document's bytes as ingested                        |
| `GET /v1/health`                     | the reader, format, Python and Unicode versions         |

What it guarantees:

- **The same bytes, the same answer.** A document is named by the SHA-256 of its bytes; the
  receipt and the result for it are the same bytes whoever sends it, however often, on any
  machine running the pinned runtime. The service will not start on another Python or Unicode
  version.
- **Nothing kept is changed.** The source, result and receipt are each written once (a new file
  or nothing); a result is served only if it hashes to what its receipt records; and `verify`
  reads every kept source again and requires the kept result byte for byte, which catches any edit
  to the store, however consistent.
- **Every version is kept.** Results are kept per reader and format version: a new reader adds
  its results beside the old ones, so what a document was read as on any date can be shown.
- **A refusal is an answer.** A document the reader cannot read exactly is kept, with its refusal
  code and detail, and answered the same way every time. A PDF is refused by name: it holds glyphs
  placed on a page, not the text Word holds, and the reader will not guess at it.

EMA publishes approved SmPCs as PDF. Their exact text is in EMA's ePI (FHIR), which EMA Flow's
ePI reader reads; the authored Word SmPC and Module 3 sections are what this service reads.

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
Microsoft Word (macOS), has Word write every list label into the text ("convert numbers to
text"), and records what each list item gained: the label and the tab or space after it. That is
`word.json`; `tests/test_word_oracle.py` holds the reader to it in CI, without Word.

Recorded with Microsoft Word 16.113.3 for macOS, over every corpus set: the reader agrees with
Word on every list label of every document it reads, and each document it refuses is listed in
the test with its reason (footnotes, EMA's stray private-use character in its Spanish template,
a numbering-style link with no link back).

## Checking your own documents

Neither command writes anything, or prints a document's text, so confidential labels can be
checked on one machine and never enter the repository:

```bash
uv run --frozen python scripts/survey.py path/to/folder            # read or refused, and why
uv run --frozen python scripts/word_oracle.py compare path/*.docx  # list labels against Word
```

### What is refused

- `unsupported-numbering`: a format that depends on the language (ordinal, cardinal text) or is
  custom, a picture bullet, a Wingdings bullet (no closed mapping yet), a missing definition, a
  numbering-style link without its link back.
- `ambiguous-numbering`: a hidden label, or a numbered paragraph run on after a hidden mark
  (Word reports a label but not whether or where it is drawn); a label showing a level that only
  a level override defines.

## Public documents checked

On 2026-09-30 the reader (1.5.0) and Microsoft Word 16.113.3 were run over 348 public Word
documents: EMA's product-information templates in 24 languages (336, from the
[QRD templates page](https://www.ema.europa.eu/en/human-regulatory-overview/marketing-authorisation/product-information-requirements/product-information-qrd-templates-human)),
the WHO prequalification QOS and QIS templates (10, Module 2.3 summaries in the Module 3
outline, from [WHO PQ](https://extranet.who.int/prequal/key-resources/documents/medicines/q)),
and SAHPRA's Module 2.3 QOS template (1). Approved SmPCs themselves are published as PDF (EMA's
product information; 7,507 of the 7,510 in the Czech regulator's open-data set), and Module 3
dossiers are not published at all.

| Outcome                                                              | Documents |
| -------------------------------------------------------------------- | --------: |
| Read, every list label the one Word draws                            |       329 |
| Refused: fields Word computes (EMA Annex IV mail merge, WHO `SEQ`)   |        11 |
| Refused: an equation (SAHPRA)                                        |         1 |
| Refused: EMA's stray U+F02D (SmPC template: es, fr, ro, sv)          |         4 |
| Refused: an unaccepted tracked insertion (EMA Appendix I, is)        |         1 |
| Refused: a .doc under a .docx name (EMA Annex IV, lv)                |         1 |
| Refused: a list item run on after a hidden mark (EMA ATMP, no)       |         1 |

No document was read with a list label or a note mark other than Word's. (Before footnotes were
read, 1.5.0 refused nine of these documents for them; 1.6.0 reads five, and the other four stop
at a `SEQ` field or an equation.) Five EMA files could not be
downloaded (HTTP errors after rate limiting); Health Canada and the TGA refuse scripted
downloads.

## What it does not read

- **Headers, footers, comments.** Separate parts, not read.
- **Fields Word computes from the layout or the clock** (`PAGE`, `DATE`, tables of contents,
  whose page numbers depend on layout) and **equations** are refused, as are `SEQ` captions with
  no stored result (three WHO Module 2.3 templates: Word shows nothing there and prints a
  number).
- **Word 97-2003 documents (.doc)** are refused, including under a .docx name: EMA's site
  serves one. Save them as .docx in Word first.
- **Documents with tracked changes** are refused, not resolved. Accept or reject all changes in
  Word first; which text is "the label" is a decision the reader will not make.
- Some marks are conservative: a toggle property (caps, strike) set at any level of the style
  hierarchy is reported even where Word's toggle rules would cancel it. It can over-report a
  mark; it never under-reports one.

## How the claims are held

`docs/requirements.md` lists the requirements (R-01 to R-17) with the tests that prove each; the
table below is the short form.

| Claim                                                            | Where                        |
| ---------------------------------------------------------------- | ---------------------------- |
| Each rule, read exactly or refused, in isolation                 | `tests/test_reader.py`       |
| No text passed over: the 13 cases 1.1.0 lost silently            | `tests/test_reader.py`       |
| The EMA QRD files keep their ≥, °, Symbol braces and pictures    | `tests/test_reader.py`       |
| List labels counted and drawn as Word does, or refused           | `tests/test_reader.py`       |
| Every corpus list label and note mark is the one Word draws      | `tests/test_word_oracle.py`  |
| Fields are read only where Word prints what it shows            | `tests/test_word_oracle.py`  |
| The numbering cases are what their script writes, byte for byte  | `tests/test_corpus.py`       |
| The EMA template's 7 Symbol bullets and 9 Word 6 dashes          | `tests/test_reader.py`       |
| A .doc, or a zip that is not the whole file, is never read        | `tests/test_reader.py`       |
| Every change to what Word shows is noticed; nothing else is      | `tests/test_mutations.py`    |
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

`corpus/<set>/` holds documents with a `sources.json` recording where each came from and its
hash, the reader's locked digests (`expected.json`) and Word's list labels (`word.json`). Only
public or synthetic documents go here:

- `ema-qrd/`: the four EMA QRD files the reader's rules were first written from.
- `ema-templates/`: the other English EMA product-information templates (ATMP, MRP/DCP
  referral, PSUSA, Annex IV, appendix cover pages, Appendix V), and five kept for what they
  hold: a VML picture (Estonian), smart-tag properties and a stray private-use character
  (Spanish), and Cyrillic and Greek text (Bulgarian, Greek ATMP).
- `numbering-cases/`: synthetic, written by `scripts/numbering_cases.py`.

EMA's files are reproduced with acknowledgement as the EMA permits. The WHO and SAHPRA Module 2.3
templates were checked (above) but are not reproduced, their terms of reuse being unclear. To
add a set: the files, a `sources.json` in the same shape, then `scripts/lock.py` and
`scripts/word_oracle.py record`.

## Origin

Extracted from the EMA Flow repository at commit `d2d2d1f` (`zone-a/src/zone_a/docx/reader.py`,
`docx-reader/1.1.0`, SHA-256 `5e84e792…6f1a`, and its tests `zone-a/tests/test_docx_reader.py`).
1.2.0 adds the `stray-text` and `unread-content` refusals and changes nothing else: it reads the
four EMA files to the same paragraphs, marks and structure as 1.1.0. 1.3.0 draws list labels
(`label-docx-json/1.1.0` adds `numbering.text` and `numbering.suffix`) and accepts the font hint
`default`, which sends ambiguous characters to the `hAnsi` font. 1.4.0 replaces the reader's
assumptions about counting with Word's answers: it reads the cases 1.3.0 refused as ambiguous,
and draws the one label 1.3.0 got wrong (a level counted after a deeper one) as Word does.
1.5.0 reads what public regulator templates hold and 1.4.0 refused without cause (VML pictures,
smart-tag and custom-XML properties, conditional table formatting that cannot change the text),
and refuses a .doc under a .docx name, which 1.4.0 opened as the zip of its theme. 1.6.0 reads
footnotes and endnotes (`label-docx-json/1.2.0`), their marks numbered by the rules Word showed:
the section's settings, not the document's, and start plus the notes before. 1.7.0 reads SEQ and
STYLEREF fields whose stored result is what Word prints, by rules Word answered in 11 cases.
1.8.0 names a PDF in its refusal, for the ingestion service.
