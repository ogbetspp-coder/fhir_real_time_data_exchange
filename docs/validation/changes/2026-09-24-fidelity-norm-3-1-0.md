# Recorded change: `fidelity-norm/3.0.0` → `fidelity-norm/3.1.0`, 2026-09-24

_Follows the procedure (steps 0–8) in `docs/validation/README.md`, "Change control for shared,
evidenced libraries". The UR- rows it cites are in that file._

**What changed.** `NORMALIZATION_VERSION` moved from `fidelity-norm/3.0.0` to
`fidelity-norm/3.1.0` in `src/fidelity/normalize.ts` and `zone-a/src/zone_a/fidelity/normalize.py`.
`docs/fidelity-normalization.md` section 5 gains one rule, and section 9 an entry: inside `sub`
only, U+00BD VULGAR FRACTION ONE HALF and U+221E INFINITY are kept unchanged instead of
rejecting (`unmappable-script`). `t<sub>½</sub>` reads `t½` and `AUC<sub>(0-∞)</sub>` reads
`AUC₍₀₋∞₎`. Inside `sup` both still reject, and so does every other fraction or symbol inside
`sub`. In code, the `sub` rule's kept set gains the two code points (`KEPT_IN_SUBSCRIPT` in
`src/fidelity/xhtml.ts` and `zone-a/src/zone_a/fidelity/xhtml.py`). The design is
`docs/design/fidelity-norm-3-1-0.md`. The authority importer moves to 1.1.0 (`IMPORTER_VERSION`,
`src/authority/importer.lock.json`), because its golden vectors carry the version.

**Why.** ADR 0005 decision 1 left the EMA's lowered `½` (half-life) and `(0-∞)` (AUC) to a
minor version of the fidelity contract. A scan of every English SmPC the EMA's ePI service lists
(21 documents in 23 product Lists) found ½ lowered six times in four products and ∞ lowered twice,
and no other refused script content except Brukinsa's raised ± footnote mark. Without this
change no Imatinib Teva SmPC can be imported whole (4.5 and 5.2 refuse). The design explains why
the change admits no false pass: it creates no text a narrative could not already carry, and a
lowered ½ or ∞ reads as the same code point on the line, as a lowered letter already does
(ADR 0003's stated residual). Raised, the two differ (`2<sup>½</sup>` is a root), so `sup` still
refuses them.

**Impact assessment (step 0).** Importers of `src/fidelity/`:

- **Worker gate** (`src/pipeline.ts`, `src/contracts/canonical-submission.ts`): refuses a
  submission or report that names another version. A 3.0.0 submission is refused, not
  re-evaluated. The producers in the repository (the synthetic builder, the authority importer)
  take the version from the constant.
- **Authority importer** (`src/authority/`): its narrative stage runs the scanner, so it now
  also accepts the two forms. Version 1.1.0; its vectors move only in the version string and the
  hashes that embed it (the synthetic publication has neither form).
- **Crosswalk** (`src/fhir/transform.ts`): unchanged; it reads presence, and a section with a
  lowered ½ has text either way.
- **Query service** (`src/query/tools.ts`): `get_section` and `verify_quote` report the new
  version. A stored narrative 3.0.0 accepted normalises to the same text under 3.1.0, because
  the change only turns a refusal into text, so answers over the dev store are unchanged.
- **Zone A** (`zone-a/src/zone_a/fidelity/`): ported in this change.
- **Agent** (`agent/`): reads the version as an opaque string; its test double's constant and one
  test fixture move.
- **Spike scripts** (`scripts/spikes/document-ai/`): the recorded replay still verifies 32 of 32
  sections; its report moves only in the version string, and the pinned `reportHash` moved to
  `e8bf0580…` (`test/spikes/document-ai-verdict.test.ts`).

Previously produced evidence stays reproducible from its commit (the version names the code),
and none of it is re-evaluated.

**Steps 1–6.** 1: `NORMALIZATION_VERSION` is `fidelity-norm/3.1.0` on both sides. 2:
`npm run contracts:generate` (no drift), `npm run vectors:generate`, `npm run contracts:fixtures`,
`npm run contracts:quote-edge`, `npm run differential:smoke` and `npm run authority:vectors`
regenerated `test/fixtures/fidelity/vectors.json` (591 → 600: XHTML 340 → 349; normalisation 71
and verify 180 unchanged in number), the four contract fixtures, the quote-edge export, the
smoke corpus and the importer's vectors; `npm run authority:lock` added importer 1.1.0. 3: below.
4: nine new XHTML vectors: `t<sub>½</sub>` in a sentence, `&#189;` and `&#xBD;`, `(0-∞)` and
`0&#8211;&#x221E;` among folded signs, ½ after a digit; and still refused: ∞ and `&#189;` in
`sup`, ¼ and U+2189 in `sub`, U+29DC INCOMPLETE INFINITY in `sub`. 5: ADR 0003's residuals name
the two code points; ADR 0005 amended (the two refusals of decision 1 settled, and the survey's
underline finding recorded for PR 3). 6: UR-09 names 3.1.0; `AGENTS.md` names 3.1.0.

**Changed vectors (step 3).** Compared by name with the 3.0.0 vectors: no normalisation or XHTML
vector changed and none was removed; the nine above were added. All 180 verify vectors changed in
exactly three fields and no other: `input.normalizationVersion`, `expected.normalizationVersion`
and `expected.reportHash`. No status, reason, text or coverage figure moved.

**Differential proof.** `scripts/fidelity/differential.ts` gains a class drawing ½, ∞, ¼ and
U+29DC, literal and as references, inside both `sup` and `sub`. TypeScript and Python agree on
all 6000 cases at each of seeds 20260920, 1, 2, 3 and 4 (30 000 cases); across them, ½ or ∞ occurs
inside `sub` 232 times and inside `sup` 227 times, each side reached both accepted and refused.

**Blast radius.** Every approved hash that embeds the version moves (step 7). No accepted text
changes. The newly accepted forms are two code points inside one element.

**Step 7.** Previously approved submissions require re-approval: the version is part of the
approved content hash. The demonstration's approvals are synthetic placeholders and are
re-seeded; their narratives hold neither form.

**Approval (step 8).** Not obtained: author and releaser are the same identity. This change
touches an approved hash, so it needs a quality representative other than the author; until
branch protection requires a second reviewer this is a procedural control only, and the
independent reviews recorded in the design note stand in for it.
