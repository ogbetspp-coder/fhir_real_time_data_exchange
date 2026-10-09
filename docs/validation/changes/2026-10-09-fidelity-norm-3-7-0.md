# Recorded change: `fidelity-norm/3.6.0` → `fidelity-norm/3.7.0`, 2026-10-09

_Follows the procedure (steps 0–8) in `docs/validation/README.md`, "Change control for shared,
evidenced libraries". The UR- rows it cites are in that file._

**What changed.** `NORMALIZATION_VERSION` moved from `fidelity-norm/3.6.0` to
`fidelity-norm/3.7.0` in `src/fidelity/normalize.ts`, `zone-a/src/zone_a/fidelity/normalize.py`
and the agent's `quote_edge.NORMALIZATION_VERSION`. In `docs/fidelity-normalization.md`, only
section 7's certified Word rule changes (`zone_a.word_epi`, `word-epi/1.7.0`); sections 1 to 6
and the code point table do not.

**The 15% pattern greys** (not an owner decision: Word's own print). `shading-pct15-AUTO-AUTO`
and `shading-pct15-AUTO-FFFFFF` join `word_epi.GREY`, the template's grey, which the narrative
writes as the silver span and the page does not mark. Word prints each as exactly #D9D9D9, which
is what `shading-D9D9D9`, already the grey, paints:

- 2026-10-07 (`label-docx-reader-scratch/grey-shading/RESULTS.md`, "Pattern over painted
  backgrounds"): a run's, on a white page, over a table cell shaded FFFF00 (the cell yellow
  around it) and over a paragraph shaded D9D9D9 (not darker);
- 2026-10-09, this change (the same file, "A 15% pattern's edge cases"; the probes and Word's
  PDFs committed as `zone-a/tests/fixtures/word-oracle/claude-edges-probe.*` and
  `claude-edges-page-probe.*`, printed through `label_docx.word`'s locked oracle, Microsoft Word
  16.113.4 for macOS, closed by name, never quit; counted with `look.swift` at 144 dpi): a run's
  with `w:color` absent, with `w:fill` absent, and with both; a paragraph's (`pPr/shd`) on an
  automatic or white fill, or with both absent, painted across the paragraph's width; a run's on
  an automatic or white fill, and a paragraph's, in a cell shaded 000000 (the cell black around
  it); and a run's, on either fill, and a paragraph's, on a page coloured FFFF00
  (`w:background`, `displayBackgroundShape`), which Word does not print.

The automatic fill is opaque white. The reader (`docx-reader/1.34.0`) reports none of those
grounds on the text: each probe case reads as the plain kind, so the rule had to hold on every one
of them, and it does. The builder does not see a cell's shading or the page colour; what Word
shows on screen over a page colour is not measured, and no English EMA cut has one (counts: 1 SmPC
and 1 leaflet carry a `w:background` of white or auto, the other 580 none). A run's pattern over a
paragraph shaded 000000 reads with that paragraph's `shading-000000` too, and is refused for it.
They were held back from 3.4.0 for one reason: the reader read a theme-coloured pattern as `AUTO`.
`docx-reader/1.34.0` (on main) names a pattern's theme colour and theme fill in the kind
(`shading-pct15-THEME-accent2-AUTO`, which Word draws #FCEBE0;
`shading-pct15-AUTO-THEME-background1`), and refuses a theme colour it cannot resolve, so `AUTO`
now means the automatic colour; `zone-a/tests/test_word_epi.py` holds that by writing .docx files
and reading them with the reader. Every other pattern stays refused (`formatting`): 10%, 12%, 20%
or 25%, `solid`, a stripe, another colour (`000000`, `FFFFFF`) or fill (`D9D9D9`, `E6E6E6`,
`C0C0C0`, `FFFFFE`), a theme's colour, tint, shade or fill, and any spelling not the reader's.

**Withdrawn: a nudge as layout.** This change first also carried ADR 0006 owner decision 13: a
run raised or lowered by `w:position` by at most 2 half-points, at its paragraph's size and
neither superscript nor subscript, left out as layout. The independent review of #216 found it is
not exact. The reader's kind (`position±N-size<run>-in<paragraph>`, `docx-reader/1.34.0`) takes
the paragraph's size from its style and the defaults, not from the text around the run: "Area 5
m" set directly to 14 pt, then "2" raised a point with no size of its own (the style's 10 pt),
then " daily" at 14 pt, reads `position+2-size20-in20` and was carried as "m2", where Word draws
a smaller raised 2 (m²). And nudges add up: -2 on one run and +2 on the next is a shift of 2 pt
between them. The rule was removed whole (the builder's predicate, its uses in the narrative,
the page, the heading and the drawing check, its tests and vectors, ADR 0006's decision and the
specification's text); text raised or lowered by `w:position` still refuses the section by any
amount, as under 3.6.0, and a test pins the review's case. Carrying a nudge needs the reader to
spell the sizes of the text actually around a shifted run (queued for `docx-reader/1.35.0`) and a
bound on the shift between adjacent runs; that is a future owner decision. The drawing check's
rules therefore do not change (`word-drawing/1.2.6`, a patch: it reads `word_epi.py`).

**Why.** Evidence, on EMA's published English Word PI cuts (internal corpus,
`label-docx-reader-scratch/ema-pi-tc`, counts only), measured with `docx-reader/1.34.0`
(`baseline.py`, 296 SmPC and 286 leaflet cuts; before on main 148a71b, after on this branch). By
file:

| SmPC (296)               | before | after |
| ------------------------ | -----: | ----: |
| whole                    |     20 |    21 |
| built, a section refused |     81 |    80 |
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

By section: of 4 194 SmPC sections built, 242 were refused before and 227 after (`formatting` on
a 15% pattern 15 → 0, in 14 → 0 files); of 1 870 leaflet sections built, 147 before and 121 after
(26 → 0, in 13 → 0 files). No other refusal count moved, and no section is newly refused.
`formatting` on a position stays as on main (41 SmPC sections in 16 files, by first refusal).

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
- **Zone A** (`zone-a/`): the port's constant moves; `word-epi/1.7.0` (a minor: what it carries
  changes), and the components locked to the files that changed move a patch version:
  `word-drawing/1.2.6`, registry 1.3.4, `qrd-check/1.6.4`, `smpc-structure/1.3.4`,
  `pl-structure/1.0.4` and `product/1.0.13` (`zone-a/versions.lock.json`). The registries, the
  five QRD check results and the certified Word recompute fixtures move in those version strings
  (and the registry's hash) only, byte for byte otherwise; Chrome's recording of the five Word
  SmPCs and the QRD template (`zone-a/tests/fixtures/word-smpc/chrome.json`) was made again and is
  byte for byte the same.
- **The Word drawing** (`src/render/word-drawing/lock.json`): its version moves to
  `word-drawing/1.2.6`, the code's; dev's image digest is unchanged, since the image holds no Zone
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

- `certified-word-pattern-grey`: a certified Word page with a 15% pattern grey over a run (on an
  automatic fill), over a whole paragraph, and in a table cell (on a white fill), against the
  narrative built from the same read (passed: the grey the silver span, the page unmarked);
- `certified-word-pattern-grey-dropped`: the same page against that narrative with the grey
  paragraph left out, as a renderer hiding the template's "not printed" text would (failed: grey
  text is text).

`zone-a/tests/test_word_epi.py` builds both vectors' page and the passing one's narrative from
the same read by `zone_a.word_epi`, and pins, with the reader as the source of the kinds where it
can write them:

- `GREY`'s exact membership (five kinds);
- Word's printed edge cases: each probe paragraph of `claude-edges-probe.docx` and
  `claude-edges-page-probe.docx` read by the reader as the plain kind and carried as the silver
  span (14 cases), and the run over a black paragraph refused for its `shading-000000`;
- the pattern greys carried, read from .docx files (`color` auto or absent, `fill` auto, absent,
  `FFFFFF` or `ffffff`), and refused (35 kinds by name, 8 read from .docx files): a theme's
  colour (accent2, text1, a shade) or fill (background1), `000000`, `D9D9D9` and other fills,
  10%, 12%, 20%, 25%, solid and stripes, and spellings not the reader's; a theme colour the reader
  cannot resolve (no theme part) is refused by the reader;
- every position refused (12 kinds, ±1 and ±2 at the paragraph's size among them), in a
  paragraph, a table cell and a heading, and the review's case read from a .docx.

5: ADR 0006's decision 9 table names the pattern greys carried from 3.7.0, with a note on them
(not an owner decision; decision 13 was withdrawn with the nudge); ADR 0002 and ADR 0003 amended
for the withheld note (below). 6: UR-09 and `AGENTS.md` name 3.7.0.

The withheld section of an authority import (`docs/design/authority-import-withheld.md`,
decided, not built), which had taken 3.7.0, moves to 3.8.0; ADR 0002, ADR 0003 and that note say
so. The authority importer moves to 2.4.6 and the certified Word importer to 1.3.6
(`IMPORTER_VERSION`, their `importer.lock.json`), because their golden vectors carry the version.

**Mutation spot-check (not committed).** `GREY` was narrowed in turn in a scratch edit of
`zone-a/src` and the tests rerun; each mutant failed at least one: `shading-pct15-AUTO-FFFFFF`
left out, `shading-pct15-AUTO-AUTO` left out, and `shading-pct15-AUTO-D9D9D9` let in.

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
are now carried. The five Word SmPC fixtures and the QRD template build as before.

**Step 7.** Previously approved submissions require re-approval: the version is part of the
approved content hash. The demonstration's approvals are synthetic placeholders and are
re-seeded.

**Approval (step 8).** Not obtained: author and releaser are the same identity. This change
touches an approved hash, so it needs a quality representative other than the author. No owner
decision is taken: the pattern greys are Word's own print, and the nudge that would have needed
one was withdrawn (above).
