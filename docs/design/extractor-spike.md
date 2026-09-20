# Design note: extractor spike

- Status: In progress
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

_Pending._
