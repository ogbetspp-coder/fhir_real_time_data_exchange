# Recorded change: `fidelity-norm/1.1.1` → `fidelity-norm/2.0.0`, 2026-09-23

_Follows the procedure (steps 0–8) in `docs/validation/README.md`, "Change control for shared,
evidenced libraries". The UR- rows it cites are in that file._

**What changed.** `NORMALIZATION_VERSION` moved from `fidelity-norm/1.1.1` to
`fidelity-norm/2.0.0` in `src/fidelity/normalize.ts` and `zone-a/src/zone_a/fidelity/normalize.py`,
and `docs/fidelity-normalization.md` was rewritten to 2.0.0 (sections 1, 2, 3, 4, 5, 6, 7 and 9;
section 8 unchanged). The design is `docs/design/fidelity-norm-2-0-0.md` (changes A–I) as
amended by its independent re-review (R1–R11, adopted exactly as worded and recorded at the end
of that note). In short: only `br` and `hr` may be self-closing, and must be (`void-element`);
digits and signs inside `sup` and `sub` fold to script code points, other numbers and U+00B1,
U+2213 there reject (`unmappable-script`), and `sup`/`sub` hold no element (`script-content`);
U+00AD before a line break anywhere in the emitted narrative text rejects, decided after the
scan; the section-edge rules read back through whitespace and across pages, pages are numbered
1..N, and a non-empty body ends with its own line feed even at the end of a page; section 2 adds
U+000B, U+000C, the C1 controls and the bidirectional controls on both sides, applies to the
whole `div` as decoded from JSON before the scan and to each decoded reference on its own, and
U+000B, U+000C and U+0085 leave the section 3 whitespace list; `class` and `id` are gone, `lang`
and `xml:lang` are root-only, `href` has only its `https://` form, `scope` is `th`-only; tables
contain only table parts and rows of one width (`table-content`, `table-shape`) and lose
`colspan`, `rowspan` and `pre`; the order in which violations are decided is stated in full;
and the extractor contract (section 7) gains the script, unverifiable-document, table-slot and
page-break rules. `XhtmlErrorCode` gains `void-element`, `script-content`, `unmappable-script`,
`table-content`, `table-shape` and `forbidden-character` (the scanner now raises it itself, for
the whole `div` and for decoded references).

**Second review, folded into 2.0.0.** A second independent review of the first 2.0.0
implementation found false passes before anything was released, and they were fixed in 2.0.0
itself (no version was bumped; nothing carried the first implementation's hashes outside this
branch):

- **C1** — whitespace inside a tag is U+0009, U+000A, U+000D and U+0020 only (was JavaScript's
  `\s`, which includes U+00A0, U+2000–U+200A, U+3000 and U+FEFF). An HTML parser reads those as
  part of the tag name, so `10<sup` U+00A0 `>6` rendered "106" and verified against "10⁶", and a
  `br`, `table` or `td` written that way vanished. Any other code point there is now
  `malformed-tag`.
- **C2** — a span edge must touch §3 whitespace or a body edge, whatever the code point beyond
  it (was: not a letter, digit or mark). "Maximum dose is 1" verified against "Maximum dose is
  1.5 mg", and a section could start at "20 °C." after "−", at "5 mg." after "0.", after "1," or
  inside "non-steroidal". R4's look-back is kept; only its word-character tests changed.
- **C3** — a bullet glyph becomes a space only at the start of a line and before whitespace
  (was: anywhere), and U+2219 and U+2043 are no longer bullets. `2∙10` verified against a table
  row reading 2 and 10.
- **L1** — inside `sup` the subscript digits and signs reject, inside `sub` the superscript ones
  (the first implementation kept a script digit of either table in both); U+2015, U+02D7,
  U+FE58 and U+2796 fold to minus and U+2795 to plus.
- **L2** — a span whose `page`, `startOffset` or `endOffset` is not an integer (a boolean, a
  fraction, `null`), and a page number that is a boolean, are structural errors in both
  implementations. Python reads `true` as page 1 unless it tests for `bool` first; TypeScript
  read it as no page. The divergence would have made the two reports differ.

Every probe of that review named "FP …" now ends non-verified in both implementations but one:
"FP empty cell shift (documented residual)", a table whose value moves from the Adults column
to the Children column behind an empty cell. That is cell association, which the review asked
to be stated as a residual rather than closed (below); it still verifies.

**Second review, round 2, folded into 2.0.0.** The review of the fixes above found two more
false passes and three smaller items, fixed in 2.0.0 as well:

- **Text line breaks** — a raw or referenced U+000A or U+000D in narrative text is emitted as
  U+0020, because a renderer draws it as a space. `<p>Take 2` U+000A `• 10 mg</p>` (and the
  same with `&#10;` or `◦`) verified against a page whose "• 10 mg" starts a new line, because
  the check read the bullet as a list item. `soft-hyphen-at-boundary` now concerns only U+00AD
  before a block boundary or `br`; U+00AD before a text line break is followed by a space,
  which section 3 step 1 does not join, so there is no false pass: `un` U+00AD U+000A `safe` in
  a paragraph reads "un safe" and mismatches a page's "unsafe"
  (`soft-hyphen-before-raw-line-feed-against-joined-word`), and verifies only against a page
  that also reads "un safe".
- **Grouped numbers at section edges** — a section edge is a cut when the code point on its
  inner side is a digit (Nd) and the first non-whitespace code point beyond it, on the same
  line, is a digit too; and U+00A0, U+2007 and U+202F are not edge whitespace. Against "The
  maximum dose is 10 000 IU daily." (with a space, U+202F or U+2009 between the groups) a span
  ending at "…is 10" verified, and so did one starting at "000 IU daily.".
- **Bullets in table cells** — a bullet glyph on a line that contains U+0009 is content, and
  the scanner writes a table cell, and everything inside it, on a U+0009-separated line (cells'
  start and end tags, and blocks and `br` inside a cell, emit U+0009 instead of U+000A), so a
  bullet in a cell is never a list item on either side. `<td>2</td><td>• 10</td>` verified
  against the row 2 U+0009 10. With the start of a text still counted as a line start, a bullet
  kept for its U+0009 would be replaced when the result was normalised again (the reviewer's
  own normalisation fuzz found 2,728 such inputs in 200,000), so the start of a text is no
  longer a line start: normalised text has no U+000A and cannot change again. The narrative's
  text always begins with U+000A (the root `div`), and the verifier reads each page slice it
  normalises — the first slice of a section and every gap it tests — from the U+000A that ends
  the previous line when only whitespace lies between (section 6), so a list item that starts
  a section is still a list item on both sides. This departs from the first round's wording
  "after text start or LF" on purpose; it also closes a case nobody had reported: a span
  starting at a mid-line bullet ("• 10 mg daily." of "Take 2 • 10 mg daily.") verified
  against "10 mg daily." because the slice's first bullet was at the start of its text.
- **Script letters and symbols** — inside `sub`, U+2071 and U+207F reject; inside `sup`,
  U+2090–U+209C reject; inside either, any code point of general category Sm, Ps, Pe or Pd that
  is neither a source nor a target of the fold tables rejects (U+FF1D, U+FE59, U+2E3A, U+FE31,
  and `~`, `<`, `[`, `]` too).
- **Rename** — the vector `span-ends-before-punctuation-passes` is now
  `span-ends-before-punctuation-is-word-cut`.

Every probe of round 2 named "FP2 …" now ends non-verified in both implementations; the
reviewer's probes of both rounds (44 and 121 cases), both XHTML fuzzers (60,000 narratives
each) and the normalisation fuzz (200,000 strings, now 0 non-idempotent) agree between them.

**Why.** ADR 0003's rule is that false failures are acceptable and false passes are not. The
design note's table lists ten narratives that verified under 1.1.1 while a reader saw something
the source does not say (a superscript turning `106` into `10⁶`, a soft hyphen joining words
across a visible line break, a section starting mid-word after a hyphenated page end, surrogates
completed across markup, text hidden by a class, content a renderer moves out of a table, C1
characters drawn through windows-1252, a `br` whose content an XML renderer drops); the
re-review added an eleventh (R1: page 1 ends "The maximum daily dose is 1" with no final line
feed, page 2 begins "0 mg.", and a section ending on page 1 verified). Every one of them is a
vector below and now fails.

**Impact assessment (step 0).** Importers of `src/fidelity/`:

- **Worker gate** (`src/fhir/transform.ts`, `src/pipeline.ts`, `src/contracts/canonical-submission.ts`):
  runs `verifyNarrativeFidelity` at 2.0.0 and refuses any submission whose
  `fidelity.normalizationVersion` or fidelity report names another version
  (`fidelity.normalizationVersion must be fidelity-norm/2.0.0`). A submission prepared under 1.1.1
  is refused, not re-evaluated; it must be prepared again. The only submission producer in the
  repository is the synthetic builder (`src/fixtures/synthetic-submission.ts`), which takes the
  version from the constant; Zone A produces no submissions yet — its port verifies at 2.0.0.
- **Query service** (`src/query/tools.ts`): reports `normalizationVersion` from the constant, so
  `get_section` and `verify_quote` now say `fidelity-norm/2.0.0` (confirmed: both read
  `NORMALIZATION_VERSION`, lines 509 and 739; nothing to change). It normalises stored
  narratives live with 2.0.0; a stored narrative that no longer scans surfaces as `unavailable`.
  `verify_quote` now returns `invalid-request` for a quote containing a C1 control, U+000B,
  U+000C or a bidirectional control (section 2 rejects the quote), where under 1.1.1 U+000B,
  U+000C and U+0085 were whitespace and the rest were compared as content; pinned in
  `test/query/acceptance.test.ts` and recorded in UR-22.
- **Contracts** (`src/contracts/`): no schema changed. `NormalizationVersion` is a pattern
  (`fidelity-norm/<semver>`), so `npm run contracts:generate` produced no drift and the agent's
  vendored `query-tools` schema is unchanged (`sync_contract.py --check` passes).
- **Synthetic fixtures** (`src/fixtures/synthetic-submission.ts` → `test/fixtures/contracts/`):
  the synthetic product's narratives are plain `<div><p>…</p></div>` and verify under 2.0.0
  with the same sections, statuses and coverage; `fidelity-report.json`,
  `canonical-submission.json`, `run-request.json` and `quote-edge-cases.json` moved only in the
  version string and the hashes that embed it. The second review's fixes moved none of them.
- **Zone A** (`zone-a/src/zone_a/fidelity/`): ported to the same rules in this change; see steps
  2 and 4.
- **Agent** (`agent/`): reads `normalizationVersion` as an opaque string; only test literals
  named 1.1.1 (`agent/tests/fake_query_service.py`, `agent/tests/test_postcheck.py`), now 2.0.0.
- **Spike scripts** (`scripts/spikes/document-ai/`): their mirrors of the section 2 list, the
  section 3 whitespace list and the bullet list follow 2.0.0 (the locator still replaces a
  bullet anywhere, which can lose a candidate span, never fabricate one: every candidate is
  re-checked with `normalizeText`). The recorded Document AI replay still verifies 32 of
  32 sections; its `reportHash` moved from the verdict's `12449bee…` to `be91473d…`, and
  replaying with the version string set back to 1.1.1 reproduces `12449bee…` exactly, so the
  move is the version string alone (`test/spikes/document-ai-verdict.test.ts`).
- **Previously produced evidence** remains reproducible only under 1.1.1: every fidelity report,
  binding hash and approved content hash recorded under 1.1.1 names that version, and this code
  refuses to re-verify it (a structural error, by design).

**Steps 1–6.** 1: `NORMALIZATION_VERSION` is `fidelity-norm/2.0.0` on both sides. 2:
`npm run contracts:generate` (no drift), `npm run vectors:generate`, `npm run contracts:fixtures`,
`npm run contracts:quote-edge` and `npm run differential:smoke` regenerated
`test/fixtures/fidelity/vectors.json` (137 → 402 vectors: normalisation 25 → 62, XHTML 60 → 214,
verify 52 → 126), the four contract fixtures and the smoke corpus; no `query-tools` or
`agent-turn` change, so no vendored copy moved; `zone-a/scripts/generate_models.py --check`
passes. 3: every changed vector, below. 4: the 265 new vectors — every row of the design note's
table, every amendment's boundary, and both the rejecting and the accepting side of each rule
(for example `<sup>2</sup>` → `²`, `<sup>a</sup>` kept, `<sup>–6</sup>` → `⁻⁶`, `<sup>±1</sup>`
rejects, a non-ASCII digit in `sup` rejects, whitespace-only text in table parts accepted, an
empty table accepted, R1's page without a final line feed, R4's blank-line and previous-page
starts, R6's escaped surrogate pair accepted as one code point — the last pinned by a test on
each side, because `JSON.stringify` never writes the escaped form into a vector; and for the
second review, `<sup` U+00A0 `>`, `</sup` U+00A0 `>`, `<br` U+00A0 `/>`, `<table` U+3000 `>` and
`<td` U+FEFF `>` → `malformed-tag` with ASCII tag whitespace accepted, span edges inside `1.5`,
`−20`, `0.5`, `1,000` and `non-steroidal` → `word-cut` with the whitespace-delimited forms
verified, bullets at a line start replaced and mid-line, after CR alone, at the end, or U+2219
and U+2043 kept, `2∙10` and `2 • 10` against a table row → mismatch, and the other script's
digits and signs in `sup`/`sub` → `unmappable-script`; and for its round 2, bullets after a raw
or referenced line feed in a paragraph → mismatch, line feeds and carriage returns in text
emitted as spaces, spans ending or starting inside `10 000` with each separator → `word-cut`
and the whole number and a number at a line end verified, bullets in the first and a later cell
against rows with and without the bullet, a bullet at a line start and at the very start of a
page, a span starting at a mid-line bullet, a section continuing over a bullet at a page head,
the other kind's script letters and the symbols, brackets and dashes in `sup`/`sub`);
seven structural cases (a missing page, pages out of order, numbering from 2, a boolean span
page, a boolean and a fractional span offset, a boolean page number) in
`test/fixtures/fidelity/cases.ts`; the `verify_quote` acceptance case above; and the extended
differential generator (below). 5: ADR 0003 amended (Decision item 1 and Consequences). 6: UR-09
(design control and evidence) and UR-22 (the `invalid-request` note) updated.

**Changed vectors (step 3).** Every existing verify vector's `reportHash` and input
`normalizationVersion` moved, because the report carries the version; for 49 of the 52 that is
the only change (every other member of `expected` compared equal with those two removed). The
vectors whose outcome changed against 1.1.1, each reviewed:

| Vector (family)                                                                                    | 1.1.1                | 2.0.0                                                   | Reason                                                                                                                                                                                                                                                                                                        |
| -------------------------------------------------------------------------------------------------- | -------------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `allows-tab-lf-cr-ff-vt` (normalisation)                                                           | `a b c d e f`        | `forbidden-character`                                   | R2: U+000B and U+000C reject on both sides. Name kept so the change is reviewable against 1.1.1; `allows-tab-lf-cr`, `rejects-vertical-tab` and `rejects-form-feed` state the new rule.                                                                                                                       |
| `inline-dropped` (XHTML)                                                                           | `…c 2`               | `…c ²`                                                  | B: a digit inside `sup` folds to its script code point.                                                                                                                                                                                                                                                       |
| `allowed-attributes` (XHTML)                                                                       | accepted             | `forbidden-attribute`                                   | F/R10: the root's `id` (then `class`, and the `#x` link) are no longer allowed; the first decides. `accepts-lang-on-root` is the accepting form.                                                                                                                                                              |
| `superscript-markup-over-plain-digit` (verify)                                                     | `passed`, `verified` | `failed`, `mismatch`                                    | B: the case is the defect — `m<sup>2</sup>` and `H<sub>2</sub>O` against a source's `m2`, `H2O`. The generator fills the provenance's `normalizedTextSha256` from the new text, so the input moved.                                                                                                           |
| `hidden-extra-element` (verify)                                                                    | `failed`, `mismatch` | `failed`, `malformed-narrative` (`forbidden-attribute`) | F: `class` is refused before any comparison. The narrative no longer normalises, so the binding hash and the filled `normalizedTextSha256` moved too.                                                                                                                                                         |
| `bullets` (normalisation)                                                                          | `one two three`      | `• one two ▪ three`                                     | C3: `▪` mid-line is content; round 2: the start of a text is not a line start, so the first `•` is content too (normalisation stays idempotent; the verifier reads page slices from their line terminator). Only `●`, after U+000A and before a space, is list structure. Name kept for review against 1.1.1. |
| `span-ends-before-punctuation-passes` (verify), renamed `span-ends-before-punctuation-is-word-cut` | `passed`, `verified` | `failed`, `invalid-provenance` (`word-cut`)             | C2: the span ends at "use" before ".", and a full stop is no longer a boundary — the same rule must refuse "1" of "1.5". A false failure the rule accepts. Renamed in round 2 so the name says the outcome.                                                                                                   |
| `table`, `accepts-caption-first`, `accepts-table-section-order` (XHTML)                            | cells between U+000A | cells between U+0009                                    | Round 2: a table cell and everything in it is emitted on a U+0009-separated line, so a bullet in a cell is never a list item. The normalised text is unchanged (both are whitespace).                                                                                                                         |

Against the first 2.0.0 implementation (commit `e00e708`), the second review changed, besides
`bullets` and `span-ends-before-punctuation-passes` above: `sup-dashes-fold-to-minus`,
`sub-dashes-fold-to-minus`, `sup-plus-variants-fold` and `sub-plus-variants-fold` (their inputs
gained U+2015, U+02D7, U+FE58, U+2795, U+2796, which now fold, L1); `sup-horizontal-bar-kept`
became `sup-horizontal-bar-folds` (U+2015 now folds to U+207B, L1); and
`sub-superscript-digit-kept` became `rejects-sub-superscript-digit` (`²` inside `sub` now
rejects, L1). Those two were renamed because they were never released.

Against the second-review commit (`fca8737`), round 2 changed:

- `bullets` — `one two ▪ three` → `• one two ▪ three`: the start of a text is not a line start.
- `bullet-at-text-start-replaced` → renamed `bullet-at-text-start-kept`, `▪ y` stays `▪ y`;
  `bullet-after-line-feed-at-text-start-replaced` (`\n▪ y` → `y`) pins the replacing side.
  `bullet-after-invisible-at-start-replaced` → renamed
  `bullet-after-invisible-at-line-start-replaced`, input now begins with U+000A; and
  `bullets-in-a-row-replaced` gained a leading U+000A for the same reason.
- `rejects-soft-hyphen-before-raw-lf`, `rejects-soft-hyphen-before-lf-reference`,
  `rejects-soft-hyphen-cr-then-br` and `rejects-raw-soft-hyphen-before-crlf` → renamed
  `soft-hyphen-before-raw-lf-is-a-space`, `soft-hyphen-before-lf-reference-is-a-space`,
  `soft-hyphen-cr-then-br-is-a-space` and `raw-soft-hyphen-before-crlf-is-a-space`; each now
  accepted, with U+00AD followed by U+0020 (text line breaks are spaces, so no join).
- `accepts-soft-hyphen-before-cr-alone` — U+000D is emitted as U+0020.
- `soft-hyphen-before-cr-and-br` and `soft-hyphen-before-raw-line-feed` (verify) —
  `malformed-narrative` (`soft-hyphen-at-boundary`) → `mismatch`: the narrative reads "non
  smokers", the source "nonsmokers".
- `sup-tilde-kept` → renamed `rejects-sup-tilde`: `~` is Sm, outside the fold tables.
- `table`, `accepts-caption-first`, `accepts-table-section-order`, `accepts-scope-on-th`,
  `accepts-nested-table-in-cell`, `accepts-header-and-data-cells`,
  `accepts-ascii-whitespace-in-tags` and `accepts-whitespace-in-table-parts` — cells are
  U+0009-separated (and, in the last, raw U+000A and U+000D in table parts are spaces).
- `span-ends-before-punctuation-passes` → renamed `span-ends-before-punctuation-is-word-cut`.

No verify vector's outcome changed from round 2 except the two soft-hyphen vectors above.

No vector used `colspan`, `rowspan`, `pre`, `lang` below the root, U+0085 as whitespace or a
non-self-closing `br`. Seven XHTML vectors keep their outcome, `forbidden-attribute`, but are now
decided by the attribute's name rather than its value grammar: `rejects-four-class-tokens`,
`rejects-class-with-text-after-newline`, `rejects-free-text-class` (`class` is gone),
`rejects-id-with-trailing-newline`, `rejects-non-token-id`, `rejects-duplicate-attribute` (`id`
is gone; `rejects-duplicate-root-lang` now pins the duplicate rule) and
`rejects-href-with-trailing-newline` (the `#` form is gone; `rejects-fragment-href` pins it).
`rejects-cell-outside-row` stays `misnested-tag` and `rejects-unknown-element` (`<img/>`) stays
`unknown-element`, as R3 pins. The implementation confirmed the re-review's simulation of R4:
no vector's outcome changed because of R4's start and end rules. C2 then changed one:
`span-ends-before-punctuation-passes`.

**Differential proof.** `scripts/fidelity/differential.ts` no longer draws U+0085, U+000B and
U+000C as whitespace (its whitespace list and the verify family's line-terminator class follow
section 3); they and the C1 and bidirectional controls are drawn as section 2 characters, and
the generator now produces every new construct (self-closing tags and `<br>` start tags, a start
tag that is both a void and a parent violation, `sup`/`sub` with ASCII digits and signs, dashes,
letters, script digits, references and numbers without a script form, table whitespace, content
and references in table parts, uneven and empty rows, surrogates split by markup and by
reference, section 2 characters raw, referenced, in a tag, in an attribute and outside the root,
U+00AD before whitespace and before each form of line break, pages beginning blank, pages ending
in a continued word, bodies without a final line feed before a footer and at the end of the
page, missing, reordered and renumbered pages, sections starting at a later page's first
sentence and ending at a page end or just after U+00AD and whitespace). For the second review it also produces tags
with whitespace that is only `\s` (U+00A0, U+1680, U+2000–U+200A, U+2028, U+2029, U+202F,
U+205F, U+3000, U+FEFF) in start tags, end tags, `br`, `table`, `td` and before and around
attributes, and tags with ASCII whitespace that is accepted; numbers with punctuation inside
them and span edges next to punctuation inside a token; bullets at line starts (including
U+2219 and U+2043) and mid-line; `sup`/`sub` text with the added fold forms and the other
script's digits and signs; and spans with boolean, fractional or `null` fields and a boolean page
number. For round 2: raw and referenced line feeds and carriage returns before a bullet in text,
numbers grouped with a space, U+2009, U+00A0, U+2007 or U+202F and joiners before a unit, span
edges inside them, bullets on U+0009 lines and at the start of table cells, and the other
kind's script letters and symbols, brackets and dashes in `sup`/`sub`. Run as CI runs it (`npx tsx scripts/fidelity/differential.ts --seed <s> --count 2000 >
<file>`, then `DIFFERENTIAL_CORPUS=<file> uv run --frozen pytest` in `zone-a/`) at seeds
20260920, 1 and 2: 2002 passed each, **zero divergences**, and the class-coverage test (now
requiring 49 more classes than under 1.1.1) passes on each. The reviewer's own probes and fuzz
also agree between the two implementations, 0 divergent (above).

Rules were then broken in the Python port one at a time and the three corpora re-run
(divergences at seeds 20260920 / 1 / 2). The second review's: tag whitespace back to `\s`
34 / 30 / 35; end edge back to word characters 25 / 39 / 29; start edge back to word characters
15 / 17 / 22; bullets replaced away from a line start 215 / 191 / 213; bullets replaced without
following whitespace 54 / 60 / 56; U+2219 back in the bullet list 13 / 12 / 10; the other
script's digits kept 7 / 11 / 10; U+2796 not folded 3 / 4 / 4; non-integer span fields not
refused 11 / 12 / 28; a boolean rendered as Python's `True` in an issue 19 / 14 / 12. The first
round's, re-run on the extended generator: body without its final line feed at a page end
44 / 45 / 52; U+000B and U+000C allowed 10 / 6 / 5; U+2066–U+2069 allowed 10 / 12 / 11; start
rule stopping at the page start 22 / 10 / 14; 1..N page check dropped 76 / 59 / 60; U+2013 not
folded in `sup` 0 / 3 / 2; numbers without a script form kept 12 / 8 / 9; whole-`div` section 2
check skipped 14 / 13 / 15; any element allowed to self-close 10 / 12 / 10; `void-element`
decided after the parent check 2 / 4 / 7; U+00AD before CR LF accepted 15 / 16 / 11;
`table-shape` skipped 24 / 12 / 12; a whitespace reference allowed in a table part 5 / 0 / 5;
`lang` allowed below the root 0 / 1 / 1; end rule not reading through trailing whitespace
17 / 14 / 14. Every break diverged on at least two of the three seeds and every review break on
all three; each was restored. (Under the first implementation's generator, before the second
review, the first round's sixteen breaks each diverged on all three seeds; the generator
changed, so those numbers are superseded.)

Round 2's rules were broken the same way on its extended generator (seeds 20260920 / 1 / 2): a
text line break kept as U+000A or U+000D 183 / 192 / 187; only raw line breaks, not
references, turned into spaces 48 / 43 / 40; the end digit-group rule dropped 1 / 1 / 1; the
start digit-group rule dropped 3 / 2 / 4; U+00A0, U+2007 and U+202F counted as edge whitespace
5 / 4 / 8; the U+0009-line bullet rule dropped 111 / 129 / 122; table cells emitted with U+000A
25 / 29 / 30; the start of a text counted as a line start again 11 / 2 / 6; page slices read
from the span start instead of the line terminator 9 / 13 / 12; symbols, brackets and dashes
kept in `sup`/`sub` 11 / 7 / 12; the other kind's script letters kept 1 / 3 / 2. Every one
diverged on all three seeds and was restored. The earlier rounds' breaks were not re-run on
this generator.

**Blast radius.**

- **Every submission carrying 1.1.1 is refused by the worker gate** from the moment this change
  deploys (the worker and the query service ship together, from `main`). Nothing in the
  repository produces a 1.1.1 submission after this change: the synthetic builder takes the
  constant, and Zone A does not produce submissions yet.
- **The persisted demonstration documents were approved under 1.1.1, and they are not re-seeded
  in this change.** The approval build's migration (`docs/design/approval.md`, phase 1 step 6)
  re-seeds and re-approves the demonstration store. Until then, the query service reports
  `fidelity-norm/2.0.0` for its live normalisation of those documents while their stored
  approvals name `fidelity-norm/1.1.1`: the version an answer carries is not the version the
  document was approved under, and an old approval does not carry forward to a new version
  (step 7). The four synthetic products' narratives are plain paragraphs and normalise to the
  same text under 2.0.0 (shown for the in-repository synthetic submission; the store itself was
  not read in this change, which had no cloud access).
- **Extractors.** Output that conformed to 1.1.1 may not conform to 2.0.0: plain digits in a
  raised or lowered run, uneven table rows or spanned cells not emitted as empty slots, a final
  line without its line feed at the end of a page, U+000B or U+000C as a page break. A real PDF
  whose text layer was decoded as Latin-1 (U+0092, U+0095, U+0096 and the rest of C1) now fails
  the page — intended.
- **Narratives.** A narrative that uses `class`, `id`, `lang` below the root, `#` links,
  `colspan`, `rowspan`, `pre`, a self-closing non-void element, `<br>` as a start tag, elements
  inside `sup`/`sub`, content directly inside table parts, rows of different widths, or C1,
  bidirectional, U+000B or U+000C characters is now `malformed-narrative`. Tables with spanned
  cells must be written as unspanned cells of one width (the extractor emits the covered slots
  empty); the engine (roadmap item 8) must produce them that way. A line feed in a
  paragraph's text is a space: a narrative that relied on one to start a list item needs a
  `br` or a block. A bullet in a table cell is content. Tag whitespace other than
  TAB, LF, CR and SPACE is `malformed-tag`. A bullet glyph mid-line, and U+2219 and U+2043
  anywhere, are now content, so a narrative and a source that differ only there mismatch.
- **Spans.** A section edge must touch whitespace or a body edge. A span that ends before
  punctuation ("… clinical use" before ".") or starts after it is now `word-cut`: a false
  failure the rule accepts, because the same rule refuses "1" of "1.5". Extractors and span
  producers must put section edges at whitespace, and not between the groups of a number. A
  section that begins with a list item at the very start of a page with no header is a
  mismatch (there is no line terminator to read the slice from; a false failure). A span with
  a non-integer field is a structural error, not a status.
- **Clients of `verify_quote`** that send a quote with a C1, bidirectional, U+000B or U+000C
  character receive `invalid-request` instead of an answer.
- **Not re-verified.** Fidelity reports and approvals recorded under 1.1.1 stay as recorded;
  they are distinguishable by the version they carry. `docs/roadmap.md` and
  `docs/design/extractor-spike.md` still name 1.1.1 where they record what ran under it.
- **Not closed**, stated in the specification and ADR 0003: **cell association.** Cell
  boundaries flatten to whitespace, so a narrative table with the same text and the same row
  width can put a value in a different cell from the source: a dose can move from the Adults
  column to the Children column and verify. The empty slots section 7 requires for a spanned
  source cell make that easier, because an empty narrative cell costs nothing. It needs a table
  extractor contract. Also: a text layer that flattens a superscript; letter exponents; strong
  right-to-left letters reordering adjacent numbers; a viewer's own stylesheet or script acting
  on the element names that remain; and a section edge that touches whitespace but not the end
  of a clause ("Take 5" of "Take 5 mg twice").

**Step 7.** Previously approved submissions require re-approval: the version is part of the
approved content hash. That is the demonstration store's approvals, re-seeded and re-approved by
the approval build's migration, not by this change (above).

**Approval (step 8).** Pending. This change touches an approved hash, so it needs a quality
representative other than the author before merge; the approval is recorded in the pull request.
Branch protection does not yet require a second reviewer (see "Release criteria"), so until it
does this is a procedural control only.
