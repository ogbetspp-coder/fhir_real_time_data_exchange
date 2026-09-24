# Recorded change: `fidelity-norm/2.0.0` → `fidelity-norm/3.0.0`, 2026-09-23

_Follows the procedure (steps 0–8) in `docs/validation/README.md`, "Change control for shared,
evidenced libraries". The UR- rows it cites are in that file._

**What changed.** `NORMALIZATION_VERSION` moved from `fidelity-norm/2.0.0` to
`fidelity-norm/3.0.0` in `src/fidelity/normalize.ts` and `zone-a/src/zone_a/fidelity/normalize.py`,
and `docs/fidelity-normalization.md` was updated to 3.0.0 (the preamble and sections 2, 4, 5, 7
and 9; section 3 step 5, which no longer counts U+1680, U+2006, U+2009, U+200A and U+202F as
whitespace; and section 6's edge wording, which follows; sections 1 and 8 unchanged). The design is `docs/design/fidelity-norm-3-0-0.md` as
amended by its independent reviews, prompted by roadmap item 3a and ADR 0005. In short:

- `ol` is allowed with `type` (`1 a A i I`) and `start` (`0|-?[1-9][0-9]{0,3}`), and each of its
  items emits the marker a renderer draws (decimal, alphabetic, additive roman), `.` and a space;
- `li` is allowed only directly in `ol` or `ul`, whose only children are `li` (`list-content`);
- `colspan` and `rowspan` (`[1-9][0-9]{0,2}|1000`) return, placed by the HTML table model, with
  an overlapping cell, a row span clipped at its row group's end, a hole and a ragged row
  refused, and rows drawn at zero height and columns drawn at zero width refused
  (`table-shape`), a table inside an open table refused (`table-structure`), and at most
  50 000 slots per narrative (`table-size`);
- every table's text carries its grid in the reserved code points U+FDD0–U+FDD5 (table, end of
  table, row, cell, slot covered from the left, slot covered from above);
- `img` is allowed with `src` alone, a PNG or JPEG `data:` URI (references are refused), and
  emits U+FFFC, the SHA-256 of its `src` and U+FFFC;
- a narrative whose normalised text is only spaces and grid markers is `empty-narrative`, and
  the crosswalk ignores grid markers, and for a mandatory section pictures, when it decides
  whether a section carries narrative;
- U+FFFC and U+FDD0–U+FDEF reject in narrative (`reserved-character`), and so do U+00AD and
  U+200B (`invisible-character`, withdrawing `soft-hyphen-at-boundary`): the two rules applied
  to one side only;
- U+1680 and the spaces narrower than a quarter of an em (U+2006, U+2009, U+200A, U+202F,
  U+205F) leave the section 3 whitespace list, since a renderer draws them as a stroke or a few
  pixels wide; section 6's digit-group rule and the quote-edge rule read past them, U+2800
  BRAILLE PATTERN BLANK and every Default_Ignorable code point, as a gap; a narrative of gaps
  alone is `empty-narrative`, and a quote of gaps alone `invalid-request`;
- nesting is bounded (`nesting-depth`, with at most one of `small`, `code`, `h5` and `h6` open),
  `]]>` in text rejects (`cdata`), a combining mark after an inline tag (past any ignorable code
  point), or a composition across one, rejects (`combining-across-markup`), and `u` and `a`
  reject (`unknown-element`), with `href`: an underline turns a sign into another, "<" into
  "≤", which no closed list of code points bounds; and an `hr` in a table cell or caption
  rejects (`table-content`), since it is drawn as a fraction bar;
- section 2 adds the interlinear annotation controls U+FFF9–U+FFFB and the prepended
  concatenation marks, and section 6's gaps add seven Mongolian and Yi letters Chrome's default
  serif face draws blank (`scripts/fidelity/blank-glyph-sweep.md`);
- the extractor contract writes tables with their grid (and a table across pages as its logical
  text, split once per break), numbered markers with a space, pictures with their hash, a
  continuation line that begins with a bullet glyph with a leading U+0009, and a structured
  source as one page per section.

`XhtmlErrorCode` gains `list-content`, `reserved-character`, `table-size`,
`invisible-character`, `nesting-depth` and `combining-across-markup`, and loses
`soft-hyphen-at-boundary`.

**Why.** Roadmap item 3a takes the EMA's own ePI for Imatinib Teva through the system. Its
summary of product characteristics has six numbered lists, 46 cells spanning columns, five
spanning rows and two pictures; 2.0.0 refused all four constructs, each because a renderer draws
something its check did not see. 3.0.0 folds what the renderer draws into the text the check
compares (ADR 0003: false failures are acceptable, false passes are not). The design's first
independent review found that spans widen 2.0.0's stated cell-association residual (a row span
draws "10 mg" against every age group while the text reads as if it applied to one), that an
`li` outside a direct `ol` parent is still numbered, and that a picture's content was unbound.
The design was changed so that the text carries each table's grid, which also closes the
residual, and each picture is bound to its source by hash. The second review, of the amended
design and a draft of the code, found a table nested in a caption, unbound picture references
(the EMA's draw at zero size), an unbounded grid size, an empty table counted as present, a
combining mark joining a picture's hash, and a residual the design overclaimed (the line a
value sits on inside a multi-line cell). Each was fixed before this change, or, for the line,
stated. A third review, of the implementation, found rows and columns a renderer draws at
zero size (a false pass the second review had called a false failure), a scan whose cost grew
with rows × table width, and three untested precedence rules; each was fixed. The design note
lists all twelve reviews' findings and what changed (two of the design, ten of the
implementation). A fourth review found a cell continued
across a page break losing a leading bullet on the page side (fixed in section 7, with two verify
vectors), and quotes carrying grid markers matching across rows (now `invalid-request`). A fifth found that rule stopping a word hyphenated across the break
inside a cell from joining (fixed in section 7: the continuation then begins with the rest of
the word; two verify vectors), and the agent's test double answering `match` where the
service now refuses (fixed). A sixth found no conforming output for a row broken across a
page in more than one cell (section 7 now keeps each cell's text whole, in slot order, on the
later page; two verify vectors), and the test double still accepting quotes that normalise to
nothing through invisible characters or a line-start bullet (fixed; it now agrees with the
service on all 11 110 strings of up to four characters from those classes). A seventh found three more page-break layouts (a page footnote between a
table's parts, a spanning cell's overflow, a repeated footer row); since rounds 4 to 7 each
found one, section 7's page-break text was rewritten as one rule (a table across pages is its
logical text, split once per break) and six verify vectors pin the new cases. An eighth tested that rule on every split point of 450
random tables (105 247 cases, identical in both languages) and found a caption continuation and,
present since 2.0.0, a paragraph wrapped before a mid-line bullet losing the bullet on the page
side; section 7 now has the extractor write such a continuation line with a leading U+0009, a
table drawn inside a cell is refused, and seven verify vectors pin the cases.

After the ninth review the version was scoped: it qualifies structured sources only (an FHIR
ePI with XHTML narratives), and drawn-document extraction is not qualified until a later version
closes the open items the reviews recorded (the blast radius below says why).

**Impact assessment (step 0).** Importers of `src/fidelity/`:

- **Worker gate** (`src/pipeline.ts`, `src/contracts/canonical-submission.ts`): refuses any
  submission whose `fidelity.normalizationVersion` or fidelity report names another version. A
  2.0.0 submission is refused, not re-evaluated. The only submission producer in the
  repository is the synthetic builder, which takes the version from the constant.
- **Crosswalk** (`src/fhir/transform.ts`): decides whether a section's narrative is present
  from the scanner's visible text. The grid markers are not visible content, so a table of
  empty cells is still absent. A list number is drawn, so it counts. A picture counts where an
  uncoded section would otherwise be dropped (the picture would be lost with it), and not
  where a mandatory section must carry narrative (a picture can draw nothing).
- **Query service** (`src/query/tools.ts`): `get_section` returns normalised text, so a
  section's table text now carries the grid markers and a picture's token. `verify_quote`
  matches a quote inside one cell, a list item with or without its number, and text separated
  from a picture by whitespace (text touching a picture is cut by the quote-edge rule). A quote
  at a word boundary inside a table cell is held to the digit and sign rules against every word
  of every cell on its side, in each row its cell covers, the grid rebuilt from the markers once
  per search ("10" | "000 IU" is drawn as one number, and a cell's lines are centred, so any of
  them can sit level with the quote); the agent's port follows. On the three pinned SmPCs this
  refuses 196 of 789 whole-cell quotes that a reader might read apart (a fail-safe `no-match`,
  stated in UR-22; `agent/scripts/measure_table_quotes.py`). A quote that runs across two cells or across a picture is `no-match`, and a quote that
  carries a grid marker or U+FFFC itself is `invalid-request` (it could join two rows, or quote
  nothing a reader sees): a
  false failure, and a correct one, because such a quote loses which cell a value is in or what
  stands between the words. The agent's instructions and the query's presentation of the
  markers are changed in roadmap item 3a's publishing step (PR 5); until then the demonstration
  store holds no table, list or picture (below). UR-22 records it. `get_section`'s
  contract caps a `div` at 200 000 code points (`src/contracts/query-tools.ts`), while a
  picture's `src` may be 1 398 104: a section holding a picture larger than about 150 KB is
  `unavailable` from `get_section`. Not changed here, because the query contract is versioned
  and vendored by the agent; it is recorded for the publishing step (PR 5), and the Imatinib Teva import carries no picture. The
  normalised `text` has the same cap, and a spanned grid makes it much longer than its markup
  (an 11 738-code-point `div` of one row of 1000 cells and 49 rows spanning them normalises to
  102 201), so a `div` of about 110 000 code points can be `unavailable` there too.
- **Cost.** The scan and the normalisation are linear in the narrative's length, which no
  per-section bound limits (the submission may be 64 MB). The TypeScript takes at most 0.42 s on
  2 MB inputs of 45 000 pictures, 220 000 empty rows, 200 000 list items or deep lists; the Python
  port takes up to 1.5 s, and picture tokens make the text 1.65 times the markup. Recorded, not
  bounded: the Python runs offline in Zone A.
- **Crosswalk, a bare list number.** `<ol start="2"><li></li></ol>` counts as narrative for a
  mandatory section: its number is drawn. Kept deliberately; the fidelity check still requires
  the source to show the same empty numbered item.
- **Contracts** (`src/contracts/`): no schema changed (`NormalizationVersion` is a pattern).
  The four contract fixtures moved only in the version string and the hashes that embed it.
- **Synthetic fixtures and demonstration store**: the synthetic submission and the seeded
  demonstration products have no table, `ol` or `img` (`src/fixtures/synthetic-submission.ts`,
  `scripts/demo/`), so their narratives normalise to the same text under 3.0.0.
- **Zone A** (`zone-a/src/zone_a/fidelity/`): ported to the same rules in this change.
- **Agent** (`agent/`): reads `normalizationVersion` as an opaque string. It quotes a
  section's whole `get_section` text back through `verify_quote`, so from this change a block
  quoted from a section with a table or a picture carries the markers, the service answers
  `invalid-request`, and the agent marks the block `verification-unavailable`: safe, never a
  false `match`, but unverified. The agent's presentation of tables and pictures, and its
  quoting of them cell by cell, belong to roadmap item 3a's publishing step (PR 5); the
  demonstration store holds no table or picture until then. The agent's test double
  (`agent/tests/fake_query_service.py`) now refuses the quotes the service refuses, and a test
  pins it.
- **Spike scripts** (`scripts/spikes/document-ai/`): the recorded Document AI replay still
  verifies 32 of 32 sections. Its one table lies outside every section, so its report changes
  only in the version string; the pinned `reportHash` moved to `8e5bbf17…`. The spike adapter
  still writes tables in the 2.0.0 form; it is a recorded experiment, not a controlled
  extractor, and a real extractor must follow section 7.
- **Readers** (`zone-a/src/zone_a/epi/reader.py`, now `epi-reader/1.1.0`, and
  `zone-a/src/zone_a/docx/reader.py`, now `docx-reader/1.1.0`): both mark an underline (`u`, `a`
  with a target, `text-decoration: underline` and a border on inline text; Word's `w:u`), since
  an underlined "<" is drawn "≤". What an underline can change is one rule,
  `zone-a/src/zone_a/underline.py` (ADR 0005's allowlist). The QRD registry accepts an underline
  only where it changes nothing (the template's brackets and a hyphen inside a word allowed); its
  content is unchanged and only its reader version moved. The ePI reader marks a border on
  another side of inline text as `border` (a bar beside or over it), which the check reports.
  The QRD check (now `qrd-check/1.1.0`) reports an underline
  over what it can change as a formatting finding, which adds four to the committed checks:
  Brukinsa's 5.1 writes ">1", ">5" and ">2" with the ">" underlined (the EMA's viewer draws
  "≥"), and Jentadueto underlines a 5.1 heading holding "≥". The ePI reader becomes an extractor
  under section 7 in ADR 0005's importer (PR 3), which must emit list numbers, grids and picture
  tokens exactly as section 5 does.

**Steps 1–6.** 1: `NORMALIZATION_VERSION` is `fidelity-norm/3.0.0` on both sides. 2:
`npm run contracts:generate` (no drift), `npm run vectors:generate`, `npm run contracts:fixtures`,
`npm run contracts:quote-edge` and `npm run differential:smoke` regenerated
`test/fixtures/fidelity/vectors.json` (411 → 591 vectors: normalisation 62 → 71, XHTML 214 → 340, verify 135 → 180; 190 added,
10 removed or renamed), the four contract fixtures and the smoke corpus. 3: every changed vector,
below. 4: the new vectors: every case the design names, both sides of every boundary (counter
styles at 26/27, 703, 3999/4000 (702 in the differential), −1/0/1; `start` at `-0`, `007`, `9999`, `10000`; spans at
0, `02`, 1000, 1001; each `src` form and each refused form; U+FFFC, U+FDD0 and U+FDEF by reference,
U+FDD3 raw, and the neighbours of the reserved block; a table in a caption; the slot limit, with its accepted side in a
unit test on each side because its text is 150 000 code points; a combining mark after a
picture, which stays outside its token under NFC; a picture by reference, refused; the three precedence rules the third review found untested),
placement cases (overlap, a row drawn at zero height, a column drawn at zero width, clip at `thead` and at bare rows, a
hole, a ragged row, a row span to its group's end, covered slots before, after and between
cells), and verification cases (a list number against another number and another style, a row
span against empty cells and the reverse, a value moved to another column, a picture against
other bytes and against none, an empty table as `empty-narrative`, a cell continued across a page break with and without its
bullet, a word in a cell hyphenated across a page break joined and split, a row broken across a page
in two cells with each cell kept whole and with a word moved to the next cell, a page footnote
between a table's parts, a spanning cell broken across a page, a repeated footer row, a caption continued across a
page, a paragraph wrapped before a mid-line bullet, and a repeated header row excluded). 5: ADR 0003 amended (Consequences), ADR 0001 amended for
ADR 0005's Type 1 record, ADR 0005 added, `AGENTS.md`'s "Preserve supplied XHTML" reworded to
the owner's decision of 2026-09-23. 6: UR-09 and UR-22 updated.

**Changed vectors (step 3).** Every verify vector's `reportHash` and input `normalizationVersion`
moved, because the report carries the version. The shared three-page source's table (page 3) is
now written with its grid, so every verify vector built on that source also moves in
`extractedTextSha256` and its coverage figures, with no status or reason changed. The vectors
whose outcome changed, each reviewed:

| Vector (family)                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | 2.0.0                                                | 3.0.0                                                                                                                         | Reason                                                                                                                                |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `rejects-ol` → `accepts-ol` (xhtml)                                                                                                                                                                                                                                                                                                                                                                                                                                                           | `unknown-element`                                    | `1. a`                                                                                                                        | `ol` is allowed; its marker is text.                                                                                                  |
| `rejects-colspan` → `accepts-colspan` (xhtml)                                                                                                                                                                                                                                                                                                                                                                                                                                                 | `forbidden-attribute`                                | grid with one covered-left slot (a row of single cells added, since a column no single cell starts in is drawn at zero width) | Spans are allowed and carried in the grid.                                                                                            |
| `rejects-rowspan` → `rejects-rowspan-past-group` (xhtml)                                                                                                                                                                                                                                                                                                                                                                                                                                      | `forbidden-attribute`                                | `table-shape`                                                                                                                 | A row span of 2 in a one-row table is clipped by a renderer.                                                                          |
| `accepts-nested-table-in-cell` → `rejects-nested-table-in-cell` (xhtml)                                                                                                                                                                                                                                                                                                                                                                                                                       | text                                                 | `table-structure`                                                                                                             | Nested tables are refused, so grid text never nests.                                                                                  |
| `rejects-uneven-nested-table` (xhtml)                                                                                                                                                                                                                                                                                                                                                                                                                                                         | `table-shape`                                        | `table-structure`                                                                                                             | The nested table is refused before its shape is decided.                                                                              |
| 14 soft-hyphen vectors (`rejects-soft-hyphen-before-br`, `rejects-soft-hyphen-before-block-end`, `soft-hyphen-before-raw-lf-is-a-space`, `soft-hyphen-before-lf-reference-is-a-space`, `soft-hyphen-cr-then-br-is-a-space`, `raw-soft-hyphen-before-crlf-is-a-space`, `rejects-soft-hyphen-before-hr`, `soft-hyphen-decided-after-scan`, `unbalanced-before-soft-hyphen`, `rejects-soft-hyphen-before-caption-end`, and four `accepts-soft-hyphen-*` renamed `rejects-soft-hyphen-*`) (xhtml) | `soft-hyphen-at-boundary`, `unbalanced-tag`, or text | `invisible-character`                                                                                                         | Narrative holds no soft hyphen from 3.0.0.                                                                                            |
| `soft-hyphen-entity-in-narrative`, `soft-hyphen-before-cr-and-br`, `soft-hyphen-before-raw-line-feed` and its two `-against-*` variants (verify)                                                                                                                                                                                                                                                                                                                                              | `verified` or `mismatch`                             | `malformed-narrative` / `invisible-character`                                                                                 | The same.                                                                                                                             |
| `unicode-spaces`, `narrow-no-break-space-after-overrides` (normalisation)                                                                                                                                                                                                                                                                                                                                                                                                                     | U+202F → space                                       | U+202F kept                                                                                                                   | U+202F is content.                                                                                                                    |
| `empty-table-div` (verify, new)                                                                                                                                                                                                                                                                                                                                                                                                                                                               | —                                                    | `empty-narrative`                                                                                                             | Grid markers are not drawn text; a table of empty cells draws nothing.                                                                |
| `accepts-https-href` → `rejects-https-link`, `rejects-href-with-query`, `rejects-href-with-trailing-newline`, `rejects-javascript-href`, `rejects-fragment-href`, `rejects-self-closing-anchor` (xhtml)                                                                                                                                                                                                                                                                                       | text, `forbidden-attribute` or `void-element`        | `unknown-element`                                                                                                             | `a` is refused from 3.0.0: a renderer underlines a link, and an underline turns a sign into another.                                  |
| `cell-breaks-are-tabs` (xhtml)                                                                                                                                                                                                                                                                                                                                                                                                                                                                | text                                                 | text (the input drops its `hr`)                                                                                               | An `hr` in a cell is refused from 3.0.0; the vector still pins that a cell's breaks are tabs.                                         |
| `span-ends-with-thin-space-inside-number`, `span-ends-with-narrow-no-break-space-inside-number` (verify)                                                                                                                                                                                                                                                                                                                                                                                      | `invalid-provenance` / `word-cut`                    | `invalid-provenance` / `word-cut` (the narrative now carries the U+2009 or U+202F)                                            | U+2009 and U+202F are content, so the narrative holds them to read as the page does; the cut, and the rule it pins, are unchanged.    |
| `rejects-unknown-element`, `soft-hyphen-decided-after-scan`, `rejects-unknown-before-root` (xhtml)                                                                                                                                                                                                                                                                                                                                                                                            | `unknown-element`                                    | `unknown-element`                                                                                                             | Input changed from `img` to `iframe`, since `img` is now known; the rule each pins is unchanged.                                      |
| 11 accepted tables (`table`, `accepts-table-section-order`, `accepts-caption-first`, `accepts-scope-on-th`, `accepts-whitespace-in-table-parts`, `accepts-header-and-data-cells`, `accepts-empty-table`, `accepts-caption-only-table`, `accepts-empty-rows`, `accepts-ascii-whitespace-in-tags`, `cell-breaks-are-tabs`) (xhtml)                                                                                                                                                              | text                                                 | the same text with the grid markers                                                                                           | Every table's text carries its grid; nothing else moved.                                                                              |
| `spanned-cell-rejected` → `spanned-cell-against-separate-cells` (verify)                                                                                                                                                                                                                                                                                                                                                                                                                      | `malformed-narrative` / `forbidden-attribute`        | `mismatch`                                                                                                                    | A span is allowed; drawing two source cells as one spanned cell is a different table.                                                 |
| `row-cell-cut-before-tab-against-cell` (verify)                                                                                                                                                                                                                                                                                                                                                                                                                                               | `verified`                                           | `mismatch`                                                                                                                    | A narrative table carries its grid and so verifies only against a page with the same grid; this 2.0.0-shaped row has none.            |
| `bullet-in-*-cell-*`, `bullet-row-against-cells-without-bullet`, `bullet-in-cell-paragraph-against-row` (verify)                                                                                                                                                                                                                                                                                                                                                                              | as before                                            | as before                                                                                                                     | The sources were rewritten in the 3.0.0 table form; each outcome, and the rule it pins (a bullet in a cell is content), is unchanged. |

**Differential proof.** `scripts/fidelity/differential.ts` now generates ordered lists (every
style, starts at the edges of each range, 28-item lists), grids with column and row spans
placed by the table model, with deliberate overlaps, clipped spans and ragged rows, pictures
with every accepted source form, and violation classes for list content, `li` outside a list,
list attributes, attribute limits, nested tables (in cells and captions), span overlaps and
holes, the slot limit, picture sources (references included), `data:` bodies and markup,
combining marks after pictures, rows and columns drawn at zero size, the precedence rules of
section 5, and reserved code points raw and by reference. Generated tables are valid by
construction (a table's first row has single-column cells only, each row's first cell spans one
row) and then perturbed. Violation classes are
now drawn in turn rather than at random: with this many classes, a random draw left one class
out of a 2000-case corpus at some seeds. Zero divergences at seeds 20260920, 1, 2, 3 and 4 (2000
cases each) and at seeds 1, 2 and 3 (6000 cases each). Breaking each rule in the Python port
(divergences in the differential at seeds 20260920 / 1 / 2, then failures in the golden
vectors):

| Break                                                     | Differential    | Vectors |
| --------------------------------------------------------- | --------------- | ------- |
| alphabetic counter off by one                             | 6 / 4 / 2       | 3       |
| roman range to 4000                                       | 2 / 1 / 0       | 1       |
| ordinal not advanced                                      | 7 / 7 / 13      | 5       |
| `li` allowed anywhere                                     | 87 / 88 / 91    | 26      |
| an element allowed in a list                              | 0 / 0 / 0       | 2       |
| text allowed in a list                                    | 2 / 2 / 0       | 2       |
| overlap not checked                                       | 2 / 4 / 4       | 2       |
| clipped row span not checked                              | 1 / 2 / 0       | 2       |
| a row with a hole not refused                             | 0 / 0 / 0       | 0       |
| zero-height row not refused                               | 3 / 2 / 2       | 1       |
| zero-width column not refused                             | 4 / 6 / 4       | 1       |
| covered-left slots dropped                                | 4 / 3 / 1       | 4       |
| covered-above slots before a cell dropped                 | 2 / 1 / 1       | 6       |
| covered-above slots after a row dropped                   | 2 / 1 / 2       | 4       |
| cell marker dropped                                       | 14 / 18 / 18    | 48      |
| row marker dropped                                        | 25 / 24 / 28    | 49      |
| end-of-table marker dropped                               | 27 / 26 / 35    | 51      |
| nested table (in a cell or a caption) allowed             | 5 / 7 / 7       | 3       |
| slot limit not checked                                    | 2 / 0 / 0       | 2       |
| `table-size` decided before overlap                       | 0 / 2 / 0       | 1       |
| picture hashed from another value                         | 13 / 17 / 9     | 4       |
| picture token not closed                                  | 13 / 17 / 9     | 4       |
| reference `src` accepted                                  | 5 / 4 / 2       | 6       |
| reserved check of the whole `div` dropped                 | 3 / 2 / 3       | 2       |
| `reserved-character` decided before `forbidden-character` | 0 / 1 / 1       | 1       |
| reserved reference allowed                                | 1 / 4 / 3       | 4       |
| reserved range narrowed to U+FDD0–U+FDD5                  | 1 / 3 / 2       | 1       |
| span 1000 refused                                         | 3 / 3 / 1       | 4       |
| `start="-0"` accepted                                     | 0 / 1 / 0       | 1       |
| `data:` padding inside the body accepted                  | 3 / 1 / 1       | 1       |
| `img` without `src` accepted                              | 2 / 0 / 0       | 2       |
| `void-element` decided before a missing `src`             | 1 / 0 / 0       | 2       |
| nesting depth not bounded                                 | 0 / 1 / 0       | 3       |
| two shrinking elements allowed                            | 5 / 5 / 3       | 4       |
| heading inside a heading allowed                          | 11 / 9 / 14     | 1       |
| mark after an inline tag accepted                         | 2 / 0 / 2       | 12      |
| mark after an ignorable code point accepted               | 1 / 0 / 1       | 5       |
| marks narrowed to Mn                                      | 0 / 0 / 1       | 2       |
| `h5` not shrinking                                        | 1 / 1 / 2       | 1       |
| narrative of gaps counted as drawn                        | 0 / 0 / 2       | 3       |
| indenting containers not bounded                          | 0 / 0 / 0       | 2       |
| `]]>` in text accepted                                    | 2 / 1 / 1       | 2       |
| composition across markup not checked                     | 3 / 1 / 2       | 17      |
| invisible break in the div accepted                       | 398 / 416 / 401 | 3       |
| invisible break by reference accepted                     | 2 / 1 / 2       | 19      |
| U+2800 not a gap                                          | 0 / 0 / 1       | 2       |
| U+205F whitespace again                                   | 24 / 24 / 30    | 2       |
| supplementary-plane ignorables not gaps                   | 1 / 0 / 0       | 1       |
| edge rules read only section 3 whitespace, not every gap  | 1 / 5 / 2       | 6       |
| underline allowed                                         | 2 / 0 / 1       | 2       |
| link allowed                                              | 94 / 107 / 115  | 8       |
| ignorable marks skipped before the mark test              | 1 / 0 / 0       | 2       |
| 3.0.0 section 2 additions dropped                         | 16 / 12 / 19    | 6       |
| font-blank letters not gaps                               | 0 / 0 / 0       | 2       |
| rule in a cell allowed                                    | 3 / 9 / 3       | 4       |
| rule decided before the parent check                      | 0 / 0 / 0       | 2       |
| `img` not a void element                                  | 34 / 31 / 19    | 9       |

Every break but one is caught by the vectors. The differential catches the frequent ones on all
three seeds; the rarest (the nesting bound, the mark rules, `h5`, U+2800 and the other blank
glyphs, a narrative of gaps alone, the supplementary-plane ignorable code points, the indent
bound and `]]>`) are drawn rarely enough, or masked often enough by an
earlier error in the same generated document, to be missed at some seeds, and the vectors pin
each. The hole check is never decisive on its own: a row with a hole always also covers fewer
slots than the row its row span starts in (a cell takes the first uncovered slot, so the span's
first row covers every slot up to the spanned column), so the unequal-width check already
refuses it. It is kept because the specification states the rule directly. Each break was
restored.

**Blast radius.**

- **Drawn documents are not qualified.** Section 7 is complete for a structured source only (an
  FHIR ePI document Bundle with XHTML narratives; every other source, any Word document
  included, is a drawn document). Nine
  review rounds found open cases in how an extractor of a drawn document must write wrapped lines, soft hyphens, page footnotes and a few
  table and caption splits; several hold for 2.0.0 as well. They are recorded in the design note
  ("Drawn documents: open items"). No drawn-document extractor exists in the repository (the
  synthetic builder derives its page text from the narrative, and the Document AI adapter is a
  recorded spike), and none may support an approval until a later version closes them. Roadmap
  item 3a's canonical-contract change (PR 2) makes the gate accept a real source only through a
  structured-source extractor; until then the interim control is `AGENTS.md`'s synthetic-only
  rule, under which no real drawn document enters. The synthetic fixture declares
  `application/pdf` and carries an approval, so PR 2's gate must tell the synthetic extractor
  apart from a real drawn source. A tenth review found that the structured-source rule itself
  needed a closed definition (the page is exactly the scanner's text of the div with ADR 0005's
  droppable presentation removed; a section the scanner refuses, or that holds a soft hyphen,
  refuses), and that ADR 0005 needed a closed list of the presentation that may be dropped;
  both are fixed, with a property test over every accepted XHTML vector. An eleventh review found
  the scanner and verifier sound on 804 divs from the pinned EMA labels, and the remaining gaps
  in ADR 0005's detailed lists (attributes, CSS that can hide or overprint, raised runs, how
  presentation is removed); ADR 0005 now states the page as the scanner's text of T(div), a
  lexical transform of the div string, with precise requirements on T, and leaves the closed
  lists, settled against the real label, to the importer (PR 3). It also found that the pinned
  Imatinib Teva label cannot be imported whole under the rules as written (six sections refuse
  in the scanner, among them `t` with a lowered `½` and `AUC` with a lowered `(0-∞)`, which
  nearly every label has); ADR 0005 records each refusal and how PR 3 settles it, including a
  possible minor version of this contract for `½` and `∞` in `sub`. A pairwise property test on
  each side checks that a narrative verifies against another's structured page only when both
  read the same. A twelfth review found U+1680 OGHAM SPACE MARK, drawn as a stroke,
  normalised as a space since 1.0.0 (section 3 step 5 no longer lists it; two verify vectors and a
  normalisation vector), the `href` grammar unstated since 2.0.0 (section 5 now gives the whole
  regular expression; nine boundary vectors), and gaps in ADR 0005's requirements: links and
  bare spans unwrapped as a fourth operation of T, raised offsets summed per glyph, "draws
  nothing" defined and every uncovered page required to be blank, picture sizes bounded, a
  picture deleted only on pinned evidence that the authority's viewer draws nothing, and a
  structured picture's token taken from `src` as T(div) holds it. A thirteenth review swept the
  older rules against Chrome and found four false passes that predate 3.0.0: markup nested past
  512 open elements, which an HTML parser rearranges (a bound of 32, `nesting-depth`, with
  `small` inside `small`, a heading inside a heading and more than six indenting containers);
  U+200A drawn about a pixel wide, and U+2006, U+2009 and U+202F barely wider (now content); a
  combining mark after an inline tag, which NFC joins but a renderer draws apart
  (`combining-across-markup`); and a soft hyphen or zero-width space inside a number, which a
  narrow viewer breaks at (narrative now rejects both, `invisible-character`, which withdraws
  `soft-hyphen-at-boundary`). It also found `]]>` in text accepted though an XML renderer then
  draws nothing (`cdata`). A fourteenth review found the thirteenth's fixes incomplete: a
  combining mark after a tag that NFC leaves apart (U+0301 after "q") still drawn apart from its
  letter (now any category M code point first after an inline tag rejects); U+205F, a
  medium mathematical space, drawn as narrow as the thin spaces (now content); `code`, `h5` and
  `h6` shrinking text like `small` (at most one of the four open); and, from making thin spaces
  content, the digit-group and quote-edge rules no longer seeing "10" U+2009 " 000" as one
  number (both now read past every gap: whitespace, thin spaces and Default_Ignorable code
  points). A fifteenth review found a word joiner or zero-width joiner between the tag and the
  mark still getting past the mark rule (the rule now reads past ignorable code points); an
  underline turning a sign into another, present since 1.0.0 (`CrCl <u>&lt;</u> 30` drawn
  "CrCl ≤ 30"; `underlined-sign`); U+2800 BRAILLE PATTERN BLANK drawn as a gap but not read as
  one (now a gap); narrative and quotes of gaps alone accepted (now `empty-narrative` and
  `invalid-request`, and the crosswalk's test for a mandatory section follows); the agent's test
  double still treating U+205F as whitespace; stated rules no vector pinned; and the query
  service's design document not describing the gap reading. A sixteenth review found the
  underline rule, a closed list of categories, incomplete (U+02C2, a modifier letter, is drawn
  exactly "≤" when underlined, and `1<u>a</u>` "1ª"), so `u` and `a` are refused outright and
  `underlined-sign` is withdrawn; it swept every assigned code point in Chrome for blank glyphs
  (seven Mongolian and Yi letters are now gaps, and the interlinear annotation controls are
  refused in section 2, with the prepended concatenation marks, which draw across the digits
  after them); and it found no vector for an ignorable code point that is itself a mark. A
  seventeenth review found a number or a sign split across two table cells drawn side by side
  ("10" | "000 IU" reads "10 000 IU") while the quote-edge rule matched either half (it now
  rebuilds the grid and reads the neighbouring cell, in the service and the agent), and an `hr`
  in a cell drawn as a fraction bar (`<td>1<hr/>2</td>` drawn "½"; now `table-content`); it
  also found ADR 0005's underline requirement blind to CSS underlines, the ePI reader reading an
  underlined "<" as "<" (both readers now mark underlines), stated lists with entries no test
  pinned (each list is now enumerated in a test on every side), and `find_product` made
  unavailable by one stored name the normalisation refuses (that name now matches nothing). An
  eighteenth review found that a cell's lines are centred, so any line of a cell can sit level
  with a line of the next one ("Up to 10" | "once" / "000 IU" / "weekly" draws "Up to 10 000
  IU"; the quote-edge rule now weighs every word of every cell on the quote's side, the grid
  read once per search, since per occurrence it was quadratic); the sign rule stopping at a gap
  or an opening bracket ("CrCl <" U+2063 " 30"; it now reads past both); no vector pinning the
  `hr` rule's place after the parent check; the QRD check ignoring underline marks and the ePI
  reader missing a border on inline text (the check now reports an underline over what it can
  change, the reader marks the border, and both readers' versions moved); and two stale rows in
  the changed-vectors table. A nineteenth review found the sign rule stopping at an opening
  bracket set off by spaces ("CrCl < ( 30 ml/min )", "ClCr ≥ « 30 ml/min »"; it now reads past
  openers and gaps together) and missing look-alike and negated comparators (U+02C2, "≮",
  fullwidth "＜"; now in the list, named in the query design); the query design understating
  what the table rule refuses (now measured: 185 of 789 whole-cell quotes on the pinned
  SmPCs, stated in UR-22); three branches of the rule no example pinned; the underline rule
  missing Cyrillic and Greek look-alikes of an ordinal "a" or "o", a digit behind an invisible
  code point and "Nº"; and, as lows, side borders marked as underlines (now `border`), the
  checker's version (now 1.1.0) and a missing changed-vectors row. A twentieth review found the
  closed sign list still short (a sign with a combining mark, "<=", "×", "+/-", "‹", "≪") and the
  number rule reading decimal digits only ("1 ½"): a sign is now any mathematical symbol or a
  named look-alike, read past marks with the whole run of symbols before the space, a number
  any code point of category N, and the sign is found in one pass over the text (a long run of
  brackets and spaces was quadratic); the ePI reader now expands multi-value border shorthands
  per side as a browser cascades them, and the check reports a border over any visible text;
  `underline_changes` refuses any underlined lower-case letter after a number of any script
  and the "N" look-alikes; the table figure is now 192 of 789 and its script is committed. A
  twenty-first review found the sign list still short (look-alikes from other blocks, "❮", "⧼",
  "➕", a middle dot, a slash) and the opening marks the code skipped narrower than the design
  said: the rule is now inverted, so anything but a letter, a number, a gap, a mark, an opening
  mark, a dash or plain punctuation is a sign, with reference marks plain and postfix signs
  ("%", "°") read after a number only, and a sign after a number binds it too ("100" of "100 ×
  10⁹/l"); the agent's double read a long cell word with the splitter's bounded walk (now
  exact); the ePI reader applied a later declaration over an `!important` one and read an
  unparseable border value as none (now cascaded by importance, and refused); the check
  skipped faint or struck signs (now reported); and two sub-rules had no test. The table figure
  is 196 of 789, 51 beginning with a letter.

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
- **Narratives with a soft hyphen or a zero-width space** (from Word, for example) are now
  `malformed-narrative` (`invisible-character`); a producer must drop them from the narrative,
  and the page text of a drawn document keeps its line-end soft hyphens as before.
- **Narratives.** A narrative with an `li` outside a direct `ol` or `ul` parent, anything but
  `li` directly in a `ul`, a table inside a table, a row drawn at zero height, a column drawn at
  zero width, U+FFFC or U+FDD0–U+FDEF is now
  `malformed-narrative`. Every narrative with a table has new normalised text, and so a new
  `normalizedTextSha256` and binding hash.
- **Not closed**, stated in the specification and the design: what a picture shows (a text
  check cannot read it); a viewer that restyles lists or draws no numbers; `td` versus `th`, `scope` and row
  groups, which are presentation; the line a value sits on inside a multi-line cell, and how
  high or wide a row or column is drawn; and the residuals 2.0.0 stated other than cell
  association.

**Step 7.** Previously approved submissions require re-approval: the version is part of the
approved content hash.

**Approval (step 8).** Pending. This change touches an approved hash, so it needs a quality
representative other than the author before merge; the approval is recorded in the pull request.
Branch protection does not yet require a second reviewer, so until it does this is a procedural
control only.
