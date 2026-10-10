# Recorded change: `fidelity-norm/3.7.0` → `fidelity-norm/3.8.0`, 2026-10-10

_Follows the procedure (steps 0–8) in `docs/validation/README.md`, "Change control for shared,
evidenced libraries". The UR- rows it cites are in that file._

**What changed.** `NORMALIZATION_VERSION` moved from `fidelity-norm/3.7.0` to
`fidelity-norm/3.8.0` in `src/fidelity/normalize.ts`, `zone-a/src/zone_a/fidelity/normalize.py`
and the agent's `quote_edge.NORMALIZATION_VERSION`. In `docs/fidelity-normalization.md`, only
section 7's certified Word rule changes (`zone_a.word_epi`, `word-epi/1.8.0`); sections 1 to 6
and the code point table do not.

**Lists at two levels, as their labels' text** (ADR 0006 owner decision 14, amending decision 6;
taken as recommended under the owner's authorisation of 2026-10-10, for the owner's review; the
design note `docs/design/nested-lists.md`, recommendation R2). A section whose list labels stand
at more than one Word level was refused whole (`list-level`). It is now carried where:

- every label belongs to a closed family, judged as a reader sees a label (`word_epi.FAMILIES`,
  `word_epi.family`): disc (U+2022, U+25CF); circle (the letter "o", Word's default second-level
  bullet, U+25E6, U+25CB); square (U+25AA, U+25A0, U+25AB, U+25A1); dash (U+002D, U+2010 to
  U+2014, U+2212); decimal (ASCII digits, alone, with "." or ")" after them, or in brackets);
  lower-case ASCII letters, alphabetic and Roman together, with "." or ")" after them or in
  brackets; upper-case likewise;
- no family stands at two levels of the section;
- no level holds two families. This last condition is the implementation's, stricter than the
  note's rule: with "•" at level 0 and both "o" and "-" at level 1, a reader taking the label as
  the level would read three levels. On the corpus it costs nothing (one section holds such a
  level, and it is refused for another cause too; measured by running both rules on the 27 files
  with a `list-level` blocker: the same 804 sections carried and 5 files whole).

Each run of list paragraphs (no unlabelled paragraph between them) that holds two or more levels
is then written whole in decision 6's form, each item a `p` of its label, U+0020 and its text, its
"•" items too and across a change of list inside the run (`word_epi._lists`, `_tag`), so no item
is drawn as an HTML list's. A run of one level is written as before (a `ul` of "•", an `ol` of
"1.", "2.", ... from one, else labels as text). The page does not change: it already writes every
label so. The narrative (`_flow`) and the drawing check's heads (`text_labels`) go through the
same `_lists` and `_tag`, and one function (`word_epi.list_levels`) is the refusal for the
narrative, the page and the scoreboard's blocker mirror (`zone-a/scripts/scoreboard.py`).

Still refused (`list-level`): one family at two levels ("•" over "•", in one list or two;
"●" over "•", "−" over "-", "o" over "◦", "▪" over "■", "1." over "1)", "1." over "1.", "i." over
"a.", "A." over "I."), in one run or in two lists apart in the section; a label of no family
("➢", "►", "a" alone, "1.1.", "*"); two families at one level. Decision 6's guards hold: a dash
label before an item that begins with a number (`list-label`, `joins`), and a bullet glyph written
as text in a table cell (`list-label`). A run that starts deeper than it goes, or jumps a level,
is carried: the label carries the level. A level whose label is empty draws nothing and is no list
paragraph, as before. Lost, as no check compares them: the indentation, and a wrapped item's
hanging indent (as with decision 6).

Why not nested HTML lists: no nested run in the corpus uses the markers an HTML list draws at
depth (◦, ■), so none would be drawn as Word draws it (the design note, section 2); the failing
verify vector below holds that a nested `ul` loses Word's labels.

**Why.** Evidence, on EMA's published English Word PI cuts (internal corpus,
`label-docx-reader-scratch/ema-pi-tc`, counts only), measured with `zone-a/scripts/scoreboard.py
coverage` (`docx-reader/1.34.0`, 296 SmPC and 286 leaflet cuts; before on main eafd7e2a, after on
this branch) and `regress` between the two (exit 0: no file worse, no section carried before now
refused, changed or gone). By file:

| SmPC (296)               | before | after |
| ------------------------ | -----: | ----: |
| whole                    |     21 |    22 |
| built, a section refused |     80 |    79 |
| needs a person           |     33 |    33 |
| document refused         |     11 |    11 |
| parts unclear            |      8 |     8 |
| reader refused           |    143 |   143 |

| Leaflets (286)           | before | after |
| ------------------------ | -----: | ----: |
| whole                    |     20 |    24 |
| built, a section refused |     49 |    45 |
| needs a person           |    125 |   125 |
| document refused         |      1 |     1 |
| reader refused           |     91 |    91 |

By section: of 4 098 SmPC sections built, 3 876 were carried before and 3 878 after (`list-level`
as the first refusal 6 → 4); of 1 666 leaflet sections built, 1 558 before and 1 570 after (25 →
13). Converted characters: SmPC 4 595 259 → 4 607 086 (+11 827), leaflets 1 526 728 → 1 565 506
(+38 778), 15.76% → 15.89% of the corpus. Headless Chrome (`zone_a.drawing.check`) drew all 14
newly carried sections as read (2 SmPC, 12 leaflet; 16 and 113 paragraphs written as labels'
text). The 17 sections still `list-level` use one family at two levels; the source's remedy is a
distinct bullet per level.

**Impact assessment (step 0).** Importers of `src/fidelity/`:

- **Worker gate** (`src/pipeline.ts`, `src/contracts/canonical-submission.ts`): refuses a
  submission or report that names another version. A 3.7.0 submission is refused, not
  re-evaluated.
- **Authority importer** (`src/authority/`): unchanged in what it does; 2.4.7, its vectors moving
  only in the hashes that embed the version, every outcome unchanged.
- **Certified Word importer** (`src/certified-word/`): unchanged in what it does; 1.3.7, its
  vectors moving only in hashes (the recompute results name the new Zone A versions, the
  submissions the new normalisation version), every outcome unchanged.
- **Query service** (`src/query/tools.ts`): `get_section` and `verify_quote` report the new
  version. Every stored narrative reads to the same text: sections 1 to 6 do not change.
- **Agent** (`agent/`): its post-check accepts answers under exactly one version, so its constant
  moves in this change. Either deploy order leaves a window in which the agent and the query
  service differ and every block is flagged `checksum-mismatch` and shown unverified, never wrong.
  The agent's redeploy from the merged commit waits on the owner's hold on redeploys (its README).
- **Zone A** (`zone-a/`): the port's constant moves; `word-epi/1.8.0` (a minor: what it carries
  changes), and the components locked to the files that changed move a patch version:
  `word-drawing/1.2.7` (no logic change: its docstring, and what it compares as text labels
  grows through `text_labels`), registry 1.3.5, `qrd-check/1.6.5`, `smpc-structure/1.3.5`,
  `pl-structure/1.0.5` and `product/1.0.14` (`zone-a/versions.lock.json`). The registries, the
  five QRD check results and the certified Word recompute fixtures move in those version strings
  (and the registry's hash) only, byte for byte otherwise; Chrome's recording of the five Word
  SmPCs and the QRD template (`zone-a/tests/fixtures/word-smpc/chrome.json`) was made again and is
  byte for byte the same (none of them has a list at two levels).
- **The Word drawing** (`src/render/word-drawing/lock.json`): its version moves to
  `word-drawing/1.2.7`, the code's; dev's image digest is unchanged, since the image holds no Zone
  A code. The record dev's build signed for `word-epi/1.7.0` is at another path than this build's
  request and drawing id give, so a dry run answers `recomputed` until dev draws the synthetic
  SmPC again after the deploy (`docs/design/certified-word-drawing.md`).
  `test/certified-word/drawing.test.ts` holds the committed real record to the build it was drawn
  by, as before (its test key's comment names this build).

**Steps 1–6.** 1: `NORMALIZATION_VERSION` is `fidelity-norm/3.8.0` everywhere it is held. 2: in
`zone-a/`, `scripts/lock_versions.py`, `scripts/generate_qrd_registry.py`,
`scripts/check_labels.py`, `scripts/certified_word_fixtures.py` and `scripts/word_fixtures.py
record`; then `npm run contracts:generate`, `npm run vectors:generate`, `npm run
contracts:fixtures`, `npm run contracts:quote-edge`, `npm run differential:smoke`, `npm run
authority:vectors` and `npm run certified-word:vectors` regenerated
`test/fixtures/fidelity/vectors.json` (693 → 695: verify 193 → 195; normalisation 71 and XHTML
429 unchanged), the contract fixtures, the quote-edge export, the smoke corpus and both
importers' vectors; `npm run authority:lock` and `npm run certified-word:lock` added 2.4.7 and
1.3.7. 3: below. 4: two verify vectors, reviewed by hand (`test/fixtures/fidelity/cases.ts`):

- `certified-word-two-levels-as-labels`: a certified Word page with a list at three levels
  (bullets at level 0, "o" items at level 1, an en dash item at level 2), against the narrative
  built from the same read, each item a paragraph of its label and text (passed);
- `certified-word-two-levels-as-nested-list`: the same page against the run drawn as a nested
  HTML list, which draws a browser's markers, not Word's "o" and dash (failed: the labels are
  text).

`zone-a/tests/test_word_epi.py` builds both vectors' page and the passing one's narrative from the
same read by `zone_a.word_epi`, and pins:

- the closed family list, member by member, and its near misses (54 labels in all; among the misses "a", "O", "oo",
  "• " with a space, U+2015, U+00B7, U+F0B7, "➢", "►", "*", "1.1", "1.1.", "(1", "1))", "1:",
  "a.b", "Aa.", Arabic-Indic and fullwidth digits, "é.", U+2160);
- runs carried as labels' text (11 shapes: distinct families per level, three levels, starting
  deeper, a jump of two levels, numbered over lettered with numbering run on across parents,
  Roman under bullets), one block across a change of list, the drawing check's heads naming
  every item;
- a run of one level in a section of two written as before (`ul`, `ol`);
- refusals (23 shapes): each same-family pair of the note's risk list, in one list, two lists
  and two lists apart; labels of no family; two families at one level;
- a section of one level keeps any label, as before; a level whose label is empty is no list
  paragraph;
- decision 6's guards in a run at two levels (a dash before a number; a "•" in a cell), and a
  run at two levels in a cell of no bullet glyph carried;
- Chrome (`zone_a.drawing.check`, headless) drawing three sections of such runs, in the body and
  a table cell, as read, and a read with one sub-item's label changed differing;
- the scoreboard's blocker mirror (`zone-a/tests/test_scoreboard.py`) giving the builder's
  refusal for each case, in the known detail forms.

5: ADR 0006 gains owner decision 14 (with the family table and the implementation's one-family-
per-level addition), and decisions 3 and 6 note the amendment; ADR 0002 and ADR 0003 amended for
the withheld note (below). 6: UR-09 and `AGENTS.md` name 3.8.0 (`AGENTS.md` also says a certified
Word source's list nesting is kept as its labels' text).

The withheld section of an authority import (`docs/design/authority-import-withheld.md`,
decided, not built), which had taken 3.8.0, moves to 3.9.0; ADR 0002, ADR 0003 and that note say
so. The authority importer moves to 2.4.7 and the certified Word importer to 1.3.7
(`IMPORTER_VERSION`, their `importer.lock.json`), because their golden vectors carry the version.

**Changed vectors (step 3).** Compared by name with the 3.7.0 vectors: no normalisation or XHTML
vector changed and none was removed; the two above were added. Every other verify vector changed
in exactly three fields and no other: `input.normalizationVersion`,
`expected.normalizationVersion` and `expected.reportHash`.

**Differential proof.** Sections 2 to 6 do not change, so neither scanner changes; every one of
the 429 XHTML vectors is read alike by the TypeScript and the Python
(`zone-a/tests/test_golden_vectors.py`), and the smoke corpus is regenerated, moving only in
versions and report hashes. The builder's two outputs are held to each other by section 5's
scanner on every section built (`page-differs`), and by the seeded run of random bodies in
`zone-a/tests/test_word_epi.py`.

**Blast radius.** Every approved hash that embeds the version moves (step 7). No accepted text
changes: a section 3.7.0 carried is carried alike (`regress`: none changed); sections 3.7.0
refused only for lists at two levels are now carried where their labels tell the levels. The
five Word SmPC fixtures and the QRD template build as before.

**Step 7.** Previously approved submissions require re-approval: the version is part of the
approved content hash. The demonstration's approvals are synthetic placeholders and are
re-seeded.

**Approval (step 8).** Not obtained: author and releaser are the same identity. This change
touches an approved hash, so it needs a quality representative other than the author. Owner
decision 14 was taken as recommended under the owner's authorisation of 2026-10-10 and awaits the
owner's review, the family list and the one-family-per-level addition with it.
