# Design note: extractor spike

- Status: Complete — verdict below (no-go for Document AI as the character source; go as the
  structure classifier; the parser is a hybrid)
- Date: 2026-09-20
- Related: `docs/fidelity-normalization.md` (section 7, the extractor contract),
  `docs/adr/0003-mechanical-narrative-fidelity.md`, `docs/roadmap.md` item 0

## The question

The Zone A design assumes an extractor can turn a label PDF into `SourceDocumentText` — pages
with body ranges that exclude running headers and footers, discretionary hyphens emitted as
U+00AD, tables as row-major TAB/LF — such that the fidelity check (`fidelity-norm/1.1.1`)
passes for narrative that a human would accept as the same text. That contract was written
from first principles. No extractor has met it. Everything in roadmap items 2–6 is built on
it, so it is tested first, cheaply, with a written verdict at the end.

The spike is not a feature. Its code is disposable once the verdict exists; what survives is
the verdict, any amendment to the extractor contract or normalisation specification, and the
recorded extractor output that becomes a regression fixture for the real parser.

## Two parts, one verdict

### Part A — controlled round trip

Generate a synthetic SmPC PDF from the narrative already in
`src/fixtures/synthetic-submission.ts`, laid out the way real ones are: a running header with
the product name, a footer with page numbers, numbered QRD headings, justified paragraphs, at
least one table, at least one word hyphenated across a line end, typographic quotes, a
ligature glyph (U+FB01), and one page set in two columns the way a package leaflet is.

Run it through Document AI, adapt the output to `SourceDocumentText`, locate each fixture
section's text in the extracted pages as spans, and run `verifyNarrativeFidelity`. Every
section verifies, or the report says exactly which rule it failed and why.

This proves the plumbing on a document whose content is known. It cannot prove robustness on
documents nobody controlled — that is Part B.

### Part B — real-world characterisation

Run a real, publicly published EMA SmPC PDF through the same adapter and report **counts
only**: pages, blocks by type, whether headers and footers were classified, line-end hyphens
and how many are ambiguous, ligature glyphs, characters the specification rejects, tables and
their cell structure, and the character-level agreement between Document AI's text and the
PDF's own embedded text layer.

Part B is the informative half. It is also the half that touches real product information,
so it is bounded: the document is fetched by hand into the git-ignored `.cache/` directory and
never committed (no script downloads it, so no URL to a real product is ever written down in
the repository either); no text from it is written to any output, log, or fixture; only
integers and hashes leave the script, and `assertTextFree` refuses to emit a report containing
anything that is not a token, a hash, or a timestamp. This is consistent with the intent of the
synthetic-only rule in `AGENTS.md`, but it is a judgment call and is run only with explicit
approval.

### The measurement that decides most

**Character fidelity of the extractor.** The fidelity check compares text; an extractor that
"corrects" characters — resolving ligatures, straightening quotes, dropping soft hyphens,
normalising whitespace — silently breaks it even when every layout decision is right. For a
born-digital PDF the truth is the embedded text layer. The spike extracts that layer
independently (pdf.js) and diffs it against Document AI's text per page, reporting the number
of differing code points and their classes (never the characters).

If Document AI is not character-faithful, it is disqualified as _the_ extractor but not as a
component: the likely shape of the real parser is then a pinned, deterministic text-layer
extractor for the characters plus Document AI for structure (which blocks are headers, which
are tables). The spike must be able to tell those two outcomes apart.

## Adapter contract

`adapt(documentAiOutput) → SourceDocumentText`, with these rules, each of which the report
must account for:

| Contract requirement (spec §7)                    | Adapter rule                                                                                                                                |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `bodyStart`/`bodyEnd` exclude headers and footers | Blocks Document AI types as header/footer are placed outside the body range; the body must still satisfy the line-boundary rules of spec §1 |
| Body ends with its own line terminator            | Adapter appends U+000A after the last body block                                                                                            |
| Tables row-major, cells TAB, rows LF              | Table blocks are serialised from Document AI's row/cell structure, never from positional text                                               |
| Discretionary hyphens as U+00AD                   | See "the hard problem" below; the adapter's rule is explicit, and every application of it is counted                                        |
| No normalisation beyond faithful extraction       | The adapter changes no character it received; the character-fidelity diff proves this                                                       |
| Extractor identity pinned                         | `extractorVersion` is the Document AI processor version string plus the adapter's own version                                               |

### The hard problem: hyphens at line ends

A born-digital PDF contains a hyphen _glyph_ at a hyphenated line end. Nothing in the text
says whether it is a discretionary hyphen (`intra-` / `venous` → `intravenous`) or a real one
(`long-` / `term` → `long-term`). The specification deliberately leaves this to the extractor
(section 4) because guessing changes words.

The spike does **not** solve this. It measures it: how many line-end hyphens occur per
document, how many are resolvable by a conservative rule (the joined form appears elsewhere in
the same document unhyphenated), and how many are genuinely ambiguous. That number decides
whether a deterministic rule is enough, whether the reviewer UI must surface each ambiguous
case for a human, or whether the specification needs a new mechanism. It is the single most
likely source of a _conditional_ verdict.

## Verdict rubric

- **Go.** Part A verifies every section. Part B shows header/footer classification is
  available, Document AI's text agrees with the embedded text layer (differences explained by
  a closed list of classes), and line-end hyphen ambiguity is low enough for a conservative
  rule plus human review.
- **Conditional.** Part A verifies, but Part B shows a systematic gap — an unclassified header,
  a ligature class, a hyphenation rate — that needs an amendment to spec section 3, 4, or 7.
  The verdict names the amendment; it becomes `fidelity-norm/1.2.0` before item 3 starts.
- **No-go for Document AI as extractor.** Its text is not character-faithful, or it cannot
  classify headers and footers on real documents. The verdict then names the alternative: a
  pinned deterministic text-layer extractor for characters, with Document AI (or nothing) for
  structure.

## Deliverables

| Path                                                                                                               | What                                                                                               | Tier |
| ------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------- | ---- |
| `scripts/spikes/document-ai/generate-pdf.ts`                                                                       | Synthetic SmPC PDF generator (Part A input); deterministic for a given fixture                     | O    |
| `scripts/spikes/document-ai/extract.ts`                                                                            | Document AI client call; writes the raw response to a local, git-ignored path                      | O    |
| `scripts/spikes/document-ai/adapt.ts`                                                                              | The adapter above, pure, with every rule application counted                                       | O    |
| `scripts/spikes/document-ai/text-layer.ts`                                                                         | pdf.js embedded-text extraction for the character-fidelity diff                                    | O    |
| `scripts/spikes/document-ai/report.ts`                                                                             | Runs Part A verification and Part B characterisation; emits counts, hashes, and rule outcomes only | O    |
| `test/spikes/document-ai-adapter.test.ts`                                                                          | Adapter + fidelity on a recorded Document AI response for the synthetic PDF; no network            | O    |
| `infra/` — Document AI API, Layout Parser processor (multi-region `eu`), `roles/documentai.apiUser` for the worker | Managed like everything else                                                                       | S    |
| `docs/design/extractor-spike.md` — verdict section, appended                                                       | The written go / conditional / no-go with the numbers behind it                                    | F    |

Design, the verdict, and any specification amendment are Fable's. The adapter is reviewed
adversarially before the verdict is written, with the reviewer instructed to attack the "no
character changed" claim and the "no narrative in any output" claim.

## Constraints that apply here as everywhere

- No narrative from any document appears in a report, a log line, a test assertion message,
  or a committed fixture other than the synthetic fixture already in the repository. Reports
  carry counts, hashes, offsets, and rule names.
- The recorded Document AI response committed as a test fixture is for the **synthetic** PDF
  only.
- Dependencies are pinned exactly, as `zod` is.
- The spike does not modify `src/fidelity/`. If it finds the specification wanting, that is
  the verdict, and the amendment follows change control (spec section 8).

## Human steps

1. `gcloud auth application-default login` on the machine running `extract.ts` (client
   libraries need Application Default Credentials; the `gcloud` CLI login is not enough).
2. Approve Part B's use of a public EMA document at run time, or decline it — the verdict is
   weaker without it but Part A still stands.

## Verdict

Run 2026-09-20 against processor `projects/sage-ship-509104-b8/locations/eu/processors/a171773c69e36baf`,
default version `pretrained-layout-parser-v1.0-2024-06-03`, adapter 1.0.0,
`fidelity-norm/1.1.1`. Every number below is from the reports the run wrote; no text from any
document was read to write this section.

### Part A — the plumbing works

The synthetic SmPC PDF (3 pages, 24 top-level blocks, 83 flattened) went through Document AI,
the adapter, span location, and the real verifier: **32 of 32 sections located and verified,
status `passed`, zero issues**, `reportHash`
`12449bee74d936f9950b27378c2aaa9d3cd04789cae73bd9473878dd8c3a6a46`. Headers and footers were
classified on all three pages; the drawn table came back as one table block with 4 rows and 8
cells and serialised row-major; the forced line-end hyphen was kept as a glyph and counted.
The extractor contract (section 7) and the verifier are satisfiable by a real extractor's
output. That question is closed.

### The decisive measurement — Document AI is not character-faithful

Pages 2 and 3 of the synthetic document diffed against the PDF's own text layer with **zero**
differing code points and ordered agreement 1.0. Page 1, which carries the probes, diffed with
exactly 11, and their Unicode categories decode without ambiguity: `Pi: 2` and `Pf: 2` only in
the text layer with `Po: 4` only in the extractor is **all four typographic quotes straightened
to ASCII**; `Ll: 3` with the extractor one code point longer than the text layer is **U+FB01
expanded to `fi`**.

The two real-world documents confirm that this is systematic, not a probe artefact:

| Document (public, non-product)               | Pages | Differing code points | Multiset agreement | Ordered agreement | Dominant categories                                   |
| -------------------------------------------- | ----- | --------------------- | ------------------ | ----------------- | ----------------------------------------------------- |
| EudraLex Vol. 2C SmPC guideline              | 29    | 438 of 95,735         | 0.9977             | 0.9903            | Po 169, Pf 89, Pi 48, Ll 44, Zs 32, Pd 16, Sm 7, So 6 |
| EMA QRD annotated template, pages 1–28 of 39 | 28    | 287 of 58,632         | 0.9976             | 0.9955            | Po 113, Pf 58, Pi 35, Zs 24, Pd 20, Lu 18, Ll 10      |

Read by category: `Pi`/`Pf` removed against `Po` added is quote straightening on every page
that has quotes; `Pd` in matched pairs is dash substitution (a typographic dash becoming
hyphen-minus); `Zs` is space-variant normalisation (a non-breaking or thin space becoming
U+0020); `No: 2` against `Nd: 2` in the guideline is a **superscript digit flattened to a plain
digit**; `Ll` is ligature expansion and, on two pages, something more that a category diff
cannot resolve; `Lu: 18` on the template is letter-level and unexplained. Only 2 of the 29
guideline pages diffed clean.

Ligature expansion is harmless: section 3 step 2 applies it to both sides. Everything else in
that list is content under section 4 — and a superscript flattened in `m²` or a typographic
quote straightened in a product name is a change to the approved text. The failure mode is
worse than a fidelity mismatch: in Zone A the narrative is _derived from_ the extracted text,
so both sides of the check carry the same corruption, the check passes, and the published
label silently differs from the approved PDF. The character-fidelity diff is the only
measurement that can see this, which is why it is the one the design was built around.

**Document AI Layout Parser is disqualified as the source of characters.**

### What Document AI is good for

- **Header and footer classification works.** 29 of 29 guideline pages had their footer
  classified; the synthetic document had all three headers and footers classified. (The template
  slice classified one header and no footers — either the document has none or they are styled
  past the classifier; the page/body difference of 22 code points over 28 pages says almost
  nothing was excluded, so this needs a probe with known footers before it counts either way.)
- **Heading hierarchy is exposed** (`heading-1` through `heading-4`) and body blocks nest under
  headings — 553 of 593 guideline blocks were nested, which is the structure a deterministic
  QRD segmenter (roadmap item 3) wants.
- **Table structure is exposed** as rows and cells (template slice: 2 tables, 7 rows, 17 cells,
  no spans, no internal line breaks).
- **Reading order is right on body pages** (ordered agreement tracks multiset agreement to within
  a percent) and wrong on a cover page (guideline page 1: multiset 0.99, ordered 0.70), so it is
  not a substitute for the text layer's draw order on non-linear layouts.

### The hard problem, measured

Line-end hyphens across 57 real pages: **0** in the guideline, **1** in the template slice. EU
regulatory documents produced from Word are not hyphenated in practice. The ambiguity the
specification deliberately left to the extractor is, on this corpus, nearly absent; a
conservative rule — treat a line-end hyphen as a real hyphen, emit no U+00AD, surface each
occurrence for the reviewer — costs nothing here. Two documents is not a corpus; the count is
now cheap to take on any further document and should be, before item 3 relies on it.

### Two constraints for the real parser

- **Online processing has a page limit.** 28 and 29 pages were accepted; the 39-page template
  was rejected with `INVALID_ARGUMENT`. Full product information (SmPC, Annex II, labelling,
  leaflet) runs to 60–120 pages, so the parser must use Document AI's batch (asynchronous,
  Cloud Storage in and out) path, or process per-section slices.
- **The processor version is not in the response.** Every run reported
  `unknown-processor-version`; the value that pins the extractor identity
  (`pretrained-layout-parser-v1.0-2024-06-03`) came from `processors.get`. The parser must read
  it there and record it, or the `extractorVersion` field is a fiction.

### Verdict

**No-go for Document AI Layout Parser as the extractor of characters. Go for Document AI as
the structure classifier. The parser is a hybrid**, which the rubric's third branch anticipated
and the spike could distinguish, as it was required to:

1. Characters come from the PDF's embedded text layer through a pinned, deterministic library
   (pdf.js in Node, as the spike's `text-layer.ts` already does; PyMuPDF or pdfminer in Zone A,
   proven against the same differential harness), character-exact by construction for
   born-digital PDFs.
2. Structure — which spans are headers and footers, which are headings and at what level,
   which are table cells — comes from Document AI's blocks, aligned onto the text layer by
   position and used to set `bodyStart`/`bodyEnd`, to drive the segmenter, and to serialise
   tables. Document AI's own text is never emitted.
3. The extractor contract (section 7) is amended to say so; see the change made alongside this
   verdict.

Scanned (non-born-digital) labels are outside this verdict entirely: the text-layer method
presumes there is a text layer. They need OCR, and OCR needs a different fidelity story.

### What survives the spike

The recorded Document AI response for the synthetic PDF is committed as a regression fixture,
and a test asserts the verdict's evidence — 32/32 verified, and the exact page-1 category
diff that shows the straightening — so the finding is executable, not just written down. The
rest of the spike code is disposable once item 3 exists.
