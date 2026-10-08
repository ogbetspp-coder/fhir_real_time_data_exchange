# Recorded change: `fidelity-norm/3.4.0` → `fidelity-norm/3.5.0`, 2026-10-08

_Follows the procedure (steps 0–8) in `docs/validation/README.md`, "Change control for shared,
evidenced libraries". The UR- rows it cites are in that file._

**What changed.** `NORMALIZATION_VERSION` moved from `fidelity-norm/3.4.0` to
`fidelity-norm/3.5.0` in `src/fidelity/normalize.ts`, `zone-a/src/zone_a/fidelity/normalize.py`
and the agent's `quote_edge.NORMALIZATION_VERSION`. In `docs/fidelity-normalization.md`, only
**section 7's certified Word rule** changes (`zone_a.word_epi`, `word-epi/1.5.0`), in five rules
and a sixth that rests on Word's own drawing (below). Each tab rule is one function the narrative
and the page share (`spaced`), so the two cannot diverge; the grid is laid by each by its own code,
so that the fidelity check still compares two
readings of it:

1. **A grid column at which no cell starts is dropped** (the builder's `_rows`, and the page's own
   `_slots`, written apart and held to each other on 3000 random grids, and the narrative's
   table, placed by the HTML table model, held to the model computed directly from Word's grid):
   in a table as the builder
   sees it, a grid column k (0 < k < width) at which no
   cell of any row starts, an empty cell of rule 2 included, is dropped, and each cell's
   `colspan` counts only the columns kept. Every cell over such a column also covers the one
   before it, so section 5's HTML table model draws the column at no width (Word draws it inside
   the cells that span it); section 5 rejects such a
   narrative (`table-shape`, its rule that "in every column 0 … w−1 a cell spanning one column
   must start"), and the table without it is the same table, each value in the same cell, every
   merge kept. A fix of the builder's grid, not an owner decision: the same table is drawn. A
   column that some cell starts at but none of one column spans is a real boundary, kept, and
   still rejected by section 5 (`narrative`, `table-shape`).
2. **ADR 0006, owner decision 10** (taken as recommended under the owner's authorisation of
   2026-10-08, night; for the owner's review): the grid columns a row leaves out at its start
   (`gridBefore`) or end (`gridAfter`) are an empty cell over them, a `td` with their `colspan`,
   one row high and with no content, which the page writes as the same cell (U+0009 U+FDD3
   U+0009 and no text, then U+0009 U+FDD4 U+0009 for each further column). Word draws no cell
   there and no text: the ePI holds the same text in the same columns, and only an empty cell's
   box may be drawn where Word draws none. Both ends at once are carried. Still `table-shape`: a
   vertical merge that would run through an empty cell or continue under one (an empty cell
   starts no merge, so the cell below is under no cell of its columns), and a row of no cells
   of its own that leaves columns out (until 3.5.0 every row that left columns out was refused).
3. **ADR 0006, owner decision 11** (the same authorisation; for the owner's review): in a
   paragraph that draws no list label, in a table cell or outside one, whose text past its indent
   holds exactly one U+0009 and no typed label's tab, that U+0009 is written as U+0020 (`spaced`),
   unless
   - as a space it would join a number to what stands before it (`joins`, item 5: one test for
     every tab written as a space and every label written as text);
   - it is inside a superscript or subscript mark (decision 9's raised key keeps precedence);
   - the text holds a code point of bidi class R, AL or AN or an explicit embedding, override or
     isolate. This is a narrowing of the recommendation, for exactness: a tab is a segment
     separator, drawn at the paragraph's level, and a space is not, so a space between two
     right-to-left words, or a right-to-left word and a number, joins them in one right-to-left
     run, drawn in the other order (two Hebrew words with a tab between them are drawn in their
     order, each from the right; with a space, the second is drawn first). The label reader
     refuses the bidi controls already; R, AL and AN letters and digits are the case.

   Two or more U+0009 past the indent stay refused (`tab`): a column layout. Decisions 1, 7 and 9
   keep precedence: a typed label's tab is a space where this rule would refuse it as raised (a
   raised key with its tab raised). The guard of item 5 holds both.

   **A gap, stated, and its follow-up.** The label reader reads `w:tab` and `w:ptab` as U+0009
   (`label_docx.reader`) and reports neither tab stops nor leaders, so a tab whose stop draws a
   dot or an underscore leader, or a `w:ptab` whose own `w:leader` attribute draws one, would be
   written as a space and its leader dropped; this holds for decisions 1, 7 and 9's typed
   labels, already live, too. On the EMA's English cuts no paragraph holding a tab (0 of 32 048)
   has a tab stop with a leader set directly or by its paragraph's own style (the coordinator's
   count, which did not cover a numbering level's tabs, the document defaults or a style's
   `basedOn` chain). `docx-reader/1.34.0` is to refuse a leader of any of these on any paragraph
   that holds a tab; no code changes for it in 3.5.0.

4. **ADR 0006, owner decision 12** (the same authorisation; for the owner's review): the U+0009
   before a paragraph's text starts (in the section 3 step 5 whitespace before its first other
   code point, U+000A ending it) are its indent, each written as U+0020, and the tabs after it are
   judged by decisions 7, 9 and 11 as if it were absent ("tab ● tab text" carries, "tab a tab b
   tab c" stays refused). In a paragraph that draws a list label every tab stays refused, an
   indent's included (a narrowing: the recommendation named no list label, and there the gap is
   Word's between the label and the text). Section 3 step 5 removes leading U+0020 from both
   texts, and the narrative and the page write the same spaces; a browser draws leading spaces
   in a `p` as nothing, and so does the drawing check.
5. **The guard: no space that joins a number to what stands before it** (`joins`, one function
   for every tab written as a space, an indent's, a typed label's or a lone one, in `spaced`, and
   for every label decision 6 writes as text before its item, in `_flow`). Refused where the text
   after the tab or the label begins with a number and the last code point before it, read
   backwards past every section 6 gap (`is_gap`: section 3 step 5 whitespace, U+00A0 and U+2007
   included, the thin spaces, the blank glyphs and the default ignorables; a line break is one,
   which a cell writes as a space, so the guard reads across U+000A: "5", a line break, a tab,
   "2" is refused, which is safe) and to its base past combining marks (general category M:
   "-" or "5" with U+0301 or U+0332 counts as itself), is numeric, or is a dash or minus (general
   category Pd, or U+2212, U+207B, U+208B, U+FE63, U+FF0D, U+2796, U+2043 HYPHEN BULLET, U+02D7
   MODIFIER LETTER MINUS SIGN; the reader maps a Symbol font's minus to U+2212). Numeric is
   `unicodedata.numeric(c, None)` not None: a digit of any script, a raised
   one as the read holds it, a script digit such as "¹", a vulgar fraction such as "½", a circled
   digit, a Roman numeral, and a CJK numeral, which counts too (it refuses more). A text begins
   with a number where its first code point past gaps is numeric, or a run of U+002E, U+002C,
   U+00B7, U+066B, U+FF0C, U+FF0E, U+FE50, U+FE52, U+00B1, U+002B and any dash or minus (as
   above) stands before one ("1", a tab, ",5 mg"; "1", a tab, "+.5"; "5", a tab, "–20 °C", an en
   dash as the EMA writes a minus, which as a space would read as the range 5–20 °C, as would "1",
   a tab, "–2", "5", a tab, "－2" or "➖ 2", "Day 1", a tab, "—2", and the label "1" before "–2");
   not a bracket or a comparison sign ("(",
   U+2265, "<", "~"), after which a number reads as no sign's. As a space the two would read as
   one number ("1 000"; "Day 1 ½" as "1½"), or the dash as the number's sign ("–", a tab, "2 to 8
   °C", a storage temperature, as −2 °C). These fixes narrow decisions 6, 7 and 9 (3.3.0 and
   3.4.0) with no new decision:
   - decision 9's typed labels: "Table 1", a tab, "2-year results" was written "Table 1 2-year
     results" under 3.4.0, and a raised "1", a tab, "2 mg" "¹ 2 mg"; both keep their tab, refused
     (`tab`); "Table 1:", a tab, "2-year" (the colon separates), "1.", a tab, "2 mg", "Table 1a",
     a tab, "2-year", and a raised "a", a tab, "2" carry as before;
   - decision 7's typed dashes and every other dash or minus before a tab: "–", a tab, "2 to 8
     °C"; "- ", a tab, "2"; "– –", a tab, "2"; "Store at -", a tab, "2 to 8 °C"; U+2212 (a Symbol
     font's dash), U+2010, U+2012, U+2015, U+FE58, U+FE63, U+FF0D, U+207B, U+2796; and
     "Storage:", a line break, "-", a tab, "2 to 8 °C", which 3.4.0 refused and the lone tab rule
     would otherwise have carried. "-", a tab, "see", "–", a tab, "Tablets" and "Store at -", a
     tab, "(2 to 8 °C)" carry;
   - decision 6's labels written as text (`list-label`): a dash or minus label ("–", U+2212, the
     label "- ") before "2 to 8 °C" or "2 tablets", and a label ending in a number before a
     number, which joined two numbers since 3.3.0 (the label "1" before "000 mg" as "1 000 mg",
     "1.1" before "5 mg" as "1.1 5 mg"). "–" before "Tablets", "a)" before "5 mg", "(1)" before
     "2 mg" and "3." before "5 mg" carry; a bullet or numbered list HTML draws writes no label as
     text and is unchanged.

And one rule that rests on Word's own drawing, as 3.4.0's C0C0C0 grey did: **a strike, a
highlight or a solid shading Word paints over nothing is left out** (`unpainted`): a reader mark
`strike`, a highlight of one of Word's sixteen colours (`HIGHLIGHTS`: `highlight-black` to
`highlight-lightGray`, ST_HighlightColor but `none`, each a solid paint), or a solid shading
spelled `shading-` and six hex digits (`SOLID_SHADING`, a clear pattern's fill as the reader
spells it), whose text, from its start to the paragraph's end, is only U+0020, a paragraph or a
table cell of only such spaces included; in the narrative, on the page
and in a heading. Word's own print of two synthetic probes (Word for Mac, printed to PDF on
2026-10-08, each line's ink counted at 288 dpi; the probes and Word's prints are committed as
`zone-a/tests/fixtures/word-oracle/claude-strike-probe.{docx,pdf}` and
`claude-hl-probe.{docx,pdf}`):

| probe paragraph ("Alpha beta" and ten spaces)   | ink  | right edge | Word draws        |
| ----------------------------------------------- | ---- | ---------- | ----------------- |
| no mark (the baseline)                          | 2501 | 126.25 pt  | the text          |
| ten U+0020 struck at the end                    | 2501 | 126.25 pt  | the text, no line |
| ten U+0020 struck between "Alpha" and "beta"    | 2695 | 148.25 pt  | a line in the gap |
| ten U+00A0 struck at the end                    | 2695 | 151.75 pt  | a line            |
| ten U+0020 highlighted yellow at the end        |      |            | the text only     |
| ten U+0020 highlighted yellow between the words | 5626 |            | yellow            |
| a paragraph of ten U+0020 highlighted yellow    |      |            | nothing           |
| a table cell of ten U+0020 highlighted yellow   |      |            | nothing           |
| a table cell of ten U+0020 shaded 000000        |      |            | nothing           |
| ten U+0020 shaded 000000 at the end             |      |            | the text only     |

(For the highlight probe the yellow pixels were counted, 5626 where it drew, none elsewhere.)
Every other strike, highlight or shading over whitespace is still refused (`formatting`): one
between words, one over U+00A0, one followed by a U+00A0, a tab, or a line break and text, and a
double strike (`dstrike`), a pattern's shading (`shading-pct15-...`, `shading-solid-...`, any
`shading-<pattern>-...`) and a theme's (`shading-THEME-...`) over trailing spaces, none of which
the print showed, and a highlight the reader spells with another name. The template's greys are
carried as before, as the silver span. **Follow-up:** probe Word for a pattern's and a theme's
shading over trailing spaces, then widen `unpainted` by what it draws. The reader spells a
`nil` pattern's fill as a clear one's (`shading-RRGGBB`), and Word was printed with `clear`
only.

The withheld section of an authority import (`docs/design/authority-import-withheld.md`,
decided, not built), which had taken 3.5.0, moves to 3.6.0; ADR 0002, ADR 0003 and that note say
so. The authority importer moves to 2.4.4 and the certified Word importer to 1.3.2
(`IMPORTER_VERSION`, their `importer.lock.json`), because their golden vectors carry the version.

**Why.** Evidence, on EMA's published English Word PI cuts (internal corpus, counts only, from
the coordinator's scan): 13 narratives refused `table-shape` in SmPC built files, such as rows of
a cell of two columns and one of one, then one of three, where column 1 starts no cell (how many
of the 13 each rule carries is not measured by this change's tests); rows that leave grid
columns out stand in 8 SmPC and 12 leaflet files (leaflet rows leave them out at the start 106
times, SmPC rows at the end 155 times); with decision 11 the SmPC files with tab blockers fall from
32 to 11, and those left all have several tabs;
an indent's tab ("tab:leading") stands in 6 leaflet files, and several tabs with "●" in 7; in SmPC
built files a strike over whitespace stands 10 times at a paragraph's end and 5 between words, a
yellow highlight over whitespace 4 times in cells and 3 in the body (leaflets: 4 in cells), and
black shading over whitespace in cells 6 times (leaflets: 7).

**Impact assessment (step 0).** Importers of `src/fidelity/`:

- **Worker gate** (`src/pipeline.ts`, `src/contracts/canonical-submission.ts`): refuses a
  submission or report that names another version. A 3.4.0 submission is refused, not
  re-evaluated.
- **Authority importer** (`src/authority/`): unchanged in what it does; 2.4.4, its vectors moving
  only in the hashes that embed the version, every outcome unchanged.
- **Certified Word importer** (`src/certified-word/`): unchanged in what it does; 1.3.2, its
  vectors moving only in hashes (the recompute results name the new Zone A versions, the
  submissions the new normalisation version), every outcome unchanged.
- **Query service** (`src/query/tools.ts`): `get_section` and `verify_quote` report the new
  version. Every stored narrative normalises to the same text: sections 1 to 6 do not change.
- **Agent** (`agent/`): its post-check accepts answers under exactly one version, so its constant
  moves in this change. Either deploy order leaves a window in which the agent and the query
  service differ and every block is flagged `checksum-mismatch` and shown unverified, never wrong.
  The query service redeploys on the merge (`deploy.yml`), so the agent on Agent Engine is
  redeployed from the merged commit right after it, as for 3.3.0 (#188) and 3.4.0 (#209; its
  README).
- **Zone A** (`zone-a/`): the port's constant moves; `word-epi/1.5.0` carries the rules. The
  components locked to the files that changed move a patch version: `word-drawing/1.2.2` (it
  reads `word_epi.py`; `drawing.py`'s rules do not change: an empty cell is an empty line, which
  it drops, it collapses a tab and a space alike, and it compares marks only on characters that
  are not whitespace, so a mark over trailing spaces is no mark there, held by a test that draws
  each rule in Chrome), registry 1.3.2, `qrd-check/1.6.2`, `smpc-structure/1.3.2`,
  `pl-structure/1.0.2` and `product/1.0.11` (`zone-a/versions.lock.json`). The registries, the
  five QRD check results and the certified Word recompute fixtures move in those version strings
  (and the registry's hash) only, byte for byte otherwise.
- **The Word drawing** (`src/render/word-drawing/lock.json`): its version moves to
  `word-drawing/1.2.2`, the code's; dev's image digest is unchanged, since the image holds no Zone
  A code (the drawing build mounts `zone-a/src` and `label-docx-reader/src` from main's checkout).
  The record dev's build signed for `word-epi/1.4.0` is at another path than this build's request
  and drawing id give, so a dry run answers `recomputed` until dev draws the synthetic SmPC again
  after the deploy. `test/certified-word/drawing.test.ts` holds the committed real record to the
  build it was drawn by, as before, and `test/certified-word/recompute.test.ts` holds the gate's
  step 5 to a record made as dev's build makes one for this build, signed by a key made for the
  test.

**Steps 1–6.** 1: `NORMALIZATION_VERSION` is `fidelity-norm/3.5.0` everywhere it is held. 2:
`npm run vectors:generate`, `npm run contracts:fixtures`, `npm run contracts:quote-edge`,
`npm run differential:smoke`, `npm run authority:vectors` and `npm run certified-word:vectors`
regenerated `test/fixtures/fidelity/vectors.json` (652 → 654: verify 187 → 189; normalisation 71
and XHTML 394 unchanged), the contract fixtures, the quote-edge export, the smoke corpus and both
importers' vectors; `npm run authority:lock` and `npm run certified-word:lock` added 2.4.4 and
1.3.2; in `zone-a/`, `scripts/lock_versions.py`, `scripts/generate_qrd_registry.py`,
`scripts/check_labels.py`, `scripts/certified_word_fixtures.py` and `scripts/word_fixtures.py
record` (Chrome's recording, since the QRD template's narratives change: its two sections refused
for a tab, "<2.1 General description>" and "<11. DOSIMETRY>", each a lone tab, now carry). 3:
below. 4: two verify vectors, reviewed by hand (`test/fixtures/fidelity/cases.ts`): a certified
Word page whose grid has a column no cell starts at and rows that leave columns out at their end
and at their start, with a lone tab in a cell, an indent before a caption and a strike over
trailing spaces, against the narrative built from the same read (passed), and the same page
against that narrative with one empty cell before its value instead of after it (failed: the
value is in another column); a Zone A test builds both vectors' page and the passing one's
narrative from the same read by `zone_a.word_epi`. The code point table's test records 3.5.0 with
3.1.0's hash.
`zone-a/tests/test_word_epi.py`: the corpus's grid shape and one with a merge carried, with the
narrative section 5 rejects for it; a staggered grid unchanged (and still refused by section 5);
an empty cell before, after, at both ends and over two columns; an empty cell and a dropped column
together, and an empty cell beside a merged cell; every merge an empty cell would meet, and a row
of no cells, refused; the page's `_slots` and the builder's `_rows` writing the same slots on
3000 random grids (more than 300 with a column dropped), and a `_rows` that drops no column or
moves an empty cell refused (`narrative` or `page-differs`); the narrative's table, placed by
the HTML table model, grouping the slots into the cells of the model computed directly from
Word's grid (each column's owner, merged down, the columns that only repeat the one before
them left out), each cell with its text, on 3000 random grids whose vertical merges are mostly
ones Word writes (1687 carried, 359 of them with a merge; the test requires 1000 and 200);
3.4.0's near misses, still no typed
label (`typed_tab`), now each carried as
a lone tab, in a cell and outside; a lone tab mid-text, after a line break and before "<"; a typed
label's precedence (a raised key with its tab raised); a lone tab or a typed label's between a
number and "½", "¼", "①", "Ⅻ" or "³", on either side, refused, and the same numerals beside a
word carried; a tab before ",5", ".5", "−2", "±1", "+2", "-2", "· 5", "±", a thin space and "1",
and after "Table 1" ",5" and U+066B "5", and before U+FF0C, U+FF0E, U+FE50 or U+FE52 and "5",
"+.5" and "−,5", refused, and before ", then", "- see", "± SD", ".", "+ placebo" carried; a tab
after any dash or minus before a number refused: "Storage:", a line break, "-" before "2 to 8
°C", a line break, "–" before "2", "- " before "2", "– –" before "2", "Store at -" or "Store
at", U+00A0, "–" before "2", U+2212, U+2010, U+2012, U+2015, U+FE63, U+FF0D, U+FE58, U+207B and
U+2796 before "2 to 8 °C", "-" before "+.5", and "5", a line break, a tab, "2"; and carried
before no number: "Storage:", a line break, "-" before "see 6.4", "- ", U+2212 or U+2010 before
a word, "Store at -" before "see below" or "(2 to 8 °C)", and "-" before "≥2", "<2" or "~2"; a
label written as text that joins the number after it refused (`list-label`): U+2212, "- ",
U+2010 or U+2015 before a number, "1" before "000 mg", "1.1" before "5 mg", "12" before "½",
U+2160 before "2", "1" before "–2", U+02D7 before "2", U+2043 before "2 mg"; a tab before an
en dash, U+FF0D, U+2796, an em dash or U+2010 and a number refused ("5" before "–20 °C", "1"
before "–2", "5" before "－2" and "➖ 2", "Day 1" before "—2", "1" before "‐.5"), as are U+2043
and U+02D7 before a tab and "2", and "-" or "5" with U+0301, or "-" with U+0332, before a tab and
"2"; and carried: "-" before a tab and "Tablets", "–" before a tab and "Take 2", "5" before a tab
and "– see 4.4", "a" before a tab and "–2", and the labels U+2212 before "Tablets", "- " before
"see 4.4", "a)" before "5 mg", "(1)" before "2 mg", "3." before "5 mg", "1" before "mg";
a typed dash ("-", U+2011, "–", "—") before "2 to 8 °C", "½", ",5", "−2", after spaces or an
indent, refused, and before "Tablets", "Take 2", ", then" carried; a dash list label before
"2 tablets", "½", "−2 °C" past a space, ",5 mg" or "①" refused (`list-label`), before "Tablets"
or "Take 2" carried, and a bullet or numbered list before "2" unchanged; a lone tab inside a
raised or lowered run, at its start, end or across it, refused, and one beside a run carried
(seven cases, in a cell and outside); a typed label's tab between two digits
refused (`tab`, in a cell and outside: "Table 1" or "Table 12 " before "2-year", "Figure 3" before
U+00A0 and "10", a raised "1" with its tab raised or not, a raised "a1", a raised "¹", "Table 2"
before "²", "Table 1" before "₂", after an indent too), and carried with no digit on one side
("Table 1:", "Table 1.", "Table 1a" before "2-year", "1." before "2 mg", a raised "a" before
"2 mg", "Table 1" before "Age"); an indent
before text, a dash, a typed bullet, a caption and a lone tab, in a cell and outside; refused
(`tab`): two tabs and more, after an indent too, a tab between digits read past U+0020, U+00A0,
U+2007, U+2009 and a line break, a tab before a raised digit, a raised or lowered tab, a tab in
Hebrew or Arabic text or beside a Hebrew letter or an Arabic-Indic digit, and any tab after a
list label, an indent's included; each of `strike`, `highlight-yellow`, `highlight-darkBlue`,
`shading-000000` and `shading-FF00AA` over trailing spaces left out on both outputs, in a
paragraph, a cell, a
paragraph of the spaces alone and a heading, and refused (`formatting`) between words, over
U+00A0, before U+00A0, a tab, or a line break and text, and over a visible code point; refused
over trailing spaces: a double strike, three patterns' and two themes' shading, a fill of five,
seven or not hex digits, and a highlight that is none of Word's sixteen (pinned by a test); the two
probes read by the label reader (`read_body`) and built, each paragraph
as Word drew it; Chrome drawing an empty cell, a dropped column, a lone tab, an indent and the
marks over trailing spaces as the read, and a read that differs as otherwise; and the seeded
random bodies now with tabs, rows that leave columns out and marks over trailing spaces, each
reached more than 20 times among the carried. The QRD template carries 29 sections (27 under
3.4.0). 5: ADR 0002, 0003 and 0006 amended as above (ADR 0006: decisions 10 to 12, the two
rules that are not owner decisions, and amend notes on decisions 1, 7 and 9 and on decision 3's
marks). 6: UR-09 and `AGENTS.md` name 3.5.0.

**Mutation spot-check (not committed).** In a scratch copy of `zone-a/src`, each rule was broken
in turn and `tests/test_word_epi.py` and `tests/test_recompute.py` rerun; each of the 30 mutants
failed a test: the builder keeping every column, or no empty cell before, or after; the page
keeping every column, or no empty cell after; the guard (`joins`) removed for tabs, or for labels
written as text; no number, or no dash or minus, read before; combining marks not read past; the
guard reading only the line it stands in; a dash or minus only of Pd, or only of `MINUS`; U+2043
and U+02D7 left out of `MINUS`; only Nd counted as a number; no sign, separator or dash read
before a number, no dash or minus among them, or only one; the lone tab's raised and bidi
exceptions each removed; several tabs taken as one; no lone tab; the indent kept; the list-label
exception removed; U+00A0 counted as a space Word does not paint; only the covered text judged;
any shading, or any highlight, left out; no mark left out; none in a heading.

**Changed vectors (step 3).** Compared by name with the 3.4.0 vectors: no normalisation or XHTML
vector changed and none was removed; the two above were added. Every other verify vector changed
in exactly three fields and no other: `input.normalizationVersion`,
`expected.normalizationVersion` and `expected.reportHash`.

**Differential proof.** Sections 1 to 6 do not change; the smoke corpus is regenerated, and
TypeScript and Python agree on every vector (`zone-a/tests/test_golden_vectors.py`,
`zone-a/tests/test_differential.py`). The certified Word rule is Zone A's alone; the builder's
seeded random bodies hold its two outputs to each other (`zone-a/tests/test_word_epi.py`).

**Blast radius.** Every approved hash that embeds the version moves (step 7). No accepted text
changes; text the 3.4.0 rule refused is now carried. The five Word SmPC fixtures build as before
(their narratives, and so Chrome's recording of them, are unchanged); the QRD template carries
two more sections.

**Step 7.** Previously approved submissions require re-approval: the version is part of the
approved content hash. The demonstration's approvals are synthetic placeholders and are
re-seeded.

**Approval (step 8).** Not obtained: author and releaser are the same identity. This change
touches an approved hash, so it needs a quality representative other than the author. Owner
decisions 10, 11 and 12 were taken as recommended under the owner's authorisation and are for
the owner's review, with the two narrowings of 11 and 12 above.
