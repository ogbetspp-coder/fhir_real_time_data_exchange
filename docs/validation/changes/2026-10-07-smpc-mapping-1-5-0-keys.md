# Recorded change: two SmPC section keys inside `SourceKey`, SmPC mapping 1.5.0, authority importer 2.4.2, certified Word importer 1.2.1, `dev.khs.fhir.epi#0.7.0`, 2026-10-07

_No contract changes. Found by the leaflet's change (`2026-10-07-leaflet-zone-b.md`, "Found, not
fixed") and fixed the way it fixed the leaflet's keys._

**What changed.**

1. **The SmPC mapping** (`fhir/mappings/cap-smpc-en.json`, `mappingVersion` 1.4.0 → 1.5.0): two
   optional keys renamed, their hyphens removed, as leaflet mapping 1.1.0 removed three:
   `smpc.4.6.breast-feeding` → `smpc.4.6.breastfeeding` (Breast-feeding, 200000029813) and
   `smpc.5.2.pk-pd` → `smpc.5.2.pkpd` (Pharmacokinetic/pharmacodynamic relationship(s),
   200000029833). Their titles, codes, places and cardinality are unchanged; no other rule moved.
   The contract's `SourceKey` (`^[a-z0-9]+(?:\.[a-z0-9]+)*$`) has no hyphen, so no canonical
   submission could carry either section: it was refused at the contract (fail-closed). The
   repository's convention for a key of several words is the words joined (`pl.2.donottake`,
   `pl.3.toomuch`, `pl.6.othersources`).
2. **The StructureMap twin**: the FML's hand-kept list of the canonical keys it maps
   (`fhir/maps/type2-to-ema-cap-smpc-en.map`, the `Section` group's `check`) names the two new keys;
   recompiled by the pinned validator (`npm run map:compile`), version 1.5.0.
3. **Our package, `dev.khs.fhir.epi#0.7.0`** (`npm run artifacts:generate`): the CodeSystem
   `canonical-smpc-sections`, its value set, the ConceptMap and the StructureMap at 1.5.0 with the two
   keys; every other resource changes only in the package version (0.6.0 → 0.7.0). Its SHA-256,
   `fda1d2cc609d46a071c2ef54f1a49919b8a7da1916000ed0a2a03e20b5ade5d5`, is pinned in
   `Dockerfile.validator` and `fhir/standards.lock.json`.
4. **The importers' versions**, their code unchanged: the authority importer 2.4.1 → 2.4.2 and the
   certified Word importer 1.2.0 → 1.2.1. Their golden vectors name the mapping's version (in the
   terminology references and, for the certified Word importer, in the recompute's versions), so
   the vectors moved, and a lock entry main has released is never changed. Every outcome is the
   same; only hashes moved (below).
5. **`createSyntheticSubmission` passes `optional`** to `createSyntheticType2Bundle`
   (`src/fixtures/synthetic-submission.ts`). It took the option by its type and dropped it, so a
   submission with every optional section could not be asked for. Off, every submission is byte
   for byte as before.
6. **Official validation** (`scripts/ci/emit-validation-set.ts`): a fifth case, the drawn SmPC
   (the smoke product) with every optional section of the template, as a document run sends it:
   its record, EMA List, Bundle and Composition, and its Provenance. Its 16 warnings are the SmPC
   fixture case's own, location and message for location and message (`dom-6` on the resources
   that carry no `Resource.text`, and `mg` unchecked with `-tx n/a`), each allowlisted with its
   twin's reason.

**Why.** A submission is the only route that persists a document's content, and none could carry
Breast-feeding or the PK/PD relationship. Widening `SourceKey` would be a major of
`CanonicalSubmission` (taking the 4.0.0 the renderer and withheld designs reserve), of the
fidelity report, the approval contracts, `query-tools` and the run manifest; renaming two keys is a
mapping change.

**Impact assessment (step 0).**

- Crosswalk (`src/fhir/transform.ts`), measured against `origin/main` (`7095947`) by loading both
  manifests: for the four synthetic SmPC products, both versions, mandatory sections only (every
  fixture, the fixture route and every seeded submission), the source SHA-256, the `outputHash` and
  the SHA-256 of the mapping decisions are identical. With every optional section (built only by
  tests and now the validation set), the output differs exactly where the keys do: the two
  sections' ids (`stableUuid("ema-qrd-section", sourceKey)`), their synthetic text (which names
  its key), the decisions' hashes of those sections and their ancestors, and the Composition's
  version identifier, which hashes the Composition.
- Worker, gate and signer: unchanged code. A submission with either section now passes the
  contract where it was refused.
- Query service (`src/query/tools.ts`): its index is built in memory from the SmPC mapping at start
  (titles and section ids by key), nothing stored. A section published under an old key would have
  the old key's section id and would not be found by key after this change; none can exist (below).
- Agent: unaffected (`query-tools` and `agent-turn` unchanged); no old key in `agent/`.
- Zone A: `zone_a.structure` reads the mapping's keys, but looks for required named subsections
  only (both renamed rules are optional), so no structure changes. No Zone A source file changed,
  so no component version moves (`zone-a/versions.lock.json` is unchanged). The QRD check
  (`zone_a.qrd.check`), the product reader (`zone_a.product`) and the scoreboard name no renamed
  key. What moved, regenerated: the five QRD check results (`labels/ema-epi/checks/`) in their
  `mappingVersion` and `mappingSha256` only, every finding and status unchanged; the certified Word
  recompute fixtures (`test/fixtures/certified-word/recompute/`) in `versions.mappingVersion`
  only, byte for byte otherwise; `qrd/registry/cap-smpc-en-10.4.json` is unchanged (it is built
  without the mapping). The recompute's versions name the mapping's, so a request made with 1.4.0
  is refused (`versions`).
- Evidence: `test/fixtures/contracts/{canonical-submission,fidelity-report,source-document-text}-type1.json`
  move with the authority importer's version and the mapping version in its terminology
  references. `contracts/generated/**`, `contracts/versions.lock.json`, the fidelity vectors, the
  differential corpus, the drawn contract fixtures and the frozen run manifests
  (`test/fixtures/run-manifest/`) are unchanged.
- Stored or published uses of the old keys, checked read-only. In the repository: none outside the
  mapping, the generated artefacts, the FML and the leaflet's test and records (`git grep`), none
  in `agent/`, the contract fixtures, the frozen run manifests or the dev and demo seeding scripts
  (`scripts/dev/`, `scripts/demo/`), which build mandatory sections only, as the fixture route
  does (`src/app.ts`). By construction none can be stored: a document run with either key was
  refused at the contract; an authority import and a certified Word import run dry; the
  certified Word structurer never finds an optional subsection; the fixture route builds none; the
  stored demonstration documents were seeded on 2026-09-21, before mapping 1.4.0 mapped optional
  sections. Not checked in the cloud: the dev FHIR store and BigQuery could not be read from this
  machine (gcloud needs a fresh login, and the BigQuery connection is unauthorised), so that claim
  rests on the routes above, not on a query.

**Steps 1–7.** 1: mapping 1.5.0, the package 0.7.0, the authority importer 2.4.2 and the certified
Word importer 1.2.1 (no contract version). 2: `npm run map:compile`, `npm run artifacts:generate`,
`npm run contracts:fixtures`, `npm run authority:vectors`, `npm run certified-word:vectors`,
`npm run authority:lock`, `npm run certified-word:lock`; in `zone-a/`,
`scripts/certified_word_fixtures.py` and `scripts/check_labels.py`;
`scripts/generate_qrd_registry.py --check` and `scripts/lock_versions.py --check` report both
current. `npm run contracts:check` reports every generated file current. 3: every changed vector
reviewed: the authority importer's synthetic import (its submission, page text and report hashes;
every refusal unchanged), the certified Word importer's three SmPC imports (their recompute result,
submission, page text and report hashes; outcomes unchanged), its leaflet import (its submission,
page text and report hashes, by the importer's version in the extractor; its recompute result
unchanged) and six SmPC refusals (their recompute result's hash only); its other four refusals are
unchanged. 4: tests below. 5: ADR 0002's leaflet amendment says the finding is fixed. 6: UR-46.
7: a certified Word or authority submission made by the earlier importer versions or mapping 1.4.0
is refused by the gate (another importer version, other recompute versions) and must be made
again; none was persisted (dry run only). A drawn submission's approved content does not name the
mapping version, and no drawn fixture's approved hash moved.

**Tests.** `test/leaflet.test.ts` ("the canonical section keys": every key of every manifest in
`fhir/mappings/`, its rules' and its unmapped slots', is a `SourceKey`; it listed the two SmPC
keys before); `test/pipeline.test.ts` ("publishes an approved submission with every optional
section of the template": the synthetic SmPC with all 59 sections, Breast-feeding and the PK/PD
relationship among them, through the gate and the crosswalk in a dry run, 59 decisions, the
fixture route's `outputHash`); both fail with the old keys (run: the first lists the two keys, the
second is refused `Canonical submission is invalid`). `test/ci/validation-set.test.ts` (the fifth
case is the pipeline's targets); `test/standards-lock.test.ts`; `test/authority/gate.test.ts` and
`test/authority/pipeline.test.ts` (the importer version); `test/official/structuremap-twin.test.ts`
(the twin and the crosswalk agree on every optional section, the renamed two included).

**Measured** (2026-10-07, this branch): `npm run validate:official`: 0 errors across 70
resources, 65 warnings, every one allowlisted, none stale (before: 0 errors across 65 resources, 49
warnings). `npm run test:official`: 17 tests passed.

**Blast radius.** Synthetic content only. A submission with Breast-feeding or the PK/PD
relationship is now accepted where it was refused; nothing that was accepted is refused, and every
mandatory-section output is byte for byte as before. Certified Word and authority submissions made
before this change are refused by version, as with every importer version.

**Approval (step 8).** Not obtained: the author and the releaser are the same identity (see
"Release criteria").
