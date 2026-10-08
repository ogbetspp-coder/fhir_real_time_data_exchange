# Recorded change: `fidelity-norm/3.3.0` → `fidelity-norm/3.4.0`, 2026-10-07

_Follows the procedure (steps 0–8) in `docs/validation/README.md`, "Change control for shared,
evidenced libraries". The UR- rows it cites are in that file._

**What changed.** `NORMALIZATION_VERSION` moved from `fidelity-norm/3.3.0` to
`fidelity-norm/3.4.0` in `src/fidelity/normalize.ts`, `zone-a/src/zone_a/fidelity/normalize.py`
and the agent's `quote_edge.NORMALIZATION_VERSION`. In `docs/fidelity-normalization.md`, only
**section 7's certified Word rule** changes (`zone_a.word_epi`, `word-epi/1.4.0`): the tab after a
label typed at the start of a paragraph's text (past section 3 step 5 whitespace, after no list
label) is written as U+0020 in a table cell as well as outside one, and a typed label is also

- U+2011 NON-BREAKING HYPHEN, beside U+002D, U+2013 and U+2014;
- a caption's number: `Table` or `Figure`, U+0020 or U+00A0, one to three ASCII digits,
  optionally one of `a` to `z`, optionally U+002E or U+003A;
- a raised footnote key: one to three code points, each inside a superscript mark and of bidi
  class L, EN, ES, ET, CS or ON (`unicodedata.bidirectional`): a tab separates bidi segments and a
  space does not, so a raised key that is or may join right-to-left text keeps its tab, refused;

then any number of U+0020, as in 3.3.0. Everything else is still refused (`tab`): a bare letter or
number that is not raised, four raised code points, a lead mixing raised and level code points, a
raised key of class R, AL, AN, NSM or BN or a bidi control (an Arabic letter, U+200F),
"Tables 1", "table 1", "Table" with no number, "Table 1234", "Table 1A", "Table 1.2", a second tab,
a leading tab and a tab after a list label. The page and the narrative now find the tab by the same
function (`typed_tab`), where the page had its own copy that held it outside a table; the page
writes the space before it folds a raised or lowered run, so a tab raised with its key is a raised
space, as the narrative already wrote it (under 3.3.0 the page refused that tab as `script`).

The same rule carries as the template's grey (a `span` styled `background-color: silver;`, ADR
0006 decision 5) one more reader mark, `shading-C0C0C0`, which Word draws in exactly the colour of
`highlight-lightGray` (`zone_a.word_epi.GREY`, now exactly `highlight-lightGray`,
`shading-D9D9D9`, `shading-C0C0C0`). It is no new reading: Word's own print of a synthetic probe
(one paragraph per mark over 40 U+00A0, printed to PDF by Word for Mac and counted by colour at 144
dpi, 2026-10-07):

| reader mark                 | Word draws | pixels of that colour | here              |
| --------------------------- | ---------- | --------------------- | ----------------- |
| `shading-D9D9D9`            | #D9D9D9    | 5463                  | grey (decision 5) |
| `shading-pct15-AUTO-AUTO`   | #D9D9D9    | 5440                  | refused, for now  |
| `shading-pct15-AUTO-FFFFFF` | #D9D9D9    | 5631                  | refused, for now  |
| `shading-C0C0C0`            | #C0C0C0    | 5626                  | grey (decision 9) |
| `highlight-lightGray`       | #C0C0C0    | 5629                  | grey (decision 5) |
| `shading-BFBFBF`            | #BFBFBF    | 5637                  | refused           |
| `shading-E6E6E6`            | #E6E6E6    | 5628                  | refused           |

A solid fill is opaque, and a theme fill is another reader mark (`shading-THEME-...`), so
`shading-C0C0C0` is that colour wherever it stands. The two 15% patterns wait, though Word printed
them as D9D9D9 here: the reader spells a pattern's colour from `w:color` alone and ignores
`w:themeColor`, `w:themeTint` and `w:themeShade` (15% of a themed orange accent reads as
`shading-pct15-AUTO-AUTO` too), and what Word draws for an automatic fill over a painted cell or
paragraph is not on record. Both are the reader's to say (planned for `docx-reader/1.34.0`).
`shading-BFBFBF`, `shading-E6E6E6`, the patterns and every shading the print did not show are
still refused (`formatting`).

The withheld section of an authority import (`docs/design/authority-import-withheld.md`,
decided, not built), which had taken 3.4.0, moves to 3.5.0; ADR 0002, ADR 0003, ADR 0006 and that
note say so. The authority importer moves to 2.4.3 and the certified Word importer to 1.3.1
(`IMPORTER_VERSION`, their `importer.lock.json`), because their golden vectors carry the version.

**Why.** ADR 0006, owner decision 9 (2026-10-07), taken as recommended under the owner's
authorisation. Evidence, on EMA's published English Word PI cuts (296 SmPC and 286 leaflet files;
internal corpus, counts only): among the SmPCs whose structure is ready but a section is refused,
the refused tabs in built sections were 490 in a table cell (193 after one raised letter, 112 after
one level symbol such as a bullet or "*", 33 after a raised symbol, 23 after a raised digit), 313
after a "Table N:" caption outside a cell and 91 inside one, and 177 mid-text (61 after a raised
digit, 49 after a raised letter). EMA's own 37 English ePI Bundles (`label-docx-reader/corpus/ema-epi`)
carry no tab anywhere and write "Table 1: ..." with a space. With this rule the SmPC files still
blocked by some tab fall from 54 to about 32 (measured by the coordinator's scan, not by this
change's tests). In the built but blocked files of the same cuts, `shading-C0C0C0` stands in 3
leaflet files.

**Impact assessment (step 0).** Importers of `src/fidelity/`:

- **Worker gate** (`src/pipeline.ts`, `src/contracts/canonical-submission.ts`): refuses a
  submission or report that names another version. A 3.3.0 submission is refused, not
  re-evaluated.
- **Authority importer** (`src/authority/`): unchanged in what it does; 2.4.3, its vectors moving
  only in the hashes that embed the version, every outcome unchanged.
- **Certified Word importer** (`src/certified-word/`): unchanged in what it does; 1.3.1, its
  vectors moving only in hashes (the recompute results name the new Zone A versions, the
  submissions the new normalisation version), every outcome unchanged.
- **Query service** (`src/query/tools.ts`): `get_section` and `verify_quote` report the new
  version. Every stored narrative normalises to the same text: sections 1 to 6 do not change.
- **Agent** (`agent/`): its post-check accepts answers under exactly one version, so its constant
  moves in this change. Either deploy order leaves a window in which the agent and the query
  service differ and every block is flagged `checksum-mismatch` and shown unverified, never wrong.
  The query service redeploys on the merge (`deploy.yml`), so the agent on Agent Engine is
  redeployed from the merged commit right after it, as for 3.3.0 (#188; its README).
- **Zone A** (`zone-a/`): the port's constant moves; `word-epi/1.4.0` carries the rule. The
  components locked to the files that changed move a patch version: `word-drawing/1.2.1` (it
  reads `word_epi.py`; `drawing.py`'s rules do not change: it collapses a tab and a space alike,
  in a cell's line as in any other, and compares marks on characters that are not whitespace, so a
  typed label in a cell and a raised key are read as Chrome draws them, held by a test that draws
  them in Chrome; it reads every mark of `GREY` as the silver background, so C0C0C0 needs no
  change there), registry 1.3.1, `qrd-check/1.6.1`, `smpc-structure/1.3.1`, `pl-structure/1.0.1`
  and `product/1.0.10` (`zone-a/versions.lock.json`). The registries, the five QRD check results
  and the certified Word recompute fixtures move in those version strings (and the registry's
  hash) only, byte for byte otherwise.
- **The Word drawing** (`src/render/word-drawing/lock.json`): its version moves to
  `word-drawing/1.2.1`, the code's; dev's image digest is unchanged, since the image holds no Zone
  A code (the drawing build mounts `zone-a/src` and `label-docx-reader/src` from main's checkout,
  `Dockerfile.renderer`). The real record dev's build signed for the committed synthetic SmPC
  (`test/fixtures/certified-word/drawing/smpc.record.json`) names `word-epi/1.3.2` in its request
  and `word-drawing/1.2.0`: this build's request and drawing id give another path, so the gate
  finds it nowhere, and a dry run answers `recomputed` until dev draws the label again. The
  synthetic SmPC's request is to be published again after the deploy, and the new record
  committed. Meanwhile `test/certified-word/drawing.test.ts` holds the real record to the build it
  was drawn by (the recompute's bytes it names, frozen beside it as `smpc.recompute.json`, its own
  request, dev's pins with `word-drawing/1.2.0`) and shows this build finds it nowhere, and
  `test/certified-word/recompute.test.ts` holds the gate's step 5 to a record made as dev's build
  makes one for this build, signed by a key made for the test.

**Steps 1–6.** 1: `NORMALIZATION_VERSION` is `fidelity-norm/3.4.0` everywhere it is held. 2:
`npm run vectors:generate`, `npm run contracts:fixtures`, `npm run contracts:quote-edge`,
`npm run differential:smoke`, `npm run authority:vectors` and `npm run certified-word:vectors`
regenerated `test/fixtures/fidelity/vectors.json` (651 → 652: verify 186 → 187; normalisation 71
and XHTML 394 unchanged), the contract fixtures, the quote-edge export, the smoke corpus and both
importers' vectors; `npm run authority:lock` and `npm run certified-word:lock` added 2.4.3 and
1.3.1; in `zone-a/`, `scripts/lock_versions.py`, `scripts/generate_qrd_registry.py`,
`scripts/check_labels.py` and `scripts/certified_word_fixtures.py`. 3: below. 4: one verify vector
(a certified Word page with a caption's number, a raised footnote key and a non-breaking hyphen as
typed labels, in table cells and outside them, against the narrative built from the same read;
passed), reviewed by hand (`test/fixtures/fidelity/cases.ts`). The code point table's test records
3.4.0 with 3.1.0's hash. `zone-a/tests/test_word_epi.py`: each new label kind outside and inside a
cell (both outputs write the space and the scanner reads the narrative as the page), a raised key
of one to three code points, followed by spaces, and with its tab raised; every near miss above,
a raised Arabic letter, U+200F, an Arabic-Indic digit and a combining mark included, outside and
inside a cell, still `tab`; Chrome drawing typed labels in a cell and outside as
the read, and a key the read does not raise as otherwise; `GREY` pinned to its three members
exactly; each the silver span on both outputs, and Chrome drawing each as the read in a cell and
outside (and the same text unshaded as otherwise); a .docx run of solid C0C0C0 shading read by the
label reader (`read_body`) carried as the span, and the same run with `w:themeFill="background1"`
refused; and `shading-BFBFBF`, `shading-E6E6E6`, the 15% patterns and their near misses (another
percentage, fill or colour, `C0C0C1`, a theme fill) still `formatting`. The 3.3.0 cases are unchanged but the
two that were refused only for standing in a cell (a typed bullet and a dash), which now carry. 5:
ADR 0002, 0003 and 0006 amended as above. 6: UR-09 and `AGENTS.md` name 3.4.0.

**Changed vectors (step 3).** Compared by name with the 3.3.0 vectors: no normalisation or XHTML
vector changed and none was removed; the one above was added. Every other verify vector changed
in exactly three fields and no other: `input.normalizationVersion`,
`expected.normalizationVersion` and `expected.reportHash`.

**Differential proof.** Sections 1 to 6 do not change; the smoke corpus is regenerated, and
TypeScript and Python agree on every vector (`zone-a/tests/test_golden_vectors.py`,
`zone-a/tests/test_differential.py`). The certified Word rule is Zone A's alone; the builder's
seeded random bodies hold its two outputs to each other (`zone-a/tests/test_word_epi.py`).

**Blast radius.** Every approved hash that embeds the version moves (step 7). No accepted text
changes; text the 3.3.0 rule refused is now carried. The five Word SmPC fixtures and the QRD
template build as before (their narratives, and so Chrome's recording, are unchanged).

**Step 7.** Previously approved submissions require re-approval: the version is part of the
approved content hash. The demonstration's approvals are synthetic placeholders and are
re-seeded.

**Approval (step 8).** Not obtained: author and releaser are the same identity. This change
touches an approved hash, so it needs a quality representative other than the author.
