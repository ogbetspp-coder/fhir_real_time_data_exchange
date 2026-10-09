# Recorded change: `fidelity-norm/3.6.0` → `fidelity-norm/3.7.0`, 2026-10-09

_Follows the procedure (steps 0–8) in `docs/validation/README.md`, "Change control for shared,
evidenced libraries". The UR- rows it cites are in that file._

**What changed.** `NORMALIZATION_VERSION` moved from `fidelity-norm/3.6.0` to
`fidelity-norm/3.7.0` in `src/fidelity/normalize.ts`, `zone-a/src/zone_a/fidelity/normalize.py`
and the agent's `quote_edge.NORMALIZATION_VERSION`. In `docs/fidelity-normalization.md`, only
section 7's certified Word rule changes (`zone_a.word_epi`, `word-epi/1.7.0`, and the drawing check
`zone_a.drawing`, `word-drawing/1.3.0`); sections 1 to 6 and the code point table do not:

- **The 15% pattern greys** (not an owner decision: Word's own print). `shading-pct15-AUTO-AUTO`
  and `shading-pct15-AUTO-FFFFFF` join `word_epi.GREY`, the template's grey, which the narrative
  writes as the silver span and the page does not mark. Word's print of synthetic probes
  (2026-10-07, `label-docx-reader-scratch/grey-shading/RESULTS.md`) drew each as exactly #D9D9D9:
  on a white page, over a table cell shaded FFFF00 (the cell yellow around it) and over a
  paragraph shaded D9D9D9 (not darker); the automatic fill is opaque white. #D9D9D9 is exactly
  what `shading-D9D9D9`, already the grey, paints. They were held back from 3.4.0 for one reason:
  the reader read a theme-coloured pattern as `AUTO`. `docx-reader/1.34.0` (on main) names a
  pattern's theme colour and theme fill in the kind (`shading-pct15-THEME-accent2-AUTO`, which
  Word draws #FCEBE0; `shading-pct15-AUTO-THEME-background1`), and refuses a theme colour it
  cannot resolve, so `AUTO` now means the automatic colour; `zone-a/tests/test_word_epi.py` holds
  that by writing .docx files and reading them with the reader. Every other pattern stays refused
  (`formatting`): 10%, 12%, 20% or 25%, `solid`, a stripe, another colour (`000000`, `FFFFFF`) or
  fill (`D9D9D9`, `E6E6E6`, `C0C0C0`, `FFFFFE`), a theme's colour, tint, shade or fill, and any
  spelling not the reader's.
- **A nudge is layout** (ADR 0006, owner decision 13, taken as recommended under the owner's
  authorisation, for the owner's review). The reader's kind is `position±N-size<run>-in<paragraph>`
  (signed half-points, the run's `w:sz`, its paragraph's size; `docx-reader/1.34.0`). A position
  mark is left out (`word_epi.nudged`), as an underline that changes nothing is, in the
  narrative, the page, a heading and the drawing check's read, where all of these hold: |N| ≤ 2
  half-points (a shift of at most a point); the run's size is its paragraph's; and no code point
  of it is superscript or subscript. Every other position kind still refuses the section
  (`formatting`, in a heading `heading-formatting`): a larger shift, a smaller or larger run (a
  superscript typed by hand is raised and smaller), a shift with `vertAlign` on any of its
  characters, and any spelling the reader does not write (no sign, `+0`, a leading zero, a part
  missing, a size of non-ASCII digits). Rationale: a full-size run moved up or down by at most a
  point reads as the same characters on the same line; it cannot become an exponent, an index or
  a footnote mark, which are smaller or moved further. A tab inside a nudged run is judged as if
  the nudge were absent (decision 11).

The drawing check (`zone_a.drawing.read_lines`) leaves a nudge out on the read's side, as the
narrative does, by the same predicate; every other shift stays a mark Chrome does not draw, and
differs. The pattern greys need nothing there: the check already reads every `GREY` kind as the
narrative's silver span (Chrome draws them so; `test_chrome_draws_every_grey_as_the_silver_span_word_shades`
runs over `GREY` and so over both).

**Why.** Evidence, on EMA's published English Word PI cuts (internal corpus,
`label-docx-reader-scratch/ema-pi-tc`, counts only), measured with `docx-reader/1.34.0`
(`baseline.py`, 296 SmPC and 286 leaflet cuts; before on main 148a71b, after on this branch). By
file:

| SmPC (296)               | before | after |
| ------------------------ | -----: | ----: |
| whole                    |     20 |    22 |
| built, a section refused |     81 |    79 |
| needs a person           |     33 |    33 |
| document refused         |     11 |    11 |
| parts unclear            |      8 |     8 |
| reader refused           |    143 |   143 |

| Leaflets (286)           | before | after |
| ------------------------ | -----: | ----: |
| whole                    |     16 |    20 |
| built, a section refused |     53 |    49 |
| needs a person           |    125 |   125 |
| document refused         |      1 |     1 |
| reader refused           |     91 |    91 |

By section: of 4 194 SmPC sections built, 242 were refused before and 214 after
(`formatting` on a 15% pattern 15 → 0 in 14 → 0 files; `formatting` on a position 41 → 30 in 16
→ 13 files; `heading-formatting` on a position 3 → 0; one section first refused on a pattern is
now refused on a strike, 9 → 10). Of 1 870 leaflet sections built, 147 were refused before and
121 after (`formatting` on a 15% pattern 26 → 0 in 13 → 0 files). No other refusal count moved,
and no section is newly refused for anything else. The positions left are larger shifts or runs
at another size than their paragraph's. (The coordinator's census, counting every mark and not
only a section's first refusal, gave the shifts -1 at 1 941 uses, +2 at 635 and +1 at 197, and
`formatting:position` in 21 SmPC files; `shading-pct15-AUTO-AUTO` in 10 built-but-blocked SmPC
files and `shading-pct15-AUTO-FFFFFF` in 2, each with leaflets.)

**Impact assessment (step 0).** Importers of `src/fidelity/`:

- **Worker gate** (`src/pipeline.ts`, `src/contracts/canonical-submission.ts`): refuses a
  submission or report that names another version. A 3.6.0 submission is refused, not
  re-evaluated.
- **Authority importer** (`src/authority/`): unchanged in what it does; 2.4.6, its vectors moving
  only in the hashes that embed the version, every outcome unchanged.
- **Certified Word importer** (`src/certified-word/`): unchanged in what it does; 1.3.6, its
  vectors moving only in hashes (the recompute results name the new Zone A versions, the
  submissions the new normalisation version), every outcome unchanged.
- **Query service** (`src/query/tools.ts`): `get_section` and `verify_quote` report the new
  version. Every stored narrative reads to the same text: sections 1 to 6 do not change.
- **Agent** (`agent/`): its post-check accepts answers under exactly one version, so its constant
  moves in this change. Either deploy order leaves a window in which the agent and the query
  service differ and every block is flagged `checksum-mismatch` and shown unverified, never wrong.
  The query service redeploys on the merge (`deploy.yml`), so the agent on Agent Engine is
  redeployed from the merged commit right after it, as for 3.3.0 (#188), 3.4.0 (#209), 3.5.0
  (#211) and 3.6.0 (#213; its README).
- **Zone A** (`zone-a/`): the port's constant moves; `word-epi/1.7.0` and `word-drawing/1.3.0`
  (each a minor: what they carry and compare changes), and the components locked to the files
  that changed move a patch version: registry 1.3.4, `qrd-check/1.6.4`, `smpc-structure/1.3.4`,
  `pl-structure/1.0.4` and `product/1.0.13` (`zone-a/versions.lock.json`). The registries, the
  five QRD check results and the certified Word recompute fixtures move in those version strings
  (and the registry's hash) only, byte for byte otherwise; Chrome's recording of the five Word
  SmPCs and the QRD template (`zone-a/tests/fixtures/word-smpc/chrome.json`) was made again and is
  byte for byte the same.
- **The Word drawing** (`src/render/word-drawing/lock.json`): its version moves to
  `word-drawing/1.3.0`, the code's; dev's image digest is unchanged, since the image holds no Zone
  A code. The record dev's build signed for `word-epi/1.6.0` is at another path than this build's
  request and drawing id give, so a dry run answers `recomputed` until dev draws the synthetic
  SmPC again after the deploy (`docs/design/certified-word-drawing.md`).
  `test/certified-word/drawing.test.ts` holds the committed real record to the build it was drawn
  by, as before, and `test/certified-word/recompute.test.ts` holds the gate's step 5 to a record
  made as dev's build makes one for this build, signed by a key made for the test.

**Steps 1–6.** 1: `NORMALIZATION_VERSION` is `fidelity-norm/3.7.0` everywhere it is held. 2: in
`zone-a/`, `scripts/lock_versions.py`, `scripts/generate_qrd_registry.py`,
`scripts/check_labels.py`, `scripts/certified_word_fixtures.py` and `scripts/word_fixtures.py
record`; then `npm run contracts:generate`, `npm run vectors:generate`, `npm run
contracts:fixtures`, `npm run contracts:quote-edge`, `npm run differential:smoke`, `npm run
authority:vectors` and `npm run certified-word:vectors` regenerated
`test/fixtures/fidelity/vectors.json` (691 → 693: verify 191 → 193; normalisation 71 and XHTML
429 unchanged), the contract fixtures, the quote-edge export, the smoke corpus and both
importers' vectors; `npm run authority:lock` and `npm run certified-word:lock` added 2.4.6 and
1.3.6. 3: below. 4: two verify vectors, reviewed by hand (`test/fixtures/fidelity/cases.ts`):

- `certified-word-pattern-grey-and-nudges`: a certified Word page with a 15% pattern grey on an
  automatic fill in a paragraph and on a white fill in a table cell, a "2" raised a point and
  "a day" and "mg" lowered half a point at their paragraph's size, against the narrative built
  from the same read (passed: the grey the silver span, no shift on either side);
- `certified-word-nudge-as-superscript`: the same page against that narrative with the nudged
  "2" drawn as `<sup>2</sup>` (failed: a nudge is no exponent, "²" is not "2").

`zone-a/tests/test_word_epi.py` builds both vectors' page and the passing one's narrative from
the same read by `zone_a.word_epi`, and pins, with the reader as the source of the kinds where it
can write them:

- `GREY`'s exact membership (five kinds);
- the pattern greys carried, read from .docx files (`color` auto or absent, `fill` auto, absent,
  `FFFFFF` or `ffffff`), and refused (35 kinds by name, 8 read from .docx files): a theme's
  colour (accent2, text1, a shade) or fill (background1), `000000`, `D9D9D9` and other fills,
  10%, 12%, 20%, 25%, solid and stripes, and spellings not the reader's; a theme colour the reader
  cannot resolve (no theme part) is refused by the reader;
- the nudges left out (eight kinds, ±1 and ±2 at sizes 7 to 24) in a paragraph and a cell, beside
  bold and beside a superscript, and in a heading; refused (33 kinds): shifts of 3 to 99 999
  half-points, a run smaller or larger than its paragraph's, a shift with superscript or
  subscript over any of its characters (five overlaps, each script), and spellings not the
  reader's; read from .docx files: `w:position` -2, 1 and 2 at the default size left out; 3, -2
  at size 8 in a paragraph of 10, 6 at size 7, and 2 with `vertAlign` superscript refused;
- the drawing check's read leaving a nudge out and keeping every other shift, and Chrome drawing
  a section with a nudge and a pattern grey as the read (the drawing check agrees).

5: ADR 0006 gains owner decision 13 and the note on the pattern greys (decision 9's table names
them carried from 3.7.0); ADR 0002 and ADR 0003 amended for the withheld note (below). 6: UR-09
and `AGENTS.md` name 3.7.0.

The withheld section of an authority import (`docs/design/authority-import-withheld.md`,
decided, not built), which had taken 3.7.0, moves to 3.8.0; ADR 0002, ADR 0003 and that note say
so. The authority importer moves to 2.4.6 and the certified Word importer to 1.3.6
(`IMPORTER_VERSION`, their `importer.lock.json`), because their golden vectors carry the version.

**Mutation spot-check (not committed).** The rules were broken in turn in a scratch edit of
`zone-a/src` and the tests above rerun; each of the 8 mutants failed at least one: a shift of 3
let in, the sign made optional, any paragraph size let in, subscript not checked, a script mark
checked only where it covers the whole shift, a nudge refused in a heading,
`shading-pct15-AUTO-FFFFFF` left out of `GREY`, and the drawing check reading a nudge as a mark.

**Changed vectors (step 3).** Compared by name with the 3.6.0 vectors: no normalisation or XHTML
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
changes: a section 3.6.0 carried is carried alike; sections 3.6.0 refused only for a pattern grey
or a nudge are now carried. The five Word SmPC fixtures and the QRD template build as before.

**Step 7.** Previously approved submissions require re-approval: the version is part of the
approved content hash. The demonstration's approvals are synthetic placeholders and are
re-seeded.

**Approval (step 8).** Not obtained: author and releaser are the same identity. This change
touches an approved hash, so it needs a quality representative other than the author. Owner
decision 13 is taken as recommended under the owner's authorisation, for the owner's review.
