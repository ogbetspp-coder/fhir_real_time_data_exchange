# label-docx-reader

A deterministic, fail-closed reader for the text of a label's Word (.docx) body. It gives the
exact text the document holds, or it refuses the document and says why. It never guesses,
normalises, trims or repairs.

- **True to the source.** Characters are copied as stored. Tabs, breaks, non-breaking and soft
  hyphens become their characters; Symbol-font glyphs (the "≥" and "°" Word stores as
  `<w:sym w:font="Symbol" w:char="F0B3"/>`) become their Unicode characters through a closed
  table; a picture is U+FFFC where it stands. Formatting that changes meaning (bold, italic,
  superscript, subscript, underline, strike, caps, highlight, shading, hidden paragraph marks,
  faint text...) is reported as ranges over the text, never folded into it: `10` with a
  superscript `9` is `"109"` plus a superscript mark on the `9`. Bold, italic, capitals and
  strike-through follow Word's own rules, where two styles that both set one cancel out.
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
- **Tracked changes read as two texts, never one.** A document with tracked changes is read
  with every change accepted and with every change rejected (the original), each certified,
  with the list of changes (who, when, what kind). The result has no text of its own: the caller
  names the view it takes, so a proposal never becomes "the label" unnoticed.
- **Refuses rather than guesses.** Hidden text, fields Word recomputes (PAGE,
  DATE, IF...), text boxes, footnote references, embedded objects, math, dingbat fonts, unmapped
  symbols, conditional table formatting and more are refused with a code. The full list is the
  module docstring of [`src/label_docx/reader.py`](src/label_docx/reader.py).
- **Deterministic.** Canonical JSON output (RFC 8785 form for these values); byte-identical
  across processes, hash seeds and locales, and however the .docx is zipped.
- **Proved for every document.** An independent check (`label_docx.certify`) reads the source
  again with its own parser and certifies each read: every output character is the source's,
  in order, and every source character is in the output or set aside for a stated reason
  (field code, page numbers...). A read it cannot account for is refused (`uncertified`), never
  served. The certificate is in the result and the receipt; see
  [docs/conservation.md](docs/conservation.md).
- **No runtime dependencies.** The standard library only (`zipfile`, `xml.etree`).

It also reads **EMA electronic product information (ePI)**, the FHIR document Bundles the EMA
publishes as JSON: the text a browser shows for each section, or that section refused with the
reason (see [EMA ePI](#ema-epi) below).

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

or, refused, `"refusal":{"code":"hidden-text","detail":"…"}` in place of `paragraphs`. A document
with tracked changes has `"tracked":{"accepted":{…},"original":{…},"changes":[…]}` in place of
`paragraphs` (each view holds `paragraphs`, notes, headers, footers and comments, as above).

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
- `pages` lists where Word draws a page number (a table of contents, a `PAGE` field). Word sets
  it from the page layout when it prints, so it is placed, never read.
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
| `POST /v1/documents` (.docx or ePI)  | the receipt: 201 the first time, 200 after, same bytes  |
| `GET /v1/documents/<sha256>`         | the reader's result: read, or refused with the reason; 409 if Chrome shows an ePI otherwise |
| `GET /v1/documents/<sha256>/source`  | the document's bytes as ingested                        |
| `GET /v1/documents/<sha256>/verification` | Chrome's verdicts on an ePI's result, per Chrome version |
| `GET /v1/health`                     | the readers, formats, Python and Unicode versions       |

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
- **Every read is certified.** The independent conservation check accounts for every character
  of every read (see [docs/conservation.md](docs/conservation.md)); its certificate is in the
  result and the receipt.
- **Every .docx can be held to Word.** With `--word on` (a Mac with Microsoft Word), the service
  has Word check each Word document it reads: list numbers, note marks, fields, that print
  shows what the screen shows, and bold and italic. The verdict is kept and answered like
  Chrome's, and a result Word shows otherwise is never served. Word takes about a minute a
  document, so this is off unless asked for.
- **Every ePI is held to a browser.** Where Chrome is installed (`--browser auto`, the default;
  `on` to require it, `off` to skip), the service has Chrome show each ePI it reads and compares
  every section, text and formatting, with the result. The verdict is kept beside the result,
  once per Chrome version; the response says `Verification: agrees`, `differs` or
  `not-verified`. A result Chrome shows otherwise is kept but never served.
- **A refusal is an answer.** A document the reader cannot read exactly is kept, with its refusal
  code and detail, and answered the same way every time. A PDF is refused by name: it holds glyphs
  placed on a page, not the text Word holds, and the reader will not guess at it.

EMA publishes approved SmPCs as PDF. Their exact text is in EMA's ePI (FHIR), which this service
reads too, alongside authored Word SmPC and Module 3 documents.

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

## EMA ePI

An ePI is a FHIR document Bundle: one Composition whose sections each hold an XHTML `div`, Word
exported to HTML with inline styles on nearly every element. `label_docx.epi` reads each
section's div to the text a browser shows, in the same paragraphs and marks as a .docx, or refuses
that section with a code, and the rest of the document is still read. The rules and refusals
are the module docstring of [`src/label_docx/epi.py`](src/label_docx/epi.py).

**A browser is the reference**, as Word is for a .docx. `scripts/browser_oracle.py` has headless
Chrome parse every section as HTML, lay it out and report the text it shows and what it computed
for each piece (weight, slant, colour, background, decorations, raised or lowered text). The
reader must agree character for character and mark for mark, or refuse. On 2026-10-01 every
document the EMA's public ePI API lists was checked: 108 Bundles (SmPC, package leaflet,
labelling, Annex II; English, Swedish, Danish, Spanish, Dutch), 3,347 sections.

| Outcome                                                              | Sections |
| -------------------------------------------------------------------- | -------: |
| Read, the text and marks the browser shows                           |    3,222 |
| Read otherwise than the browser shows                                |        0 |
| Refused (a Symbol font, malformed markup in EMA's data, capitals the viewer's language decides, layout that draws text over text...) | 125 |

`uv run --frozen python scripts/browser_oracle.py compare FILE.json` checks any ePI and prints
only verdicts. List numbers and bullets are drawn by the reader as the browser draws them, and
each is held to the marker Chrome draws, read from its accessibility tree.

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

- **The glossary** (Word's building blocks): not part of the document Word shows, not read. Its
  size is listed in each certificate (`notRead`). Headers, footers and comments are read (each
  with where it is used or anchored), or refused on their own with the reason, the body read
  all the same: a footer that prints today's date, say, or a watermark.
- **Page numbers** are placed, not read: Word sets them from the page layout when it prints. A
  table of contents is read as stored (Word prints it so until someone updates it), its page
  numbers placed.
- **Fields Word computes from the clock or a formula** (`DATE`, `IF`...) and **equations** are
  refused, as are `SEQ` captions with no stored result (three WHO Module 2.3 templates: Word
  shows nothing there and prints a number).
- **Word 97-2003 documents (.doc)** are refused, including under a .docx name: EMA's site
  serves one. Save them as .docx in Word first.
- **Documents with tracked changes** are read as both views, accepted and original, and never
  as one: which text is "the label" is a decision the reader will not make. Inserted and
  deleted text, moves, inserted and deleted paragraph marks, and changed run and paragraph
  formatting are undone; other tracked changes (table cells, section and table properties,
  numbering) are refused (`tracked-change`), as is a deleted paragraph mark before a table or
  at the end of a section.
- **Font size, colour, font and alignment** are not reported (faint text aside: white, tiny or
  squeezed text is marked).

## How the claims are held

`docs/requirements.md` lists the requirements (R-01 to R-39) with the tests that prove each; the
table below is the short form.

| Claim                                                            | Where                        |
| ---------------------------------------------------------------- | ---------------------------- |
| Each rule, read exactly or refused, in isolation                 | `tests/test_reader.py`       |
| No text passed over: the 13 cases 1.1.0 lost silently            | `tests/test_reader.py`       |
| The EMA QRD files keep their ≥, °, Symbol braces and pictures    | `tests/test_reader.py`       |
| List labels counted and drawn as Word does, or refused           | `tests/test_reader.py`       |
| Every corpus list label and note mark is the one Word draws      | `tests/test_word_oracle.py`  |
| Fields are read only where Word prints what it shows            | `tests/test_word_oracle.py`  |
| Every document read is one Word prints as it shows it            | `tests/test_word_oracle.py`  |
| Bold, italic, caps and strike are what Word shows, paragraph by paragraph | `tests/test_word_oracle.py` |
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
| Each ePI rule, read as a browser shows it or refused             | `tests/test_epi.py`          |
| Every ePI section read is what Chrome shows, text and marks      | `tests/test_browser_oracle.py` |
| Every read is certified character by character by an independent check | `tests/test_certify.py` |
| The check refuses all 9,192 changed results tried, of 16 kinds    | `tests/test_certify.py`      |
| Damaged files are refused or certified, never an error           | `tests/test_robustness.py`   |
| Every fault made in the check is caught by its tests, or cannot matter | `tests/test_checker_mutants.py` |
| Each ePI ingested is held to Chrome; a disagreement is never served | `tests/test_browser_verify.py` |

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

Every result names `reader` (`docx-reader/x.y.z`, or `epi-reader/x.y.z` for an ePI) and
`format` (`label-docx-json/x.y.z`, or `label-epi-json/x.y.z`).
`versions.lock.json` holds the SHA-256 of the file behind each version, and
`corpus/*/expected.json` the digest each corpus document reads to. Changing
`src/label_docx/reader.py`, `src/label_docx/output.py`, `src/label_docx/epi.py` or
`src/label_docx/epi_output.py` fails the tests until the version is
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
- `ema-epi/`: every ePI the EMA's public API listed on 2026-10-01 (108 Bundles), with Chrome's
  answers (`browser.json`).
- `word-authored/`: written by Microsoft Word itself through `scripts/word_authored.py` (a table of
  contents with page numbers, and the same gone stale), so they hold exactly what Word writes.

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
1.8.0 names a PDF in its refusal, for the ingestion service. 1.9.0 verifies cross-references
(REF, NOTEREF), which Word reprints. 1.10.0 reads tables of contents and places page numbers.
1.11.0 reports bold and italic, and every toggle by Word's rules (two styles cancel), held to
Word's answer for every corpus paragraph.
