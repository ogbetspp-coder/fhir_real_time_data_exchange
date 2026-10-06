# Recorded change: `fidelity-norm/3.1.0` → `fidelity-norm/3.2.0`, 2026-10-05

_Follows the procedure (steps 0–8) in `docs/validation/README.md`, "Change control for shared,
evidenced libraries". The UR- rows it cites are in that file._

**What changed.** `NORMALIZATION_VERSION` moved from `fidelity-norm/3.1.0` to
`fidelity-norm/3.2.0` in `src/fidelity/normalize.ts`, `zone-a/src/zone_a/fidelity/normalize.py`
and the agent's `quote_edge.NORMALIZATION_VERSION`. In `docs/fidelity-normalization.md`, section 7
qualifies a third kind of source, a certified Word source (ADR 0006), and states its page rule (with the owner's decision of 2026-10-06: a tab after a typed bullet is written as a space);
section 8's minor increment gains the case of a newly qualified source; section 9 has an entry.
Sections 1 to 6 are unchanged: no normalisation, scanning or verification rule moved, and the code
point table is the one 3.1.0 was released with. The authority importer moves to 2.3.1
(`IMPORTER_VERSION`, `src/authority/importer.lock.json`), because its golden vectors carry the
version. 3.2.0 was first reserved for the withheld section of an authority import
(`docs/design/authority-import-withheld.md`, decided, not built), which now takes 3.3.0; ADR 0002,
ADR 0003 and that note say so.

**Why.** ADR 0006 (accepted, owner decisions of 2026-10-05) imports a company's Word SmPC. Its
decision 1 trusts such a source by the label reader's exact read, Zone B's recompute and a drawing
check, and its decision 2 asks for the page text to be defined exactly in section 7. Without a
version, `fidelity-norm/3.1.0` would name two contracts: one that qualifies no Word document and
one that qualifies this kind.

**Impact assessment (step 0).** Importers of `src/fidelity/`:

- **Worker gate** (`src/pipeline.ts`, `src/contracts/canonical-submission.ts`): refuses a
  submission or report that names another version. A 3.1.0 submission is refused, not
  re-evaluated. The producers in the repository take the version from the constant. No gate admits
  a certified Word source yet (ADR 0006 P4, `docs/design/certified-word-import.md`).
- **Authority importer** (`src/authority/`): unchanged in what it does; 2.3.1, its vectors moving
  only in the version string and the hashes that embed it.
- **Query service** (`src/query/tools.ts`): `get_section` and `verify_quote` report the new
  version. Every stored narrative normalises to the same text.
- **Agent** (`agent/`): its post-check accepts answers under one version, so its constant moves
  in this change. The quote-edge rule it ports is unchanged. Deploy order (its README): the agent
  on Agent Engine is redeployed **before** the query service answers under 3.2.0; until then
  every block is flagged `checksum-mismatch`.
- **Zone A** (`zone-a/`): the port's constant moves; the components locked to
  `fidelity/normalize.py` move a patch version.

**Steps 1–6.** 1: `NORMALIZATION_VERSION` is `fidelity-norm/3.2.0` everywhere it is held. 2:
`npm run contracts:generate` (no drift), `npm run vectors:generate`, `npm run contracts:fixtures`,
`npm run contracts:quote-edge`, `npm run differential:smoke` and `npm run authority:vectors`
regenerated `test/fixtures/fidelity/vectors.json` (634 → 638: verify 180 → 184; normalisation 71
and XHTML 383 unchanged), the contract fixtures, the quote-edge export, the smoke corpus and the
importer's vectors; `npm run authority:lock` added importer 2.3.1. 3: below. 4: four verify vectors
for the certified Word rule, reviewed by hand (`test/fixtures/fidelity/cases.ts`): a section's page
as `zone_a.word_epi` writes it against the narrative it builds from the same read (passed: list
labels, a raised 9 and a lowered 0, minus and ∞, a bullet in a cell left out, a colspan and a
rowspan as grid slots), and the same page against that narrative with its merged cell unmerged or
its list numbered from one later, and a page writing a bullet after a line break as content
against a narrative drawing it after `br` (each failed). The code point table's test records 3.2.0
with 3.1.0's hash. 5: ADR 0002, 0003 and 0006 amended as above. 6: UR-09 names 3.2.0; `AGENTS.md`
names 3.2.0 and the certified Word source.

**Changed vectors (step 3).** Compared by name with the 3.1.0 vectors: no normalisation or XHTML
vector changed and none was removed; the four above were added. Every other verify vector changed
in exactly three fields and no other: `input.normalizationVersion`,
`expected.normalizationVersion` and `expected.reportHash`. No status, reason, text or coverage
figure moved.

**Differential proof.** No rule of sections 2 to 6 changed, so the differential corpus moves only
in the version string and the hashes that embed it; TypeScript and Python agree on it as before
(`zone-a/tests/test_differential.py`, the smoke corpus regenerated).

**Blast radius.** Every approved hash that embeds the version moves (step 7). No accepted text
changes.

**Step 7.** Previously approved submissions require re-approval: the version is part of the
approved content hash. The demonstration's approvals are synthetic placeholders and are
re-seeded.

**Approval (step 8).** Not obtained: author and releaser are the same identity. This change
touches an approved hash, so it needs a quality representative other than the author.
