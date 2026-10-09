# Recorded change: `fidelity-norm/3.5.0` → `fidelity-norm/3.6.0`, 2026-10-08

_Follows the procedure (steps 0–8) in `docs/validation/README.md`, "Change control for shared,
evidenced libraries". The UR- rows it cites are in that file._

**What changed.** `NORMALIZATION_VERSION` moved from `fidelity-norm/3.5.0` to
`fidelity-norm/3.6.0` in `src/fidelity/normalize.ts`, `zone-a/src/zone_a/fidelity/normalize.py`
and the agent's `quote_edge.NORMALIZATION_VERSION`. In `docs/fidelity-normalization.md`, only
section 5's lowered-half rule changes, in both scanners (`src/fidelity/xhtml.ts`,
`zone-a/src/zone_a/fidelity/xhtml.py`):

- **The half-life's forms.** A `sub` holding ½ was kept only as `t<sub>½</sub>`: a lowercase `t`
  that starts a word, and ½ alone. From 3.6.0 the code point before may be `t` or `T` (U+0054),
  under the same word-start rule; and the `sub`'s content may be ½ alone, or ½ followed by exactly
  one of a phase's letters, α, β, γ or δ (U+03B1–U+03B4: the phase half-lives t½α, t½β, t½γ,
  t½δ), or by U+00DF ß, which labels write for β. Every other letter after ½ still rejects: μ
  (U+03BC) and µ (U+00B5) above all, the micro prefix, so that "T½μ g" is no half-life; and ε,
  λ, π, ς, ω and the rest of the Greek alphabet, a capital, ϐ, ᵦ, ẞ and a Latin letter. Everything
  else is unchanged: the rules for the code point after the `sub`, `sup` still rejecting ½,
  `<sub>2½</sub>` rejecting, and a letter directly after the `sub` rejecting. Rationale: neither
  `T` nor α, β, γ, δ or ß is read as part of a number or a unit (no numeral, Roman ones included,
  is written with them), so none can join the ½ to a number on its side. The allowed contents are
  a closed list of six (`½`, `½α`, `½β`, `½γ`, `½δ`, `½ß`), not a range.

`zone_a.word_epi` does not change what it does (it writes a lowered ½ and its letter in one `sub`
and lets section 5 decide, as since 3.1.0); its version moves to `word-epi/1.6.0`, after
`docx-reader/1.34.0`'s 1.5.1, whose edits it carries, because the
scanner it reads changes.

**Withdrawn: a table's column widths.** This change first also carried, in section 5, a
`colgroup` of one `col` per column (`style="width: N%;"`, as the EMA's own ePIs write them), taken
as drawing every column of a table that the HTML table model would draw with a column of no width
(a staggered table: no cell of one column starts in some column), and, in section 7's certified
Word rule, a `colgroup` of Word's own `gridCol` widths for such a table. The independent review
of #213 found that a `colgroup` does not make Chrome draw every column. With the reviewer's table
(row 1: "Adults" over columns 0 and 1, "X" in column 2; row 2: "Y" in column 0, "10 mg" over
columns 1 and 2), Chrome drew column 1 at 0 px with a width of 0.01%, which the rule accepted; and
with honest widths of 30, 40 and 30% in a narrow container (160 px or 60 px) it laid the table
out at its min-content width and again drew column 1 at 0 px, so "10 mg" stood under one header
only. Nothing downstream checks geometry, so the rule could not be carried as exact, and it was
removed whole: the scanners, the builder, its vectors, the differential class and the
specification's text. A staggered table stays refused (`narrative`, `table-shape`), as under
3.5.0. Carrying one needs a layout proven to draw every column at every container width (for
example `table-layout: fixed` with a minimum width for each column, verified in Chrome at several
widths), and that is a future owner decision.

**Why.** Evidence, on EMA's published English Word PI cuts (internal corpus,
`label-docx-reader-scratch/ema-pi-tc`, counts only): under 3.5.0, 8 SmPC sections are refused
`narrative`/`unmappable-script` for a lowered ½. Measured on the same cuts with `docx-reader/1.33.0`
(`baseline.py`, 296 SmPC and 286 leaflet cuts, before on main e3085bf, after on this branch): of
4 194 SmPC sections built, 263 were refused before and 257 after: `narrative`/`unmappable-script`
8 → 2, every other count the same (the 2 left hold a lowered ½ with a small letter right after
the `sub`, which stays refused). The leaflets' 155 refused of 1 870 are unchanged, and so is every
file's outcome (SmPC: 18 whole, 83 built with a section refused, 142 refused by the reader, 33
needing a person, 12 refused whole, 8 parts unclear; leaflets: 14 whole, 55 with a section
refused, 125 needing a person, 91 refused by the reader, 1 refused whole), since each file the
half-lives touch has another refused section. The 19 SmPC sections refused for a staggered table
(`narrative`/`table-shape`) stay refused (above).

**Impact assessment (step 0).** Importers of `src/fidelity/`:

- **Worker gate** (`src/pipeline.ts`, `src/contracts/canonical-submission.ts`): refuses a
  submission or report that names another version. A 3.5.0 submission is refused, not
  re-evaluated.
- **Authority importer** (`src/authority/`): unchanged in what it does; 2.4.5, its vectors moving
  only in the hashes that embed the version, every outcome unchanged.
- **Certified Word importer** (`src/certified-word/`): unchanged in what it does; 1.3.5, its
  vectors moving only in hashes (the recompute results name the new Zone A versions, the
  submissions the new normalisation version), every outcome unchanged.
- **Query service** (`src/query/tools.ts`): `get_section` and `verify_quote` report the new
  version. Every stored narrative reads to the same text: section 5 only accepts more, and
  sections 1 to 4 and 6 do not change.
- **Agent** (`agent/`): its post-check accepts answers under exactly one version, so its constant
  moves in this change. Either deploy order leaves a window in which the agent and the query
  service differ and every block is flagged `checksum-mismatch` and shown unverified, never wrong.
  The query service redeploys on the merge (`deploy.yml`), so the agent on Agent Engine is
  redeployed from the merged commit right after it, as for 3.3.0 (#188), 3.4.0 (#209) and 3.5.0
  (#211; its README).
- **Zone A** (`zone-a/`): the port's constant moves, and the scanner with the TypeScript. The
  components locked to the files that changed move a patch version: `word-epi/1.6.0`,
  `word-drawing/1.2.5` (it reads `word_epi.py`; `drawing.py`'s rules do not change), registry
  1.3.3, `qrd-check/1.6.3`, `smpc-structure/1.3.3`, `pl-structure/1.0.3` and `product/1.0.12`
  (`zone-a/versions.lock.json`). The registries, the five QRD check results and the certified
  Word recompute fixtures move in those version strings (and the registry's hash) only, byte for
  byte otherwise; Chrome's recording of the five Word SmPCs and the QRD template
  (`zone-a/tests/fixtures/word-smpc/chrome.json`) was made again and is byte for byte the same.
- **The Word drawing** (`src/render/word-drawing/lock.json`): its version moves to
  `word-drawing/1.2.5`, the code's; dev's image digest is unchanged, since the image holds no Zone
  A code. The record dev's build signed for `word-epi/1.5.0` (or any for 1.5.1) is at another path than this build's
  request and drawing id give, so a dry run answers `recomputed` until dev draws the synthetic
  SmPC again after the deploy. `test/certified-word/drawing.test.ts` holds the committed real
  record to the build it was drawn by, as before, and `test/certified-word/recompute.test.ts`
  holds the gate's step 5 to a record made as dev's build makes one for this build, signed by a
  key made for the test.

**Steps 1–6.** 1: `NORMALIZATION_VERSION` is `fidelity-norm/3.6.0` everywhere it is held. 2: in
`zone-a/`, `scripts/lock_versions.py`, `scripts/generate_qrd_registry.py`,
`scripts/check_labels.py`, `scripts/certified_word_fixtures.py` and `scripts/word_fixtures.py
record`; then `npm run contracts:generate`, `npm run vectors:generate`, `npm run
contracts:fixtures`, `npm run contracts:quote-edge`, `npm run differential:smoke`, `npm run
authority:vectors` and `npm run certified-word:vectors` regenerated
`test/fixtures/fidelity/vectors.json` (654 → 691: XHTML 394 → 429, verify 189 → 191;
normalisation 71 unchanged), the contract fixtures, the quote-edge export, the smoke corpus and
both importers' vectors; `npm run authority:lock` and `npm run certified-word:lock` added 2.4.5
and 1.3.5. 3: below. 4: 35 XHTML vectors and two verify vectors, reviewed by hand
(`test/fixtures/fidelity/cases.ts`), each run through both scanners:

- accepted (4): `the T<sub>½</sub>` and `(T<sub>½</sub>)`; `t<sub>½α</sub>`, `t<sub>½β</sub>`,
  `(T<sub>½γ</sub>)` and `t<sub>½δ</sub>`; `t<sub>½ß</sub>` and `<em>T</em><sub>½ß</sub>` in a
  table; β and δ as character references;
- refused (31): `AT<sub>½</sub>` (a `T` mid-word), `2T<sub>½</sub>`, `<sub>T</sub><sub>½</sub>`,
  a fullwidth Ｔ, `V<sub>½</sub>`; `½αβ`, `½1`, `½ α`, `α½`; after ½: μ (`T<sub>½μ</sub> g`), µ
  U+00B5, μ as a reference, ε, λ, π, ς, ω, ΰ U+03B0, ϊ U+03CA, a capital Β, ϐ U+03D0, ᵦ U+1D66,
  `b`, ẞ U+1E9E; `t<sub>½β</sub>x`, `t<sub>½β</sub>2`, `t<sub>½</sub><sub>β</sub>`,
  `t<sub>½</sub>β`, `2<sub>½β</sub>`; and in `sup`, `T<sup>½</sup>` and `t<sup>½β</sup>`;
- two verify vectors: a certified Word page with the half-lives T½, t½α, t½δ and t½ß in a
  paragraph, t½β in a table cell and (T½) in brackets, against the narrative built from the same
  read (passed); and the same page against that narrative with β where the label writes ß
  (failed: labels write ß for β, but the text holds what Word holds).
  `zone-a/tests/test_word_epi.py` builds both vectors' page and the passing one's narrative from
  the same read by `zone_a.word_epi`, and holds the builder to section 5 on T½, t½γ and t½ß
  (carried) and T½μ, t½ω, AT½ and t½αβ (refused, `narrative`, `unmappable-script`).

5: ADR 0002 and ADR 0003 amended for the withheld note (below), and ADR 0003's stated residual of
a lowered ½ (it verifies against the same code points on the line) names the new forms; ADR 0006
is not amended (no owner decision). 6: UR-09 and `AGENTS.md` name 3.6.0.

The withheld section of an authority import (`docs/design/authority-import-withheld.md`,
decided, not built), which had taken 3.6.0, moves to 3.7.0; ADR 0002, ADR 0003 and that note say
so. The authority importer moves to 2.4.5 and the certified Word importer to 1.3.5
(`IMPORTER_VERSION`, their `importer.lock.json`), because their golden vectors carry the version.

**Mutation spot-check (not committed).** In scratch copies of `zone-a/src` and `src/`, the rule
was broken in turn and the XHTML vectors rerun through the changed scanner; each of the 12
mutants changed the outcome of at least one vector: in the Python, no `T`, any letter before ½,
ß left out, δ left out, μ let in, any Greek small letter let in, and two letters after ½ let in;
in the TypeScript, no `T`, ß left out, δ left out, μ let in, and two letters after ½ let in.

**Changed vectors (step 3).** Compared by name with the 3.5.0 vectors: no normalisation or XHTML
vector changed and none was removed; the 37 above were added. Every other verify vector changed
in exactly three fields and no other: `input.normalizationVersion`,
`expected.normalizationVersion` and `expected.reportHash`.

**Differential proof.** The rule is in both scanners; every one of the 429 XHTML vectors is read
alike by the TypeScript and the Python (`zone-a/tests/test_golden_vectors.py`). The seeded
differential run (`scripts/fidelity/differential.ts`, held by `zone-a/tests/test_differential.py`)
crosses every lowered ½ with every neighbour: from 3.6.0 also after `(T` and `AT`, and ½ followed
by β, δ (as references), ß, μ, αβ, a space and α, and 1 (5 796 cases a corpus to 12 236). The
TypeScript and the Python agree on every case of both corpora (seed 20260920 and one at the
time: 28 472 cases), and the smoke corpus
is regenerated, moving only in versions and report hashes.

**Blast radius.** Every approved hash that embeds the version moves (step 7). No accepted text
changes: a narrative 3.5.0 accepted reads the same; narratives 3.5.0 refused for the half-life's
new forms are now accepted. The five Word SmPC fixtures and the QRD template build as before.

**Step 7.** Previously approved submissions require re-approval: the version is part of the
approved content hash. The demonstration's approvals are synthetic placeholders and are
re-seeded.

**Approval (step 8).** Not obtained: author and releaser are the same identity. This change
touches an approved hash, so it needs a quality representative other than the author. No owner
decision is taken: the rule widens section 5 only where the text cannot change, and the column
widths that would have needed one were withdrawn (above).
