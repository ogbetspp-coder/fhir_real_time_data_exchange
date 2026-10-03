# label-docx-reader

Reads a medicinal product label (a Word `.docx`: SmPC, leaflet, Module 3; or an EMA ePI, FHIR
JSON) to its exact text as canonical JSON, or refuses it with a reason. It never guesses,
normalises or repairs.

## Guarantees

- **Exact text.** Characters as stored. Tabs, breaks and special hyphens become their
  characters; Symbol-font glyphs ("≥", "°", "μ") and Wingdings bullets ("▪") map through closed
  tables. A picture or shape in line with the text is U+FFFC; a floating one is not in the text,
  as in Word, and the certificate counts it. Formatting that changes meaning (bold, italic,
  super/subscript, underline, strike, caps, highlight, shading, faint text) is reported as marks
  over the text, never folded in.
- **What Word shows.** List labels ("4.8", "b)", "•"), footnote marks and computed fields (SEQ,
  STYLEREF, REF, NOTEREF) follow Word's rules, each Word's own answer to a test document
  (`corpus/*/word.json`). Body, notes, headers, footers and comments are read.
- **Tracked changes: two texts, never one.** A tracked document is read with every change
  accepted and with every change rejected, plus the list of changes. There is no default text;
  the caller chooses.
- **Refuses rather than guesses.** Hidden text, fields Word recomputes (DATE, IF...), text boxes,
  equations, embedded objects, dingbat fonts and more are refused with a code. The full lists:
  the docstrings of [`reader.py`](src/label_docx/reader.py) and [`epi.py`](src/label_docx/epi.py).
- **Certified.** An independent check ([`certify.py`](src/label_docx/certify.py)) re-reads the
  source with its own parser and accounts for every character of every result; a result it
  cannot account for is refused (`uncertified`). See [docs/conservation.md](docs/conservation.md).
- **Deterministic.** Same input, same bytes, on any machine, process, hash seed or locale.
- **No runtime dependencies.** Python 3.14 standard library only.

## Use

```bash
uv run --frozen label-docx label.docx       # JSON on stdout; exit 0 read, 2 refused, 1 unreadable
uv run --frozen label-docx-service serve --store DIR          # http://127.0.0.1:8080, demo page at /
uv run --frozen label-docx-service ingest --store DIR FILE... # the same, without HTTP
uv run --frozen label-docx-service verify --store DIR         # re-read every kept document, compare
```

```python
from label_docx import read  # canonical JSON bytes, and whether it was read

result, was_read = read(data)
```

| Request                                   | Answer                                                    |
| ----------------------------------------- | --------------------------------------------------------- |
| `POST /v1/documents` (.docx or ePI)       | the receipt: 201 the first time, 200 after, same bytes    |
| `GET /v1/documents/<sha256>`              | the result; 409 if Word or Chrome shows it otherwise, or (`require`) has not checked it |
| `GET /v1/documents/<sha256>/source`       | the bytes as ingested                                     |
| `GET /v1/documents/<sha256>/verification` | Word's or Chrome's verdicts                               |
| `GET /v1/health`                          | reader, format, Python and Unicode versions               |

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
 "format": "label-docx-json/1.13.0", "headers": [],
 "paragraphs": [{"comments": [], "markHidden": false,
   "marks": [{"end": 5, "kind": "superscript", "start": 4}], "notes": [], "numbering": null,
   "pages": [], "style": "Heading2", "table": null, "text": "x 109/l"}],
 "reader": "docx-reader/1.21.0", "refusedParts": 0, "source": {"bytes": 1083, "sha256": "…"}}
```

- Offsets (`marks`, `notes`, `pages`, `comments`) count Unicode code points of `text`.
- `numbering` is the list label Word draws before the paragraph (`text`, `suffix`); it is not
  part of `text`. `table` is `[table, row, cell]`, counted from 0.
- A refusal has `"refusal": {"code": …, "detail": …}` in place of the text.
- A tracked document has `"tracked": {"accepted": {…}, "original": {…}, "changes": […]}` in place
  of the text; each view has the shape above.
- Word draws caps marks by its own rule: "5 µg" in capitals is "5 µG", never "5 ΜG".

Every field: the docstrings of [`output.py`](src/label_docx/output.py) and
[`epi_output.py`](src/label_docx/epi_output.py).

## How it is checked

| Claim                                                         | Held by                                    |
| ------------------------------------------------------------- | ------------------------------------------ |
| Each rule reads exactly or refuses                            | `test_reader.py`, `test_epi.py`, `test_tracked.py` |
| Labels, notes, fields, text, emphasis are what Word shows     | Word's recorded answers (`test_word_oracle.py`) |
| Tracked views are Word's Accept All / Reject All              | Word's own files (`test_tracked.py`)       |
| ePI sections are what Chrome shows                            | Chrome's recorded answers (`test_browser_oracle.py`) |
| Every result is certified; every changed result is caught     | `test_certify.py`                          |
| Every fault put into the checker is caught by its tests       | the mutation record (`test_checker_mutants.py`) |
| Every edit to a document changes the result or is refused     | `test_mutations.py`                        |
| Same bytes everywhere; damaged files never crash it           | `test_determinism.py`, `test_robustness.py` |

Every requirement and its tests: [docs/requirements.md](docs/requirements.md). To check
confidential documents without writing or printing their text:

```bash
uv run --frozen python scripts/survey.py FOLDER              # read or refused, and why
uv run --frozen python scripts/word_oracle.py compare *.docx  # against Word (macOS)
```

## Corpus

Public or synthetic documents only, each set with its `sources.json`; `expected.json` locks what
each document reads to, `word.json` and `browser.json` hold Word's and Chrome's answers.

| Set               | Documents | What                                                              |
| ----------------- | --------: | ----------------------------------------------------------------- |
| `ema-qrd`         |         4 | EMA QRD files the rules were first written from                   |
| `ema-templates`   |        18 | EMA product-information templates                                 |
| `numbering-cases` |        89 | one Word rule each, synthetic                                     |
| `fda-templates`   |         3 | FDA prescribing information, medication guide and patient insert templates |
| `word-authored`   |         2 | written by Word itself (a table of contents)                      |
| `tracked-cases`   |        39 | tracked changes, with Word's Accept All and Reject All files      |
| `ema-epi`         |       108 | every ePI the EMA API listed on 2026-10-01: 3,355 sections, 3,258 read, 97 refused |

## Development

Python 3.14 exactly (its Unicode database decides what is whitespace). The five checks, as CI
runs them:

```bash
python3.14 -m venv .uv-bootstrap && .uv-bootstrap/bin/pip install "uv==0.12.17"
.uv-bootstrap/bin/uv sync --frozen
.uv-bootstrap/bin/uv run --frozen ruff check . && .uv-bootstrap/bin/uv run --frozen ruff format --check .
.uv-bootstrap/bin/uv run --frozen mypy --strict
.uv-bootstrap/bin/uv run --frozen python scripts/lock.py --check
.uv-bootstrap/bin/uv run --frozen pytest --cov
```

Rules for changing the code: [AGENTS.md](AGENTS.md). Extracted from EMA Flow
(`zone-a/src/zone_a/docx/reader.py`, docx-reader/1.1.0).
