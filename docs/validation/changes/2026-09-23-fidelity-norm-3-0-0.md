# Recorded change: `fidelity-norm/2.0.0` → `fidelity-norm/3.0.0`, 2026-09-23

_Follows the procedure (steps 0–8) in `docs/validation/README.md`, "Change control for shared,
evidenced libraries". The UR- rows it cites are in that file._

**What changed.** `NORMALIZATION_VERSION` moved from `fidelity-norm/2.0.0` to
`fidelity-norm/3.0.0` in `src/fidelity/normalize.ts` and `zone-a/src/zone_a/fidelity/normalize.py`,
and `docs/fidelity-normalization.md` was updated to 3.0.0 (the preamble and sections 2, 4, 5, 7
and 9; sections 1, 3, 6 and 8 unchanged). The design is `docs/design/fidelity-norm-3-0-0.md` as
amended by its independent reviews, prompted by roadmap item 3a and ADR 0005. In short:

- `ol` is allowed with `type` (`1 a A i I`) and `start` (`0|-?[1-9][0-9]{0,3}`), and each of its
  items emits the marker a renderer draws (decimal, alphabetic, additive roman), `.` and a space;
- `li` is allowed only directly in `ol` or `ul`, whose only children are `li` (`list-content`);
- `colspan` and `rowspan` (`[1-9][0-9]{0,2}|1000`) return, placed by the HTML table model, with
  an overlapping cell, a row span clipped at its row group's end, a hole and a ragged row
  refused (`table-shape`), and a table inside a cell refused (`table-structure`);
- every table's text carries its grid in the reserved code points U+FDD0–U+FDD5 (table, end of
  table, row, cell, slot covered from the left, slot covered from above);
- `img` is allowed with `src` alone (a reference, or a PNG or JPEG `data:` URI) and emits U+FFFC
  and the SHA-256 of its `src`;
- U+FFFC and U+FDD0–U+FDEF reject in narrative (`reserved-character`), the one rule applied to
  one side only;
- the extractor contract writes tables with their grid, numbered markers with a space, pictures
  with their hash, and a structured source as one page per section.

`XhtmlErrorCode` gains `list-content` and `reserved-character`.

**Why.** Roadmap item 3a takes the EMA's own ePI for Imatinib Teva through the system. Its
summary of product characteristics has six numbered lists, 46 cells spanning columns, five
spanning rows and two pictures; 2.0.0 refused all four constructs, each because a renderer draws
something its check did not see. 3.0.0 folds what the renderer draws into the text the check
compares (ADR 0003: false failures are acceptable, false passes are not). The design's first
independent review found that spans widen 2.0.0's stated cell-association residual (a row span
draws "10 mg" against every age group while the text reads as if it applied to one), that an
`li` outside a direct `ol` parent is still numbered, and that a picture's content was unbound.
The design was changed so that the text carries each table's grid, which also closes the
residual, and each picture is bound to its source by hash.

**Impact assessment (step 0).** Importers of `src/fidelity/`:

- **Worker gate** (`src/pipeline.ts`, `src/contracts/canonical-submission.ts`): refuses any
  submission whose `fidelity.normalizationVersion` or fidelity report names another version. A
  2.0.0 submission is refused, not re-evaluated. The only submission producer in the
  repository is the synthetic builder, which takes the version from the constant.
- **Crosswalk** (`src/fhir/transform.ts`): decides whether a section's narrative is present
  from the scanner's visible text. The grid markers are not visible content, so a table of
  empty cells is still absent; a picture and a list number are drawn, so they count.
- **Query service** (`src/query/tools.ts`): `get_section` returns normalised text, so a
  section's table text now carries the grid markers and a picture's token. `verify_quote`
  matches a quote inside one cell, a list item with or without its number, and text on either
  side of a picture. A quote that runs across two cells or across a picture is `no-match`: a
  false failure, and a correct one, because such a quote loses which cell a value is in or what
  stands between the words. The agent's instructions and the query's presentation of the
  markers are changed in roadmap item 3a's publishing step (PR 5); until then the demonstration
  store holds no table, list or picture (below). UR-22 records it.
- **Contracts** (`src/contracts/`): no schema changed (`NormalizationVersion` is a pattern).
  The four contract fixtures moved only in the version string and the hashes that embed it.
- **Synthetic fixtures and demonstration store**: the synthetic submission and the seeded
  demonstration products have no table, `ol` or `img` (`src/fixtures/synthetic-submission.ts`,
  `scripts/demo/`), so their narratives normalise to the same text under 3.0.0.
- **Zone A** (`zone-a/src/zone_a/fidelity/`): ported to the same rules in this change.
- **Agent** (`agent/`): reads `normalizationVersion` as an opaque string; only test literals
  changed.
- **Spike scripts** (`scripts/spikes/document-ai/`): the recorded Document AI replay still
  verifies 32 of 32 sections. Its one table lies outside every section, so its report changes
  only in the version string; the pinned `reportHash` moved to `8e5bbf17…`. The spike adapter
  still writes tables in the 2.0.0 form; it is a recorded experiment, not a controlled
  extractor, and a real extractor must follow section 7.
- **ePI reader** (`zone-a/src/zone_a/epi/reader.py`): unchanged here. It becomes an extractor
  under section 7 in ADR 0005's importer (PR 3), which must emit list numbers, grids and picture
  tokens exactly as section 5 does.

**Steps 1–6.** 1: `NORMALIZATION_VERSION` is `fidelity-norm/3.0.0` on both sides. 2:
`npm run contracts:generate` (no drift), `npm run vectors:generate`, `npm run contracts:fixtures`,
`npm run contracts:quote-edge` and `npm run differential:smoke` regenerated
`test/fixtures/fidelity/vectors.json` (411 → 479 vectors: normalisation 62, XHTML 214 → 272,
verify 135 → 145), the four contract fixtures and the smoke corpus. 3: every changed vector,
below. 4: the new vectors: every case the design names, both sides of every boundary (counter
styles at 26/27, 702/703, 3999/4000, −1/0/1; `start` at `-0`, `007`, `9999`, `10000`; spans at
0, `02`, 1000, 1001; each `src` form and each refused form; each reserved code point raw and by
reference, and its neighbours), placement cases (overlap, clip at `thead` and at bare rows, a
hole, a ragged row, a row span to its group's end, covered slots before, after and between
cells), and verification cases (a list number against another number and another style, a row
span against empty cells and the reverse, a value moved to another column, a picture against
another reference and against none). 5: ADR 0003 amended (Consequences), ADR 0001 amended for
ADR 0005's Type 1 record, ADR 0005 added, `AGENTS.md`'s "Preserve supplied XHTML" reworded to
the owner's decision of 2026-09-23. 6: UR-09 and UR-22 updated.

**Changed vectors (step 3).** Every verify vector's `reportHash` and input `normalizationVersion`
moved, because the report carries the version. The shared three-page source's table (page 3) is
now written with its grid, so every verify vector built on that source also moves in
`extractedTextSha256` and its coverage figures, with no status or reason changed. The vectors
whose outcome changed, each reviewed:

| Vector (family)                                                                                                                                                                                                                                                                                                                                                           | 2.0.0                                         | 3.0.0                               | Reason                                                                                                                                |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `rejects-ol` → `accepts-ol` (xhtml)                                                                                                                                                                                                                                                                                                                                       | `unknown-element`                             | `1. a`                              | `ol` is allowed; its marker is text.                                                                                                  |
| `rejects-colspan` → `accepts-colspan` (xhtml)                                                                                                                                                                                                                                                                                                                             | `forbidden-attribute`                         | grid with one covered-left slot     | Spans are allowed and carried in the grid.                                                                                            |
| `rejects-rowspan` → `rejects-rowspan-past-group` (xhtml)                                                                                                                                                                                                                                                                                                                  | `forbidden-attribute`                         | `table-shape`                       | A row span of 2 in a one-row table is clipped by a renderer.                                                                          |
| `accepts-nested-table-in-cell` → `rejects-nested-table-in-cell` (xhtml)                                                                                                                                                                                                                                                                                                   | text                                          | `table-structure`                   | Nested tables are refused, so grid text never nests.                                                                                  |
| `rejects-uneven-nested-table` (xhtml)                                                                                                                                                                                                                                                                                                                                     | `table-shape`                                 | `table-structure`                   | The nested table is refused before its shape is decided.                                                                              |
| `rejects-unknown-element`, `soft-hyphen-decided-after-scan`, `rejects-unknown-before-root` (xhtml)                                                                                                                                                                                                                                                                        | `unknown-element`                             | `unknown-element`                   | Input changed from `img` to `iframe`, since `img` is now known; the rule each pins is unchanged.                                      |
| 11 accepted tables (`table`, `accepts-table-section-order`, `accepts-caption-first`, `accepts-scope-on-th`, `accepts-whitespace-in-table-parts`, `accepts-header-and-data-cells`, `accepts-empty-table`, `accepts-caption-only-table`, `accepts-empty-rows`, `accepts-ascii-whitespace-in-tags`, `cell-breaks-are-tabs`, `accepts-soft-hyphen-before-br-in-cell`) (xhtml) | text                                          | the same text with the grid markers | Every table's text carries its grid; nothing else moved.                                                                              |
| `spanned-cell-rejected` → `spanned-cell-against-separate-cells` (verify)                                                                                                                                                                                                                                                                                                  | `malformed-narrative` / `forbidden-attribute` | `mismatch`                          | A span is allowed; drawing two source cells as one spanned cell is a different table.                                                 |
| `row-cell-cut-before-tab-against-cell` (verify)                                                                                                                                                                                                                                                                                                                           | `verified`                                    | `mismatch`                          | A narrative table carries its grid and so verifies only against a page with the same grid; this 2.0.0-shaped row has none.            |
| `bullet-in-*-cell-*`, `bullet-row-against-cells-without-bullet`, `bullet-in-cell-paragraph-against-row` (verify)                                                                                                                                                                                                                                                          | as before                                     | as before                           | The sources were rewritten in the 3.0.0 table form; each outcome, and the rule it pins (a bullet in a cell is content), is unchanged. |

**Differential proof.** `scripts/fidelity/differential.ts` now generates ordered lists (every
style, starts at the edges of each range, 28-item lists), grids with column and row spans
placed by the table model, with deliberate overlaps, clipped spans and ragged rows, pictures
with every accepted source form, and violation classes for list content, `li` outside a list,
list attributes, attribute limits, nested tables, span overlaps and holes, picture sources,
`data:` bodies and markup, and reserved code points raw and by reference. Violation classes are
now drawn in turn rather than at random: with this many classes, a random draw left one class
out of a 2000-case corpus at some seeds. Zero divergences at seeds 20260920, 1, 2, 3 and 4 (2000
cases each) and at seeds 1, 2 and 3 (6000 cases each). Breaking each rule in the Python port
(divergences in the differential at seeds 20260920 / 1 / 2, then failures in the golden
vectors):

| Break                                     | Differential    | Vectors |
| ----------------------------------------- | --------------- | ------- |
| alphabetic counter off by one             | 9 / 13 / 10     | 3       |
| roman range to 4000                       | 4 / 5 / 1       | 1       |
| ordinal not advanced                      | 23 / 32 / 25    | 5       |
| `li` allowed anywhere                     | 127 / 136 / 118 | 22      |
| an element allowed in a list              | 2 / 3 / 3       | 1       |
| text allowed in a list                    | 1 / 0 / 1       | 2       |
| overlap not checked                       | 6 / 3 / 3       | 1       |
| clipped row span not checked              | 4 / 6 / 5       | 2       |
| covered-left slots dropped                | 9 / 14 / 17     | 5       |
| covered-above slots before a cell dropped | 4 / 4 / 5       | 3       |
| covered-above slots after a row dropped   | 11 / 11 / 7     | 3       |
| cell marker dropped                       | 30 / 37 / 38    | 31      |
| row marker dropped                        | 42 / 48 / 54    | 32      |
| end-of-table marker dropped               | 44 / 53 / 60    | 34      |
| nested table allowed                      | 4 / 7 / 5       | 2       |
| picture hashed from another value         | 4 / 9 / 9       | 1       |
| reserved check of the whole `div` dropped | 2 / 4 / 7       | 1       |
| reserved reference allowed                | 3 / 5 / 3       | 4       |
| reserved range narrowed to U+FDD0–U+FDD5  | 1 / 5 / 4       | 1       |
| span 1000 refused                         | 2 / 1 / 1       | 1       |
| `start="-0"` accepted                     | 0 / 0 / 0       | 1       |
| `data:` padding inside the body accepted  | 1 / 4 / 3       | 1       |
| dot-initial `src` segment accepted        | 1 / 6 / 4       | 2       |
| `img` without `src` accepted              | 1 / 1 / 0       | 1       |
| `img` not a void element                  | 30 / 38 / 40    | 7       |
| a row with a hole not refused             | 0 / 0 / 0       | 0       |

Every break but one is caught, by the differential on at least one seed and by the vectors.
`start="-0"` is drawn rarely enough that an earlier error in the same generated document masks
it at these seeds; the vectors pin it. The hole check is never decisive on its own: a row with a
hole always also covers fewer slots than the row its row span starts in (a cell takes the first
uncovered slot, so the span's first row covers every slot up to the spanned column), so the
unequal-width check already refuses it. It is kept because the specification states the rule
directly. Each break was restored.

**Blast radius.**

- **Every submission carrying 2.0.0 is refused by the worker gate** from the moment this change
  deploys. Nothing in the repository produces a 2.0.0 submission after it.
- **The persisted demonstration documents were approved under an earlier version** and are not
  re-seeded in this change. As in 2.0.0's record, the approval build's migration re-seeds and
  re-approves them; their narratives have no table, list or picture, so their normalised text is
  unchanged.
- **Extractors.** Output that conformed to 2.0.0 does not conform to 3.0.0 for any page with a
  table (the grid markers), a numbered list whose marker is followed by U+0009 or nothing, or a
  picture. An extractor that cannot recover a table's grid or place a picture must refuse the
  document.
- **Narratives.** A narrative with an `li` outside a direct `ol` or `ul` parent, anything but
  `li` directly in a `ul`, a table inside a cell, U+FFFC or U+FDD0–U+FDEF is now
  `malformed-narrative`. Every narrative with a table has new normalised text, and so a new
  `normalizedTextSha256` and binding hash.
- **Not closed**, stated in the specification and the design: what a picture shows (a text
  check cannot read it; for a reference, the hash binds the reference and not what it resolves
  to); a viewer that restyles lists or draws no numbers; `td` versus `th`, `scope` and row
  groups, which are presentation; and the residuals 2.0.0 stated other than cell association.

**Step 7.** Previously approved submissions require re-approval: the version is part of the
approved content hash.

**Approval (step 8).** Pending. This change touches an approved hash, so it needs a quality
representative other than the author before merge; the approval is recorded in the pull request.
Branch protection does not yet require a second reviewer, so until it does this is a procedural
control only.
