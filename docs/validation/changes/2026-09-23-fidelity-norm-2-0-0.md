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
  is refused, not re-evaluated; it must be prepared again (Zone A is on 2.0.0 in this change).
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
  version string and the hashes that embed it.
- **Zone A** (`zone-a/src/zone_a/fidelity/`): ported to the same rules in this change; see steps
  2 and 4.
- **Agent** (`agent/`): reads `normalizationVersion` as an opaque string; only test literals
  named 1.1.1 (`agent/tests/fake_query_service.py`, `agent/tests/test_postcheck.py`), now 2.0.0.
- **Spike scripts** (`scripts/spikes/document-ai/`): their mirrors of the section 2 list and the
  section 3 whitespace list follow 2.0.0. The recorded Document AI replay still verifies 32 of
  32 sections; its `reportHash` moved from the verdict's `12449bee…` to `be91473d…`, and
  replaying with the version string set back to 1.1.1 reproduces `12449bee…` exactly, so the
  move is the version string alone (`test/spikes/document-ai-verdict.test.ts`).
- **Previously produced evidence** remains reproducible only under 1.1.1: every fidelity report,
  binding hash and approved content hash recorded under 1.1.1 names that version, and this code
  refuses to re-verify it (a structural error, by design).

**Steps 1–6.** 1: `NORMALIZATION_VERSION` is `fidelity-norm/2.0.0` on both sides. 2:
`npm run contracts:generate` (no drift), `npm run vectors:generate`, `npm run contracts:fixtures`,
`npm run contracts:quote-edge` and `npm run differential:smoke` regenerated
`test/fixtures/fidelity/vectors.json` (137 → 307 vectors: normalisation 25 → 44, XHTML 60 → 176,
verify 52 → 87), the four contract fixtures and the smoke corpus; no `query-tools` or
`agent-turn` change, so no vendored copy moved; `zone-a/scripts/generate_models.py --check`
passes. 3: every changed vector, below. 4: the 170 new vectors — every row of the design note's
table, every amendment's boundary, and both the rejecting and the accepting side of each rule
(for example `<sup>2</sup>` → `²`, `<sup>a</sup>` kept, `<sup>–6</sup>` → `⁻⁶`, `<sup>±1</sup>`
rejects, a non-ASCII digit in `sup` rejects, whitespace-only text in table parts accepted, an
empty table accepted, R1's page without a final line feed, R4's blank-line and previous-page
starts, R6's escaped surrogate pair accepted as one code point — the last pinned by a test on
each side, because `JSON.stringify` never writes the escaped form into a vector); three
structural cases (a missing page, pages out of order, numbering from 2) in
`test/fixtures/fidelity/cases.ts`; the `verify_quote` acceptance case above; and the extended
differential generator (below). 5: ADR 0003 amended (Decision item 1 and Consequences). 6: UR-09
(design control and evidence) and UR-22 (the `invalid-request` note) updated.

**Changed vectors (step 3).** Every existing verify vector's `reportHash` and input
`normalizationVersion` moved, because the report carries the version; for 50 of the 52 that is
the only change (every other member of `expected` compared equal with those two removed). The
vectors whose outcome changed, each reviewed:

| Vector (family)                                | 1.1.1                | 2.0.0                                                   | Reason                                                                                                                                                                                              |
| ---------------------------------------------- | -------------------- | ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `allows-tab-lf-cr-ff-vt` (normalisation)       | `a b c d e f`        | `forbidden-character`                                   | R2: U+000B and U+000C reject on both sides. Name kept so the change is reviewable against 1.1.1; `allows-tab-lf-cr`, `rejects-vertical-tab` and `rejects-form-feed` state the new rule.             |
| `inline-dropped` (XHTML)                       | `…c 2`               | `…c ²`                                                  | B: a digit inside `sup` folds to its script code point.                                                                                                                                             |
| `allowed-attributes` (XHTML)                   | accepted             | `forbidden-attribute`                                   | F/R10: the root's `id` (then `class`, and the `#x` link) are no longer allowed; the first decides. `accepts-lang-on-root` is the accepting form.                                                    |
| `superscript-markup-over-plain-digit` (verify) | `passed`, `verified` | `failed`, `mismatch`                                    | B: the case is the defect — `m<sup>2</sup>` and `H<sub>2</sub>O` against a source's `m2`, `H2O`. The generator fills the provenance's `normalizedTextSha256` from the new text, so the input moved. |
| `hidden-extra-element` (verify)                | `failed`, `mismatch` | `failed`, `malformed-narrative` (`forbidden-attribute`) | F: `class` is refused before any comparison. The narrative no longer normalises, so the binding hash and the filled `normalizedTextSha256` moved too.                                               |

No vector used `colspan`, `rowspan`, `pre`, `lang` below the root, U+0085 as whitespace or a
non-self-closing `br`. Seven XHTML vectors keep their outcome, `forbidden-attribute`, but are now
decided by the attribute's name rather than its value grammar: `rejects-four-class-tokens`,
`rejects-class-with-text-after-newline`, `rejects-free-text-class` (`class` is gone),
`rejects-id-with-trailing-newline`, `rejects-non-token-id`, `rejects-duplicate-attribute` (`id`
is gone; `rejects-duplicate-root-lang` now pins the duplicate rule) and
`rejects-href-with-trailing-newline` (the `#` form is gone; `rejects-fragment-href` pins it).
`rejects-cell-outside-row` stays `misnested-tag` and `rejects-unknown-element` (`<img/>`) stays
`unknown-element`, as R3 pins. The implementation confirms the re-review's simulation of R4: no
vector's outcome changed because of the new start and end rules (the two verify changes above
come from B and F).

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
sentence and ending at a page end or just after U+00AD and whitespace). Run as CI runs it
(`npx tsx scripts/fidelity/differential.ts --seed <s> --count 2000 > <file>`, then
`DIFFERENTIAL_CORPUS=<file> uv run --frozen pytest` in `zone-a/`) at seeds 20260920, 1 and 2:
2002 passed each, **zero divergences**, and the class-coverage test (now requiring 34 more
classes) passes on each. Sixteen rules were then broken in the Python port one at a time; every
break diverged on all three seeds (20260920 / 1 / 2): body without its final line feed at a
page end accepted 44 / 35 / 47; U+000B and U+000C allowed 5 / 9 / 2; U+2066–U+2069 allowed
9 / 10 / 14; start rule stopping at the page start 18 / 13 / 23; start rule cutting after
skipped whitespace 201 / 135 / 166; 1..N page check dropped 49 / 47 / 43; U+2013 not folded in
`sup` 3 / 2 / 2; numbers without a script form kept 17 / 14 / 18; the whole-`div` section 2
check skipped 13 / 11 / 13; any element allowed to self-close 12 / 14 / 12; `void-element`
decided after the parent check 6 / 7 / 6; U+00AD before CR LF accepted 17 / 26 / 11;
`table-shape` skipped 29 / 18 / 25; a whitespace reference allowed in a table part 5 / 5 / 4;
`lang` allowed below the root 2 / 1 / 2; end rule not reading through trailing whitespace
14 / 22 / 17. Each break was restored. Two of these were invisible to the first version of the
extended generator and are why the `void-element-and-parent` class and the `span-page-end` and
`span-ends-after-soft-hyphen-space` layouts exist.

**Blast radius.**

- **Every submission carrying 1.1.1 is refused by the worker gate** from the moment this change
  deploys (the worker and the query service ship together, from `main`). Zone A emits 2.0.0 in
  this change.
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
  empty); the engine (roadmap item 8) must produce them that way.
- **Clients of `verify_quote`** that send a quote with a C1, bidirectional, U+000B or U+000C
  character receive `invalid-request` instead of an answer.
- **Not re-verified.** Fidelity reports and approvals recorded under 1.1.1 stay as recorded;
  they are distinguishable by the version they carry. `docs/roadmap.md` and
  `docs/design/extractor-spike.md` still name 1.1.1 where they record what ran under it.
- **Not closed**, stated in the specification and ADR 0003: cell association in tables of equal
  row width; a text layer that flattens a superscript; letter exponents; strong right-to-left
  letters reordering adjacent numbers; a viewer's own stylesheet or script acting on the element
  names that remain.

**Step 7.** Previously approved submissions require re-approval: the version is part of the
approved content hash. That is the demonstration store's approvals, re-seeded and re-approved by
the approval build's migration, not by this change (above).

**Approval (step 8).** Pending. This change touches an approved hash, so it needs a quality
representative other than the author before merge; the approval is recorded in the pull request.
Branch protection does not yet require a second reviewer (see "Release criteria"), so until it
does this is a procedural control only.
