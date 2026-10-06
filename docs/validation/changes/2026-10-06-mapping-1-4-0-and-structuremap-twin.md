# Recorded change: every section of the CAP SmPC template mapped (mapping 1.4.0, importer 2.4.0), and the StructureMap as an executed twin, 2026-10-06

**What changed.**

1. **The mapping manifest** (`fhir/mappings/cap-smpc-en.json`, `mappingVersion` 1.3.0 → 1.4.0)
   has a rule for each of the 59 sections of the EMA profile `EUQRD-CAP-template-new-SmPC-en`
   (EUePI 1.0.0), up from 32. The 27 new rules are optional, as the profile makes them (`min` 0):
   2.1, 2.2 and "Excipient(s) with known effect" under 2; the paediatric subsections of Posology,
   4.4, 4.5, 4.8, 4.9 and 5.1, and 6.6's "Use in the paediatric population"; Precautions under
   Method of administration; Traceability; Pregnancy, Breast-feeding and Fertility; the
   subsections of 5.1, 5.2 and 5.3; 11 and 12. Each rule's title and its place in the tree are the
   profile's. A new `unmapped` list names the profile's 44 custom subsection slots (two codes,
   200000044333 at level H4 and 200000044347 at H5) with keys `smpc.custom.h4` and `smpc.custom.h5`
   and the reason: such a subsection carries its author's heading and may repeat, and the crosswalk
   carries the template's own sections only, each once.
2. **The loader** (`src/fhir/mapping.ts`) reads the `unmapped` list and refuses a key or code that
   a rule and an unmapped slot share.
3. **The crosswalk** (`src/fhir/transform.ts`) holds a mapped section to carry narrative unless it
   is a bare heading over subsections the source has under it (and its rule is not marked
   `"narrative": "required"`). Before, only a mandatory leaf was held to it; with optional children,
   4.6 with no Pregnancy subsection would otherwise have published empty. An optional section is
   held to it when present. A decision's `targetPath` counts only the children mapped, so an absent
   optional rule leaves no gap.
4. **The importer** (`IMPORTER_VERSION` 2.3.2 → 2.4.0): an EMA document's section tree must be the
   manifest's, in order, where a rule that is not required may have no section. It used to require
   one section per rule.
5. **The synthetic fixtures** and the synthetic publication have the mandatory sections only, as
   before; `createSyntheticType2Bundle(mapping, { optional: true })` adds every optional one.
6. **Our package** `dev.khs.fhir.epi` 0.1.0 → 0.2.0: the CodeSystem has the 59 keys and the two
   unmapped ones (each with its reason as `definition`); the ConceptMap maps the 59 `equivalent`
   and the two `noMap`; the StructureMap is replaced (item 7). Its SHA-256,
   `4e597ba97dfeb4170e649b99affba63337e7980f3c7f9f6c804b5ac45828999a`, is pinned in
   `Dockerfile.validator` and `fhir/standards.lock.json`.
7. **The StructureMap** is no longer a two-rule summary that nothing ran. It is the crosswalk
   written in FML (`fhir/maps/type2-to-ema-cap-smpc-en.map`), compiled by the pinned validator
   (`npm run map:compile`) and run on its engine by `fhir/maps/TwinRunner.java` against the
   crosswalk on 168 cases in CI's Official validation job (`npm run test:official`).
   `docs/design/structuremap-twin.md` says what it compares and what it cannot express.
8. **Zone A**: the QRD check (`qrd-check/1.6.0`) checks 2.1 and 2.2's statements in their own
   section where the document has it, and in section 2 otherwise, as before; the structurer
   (`smpc-structure/1.2.0`) does not look for the optional named subsections, which stay text of
   their section. `word-epi/1.3.1`, `word-drawing/1.1.1` and `product/1.0.8` move because their
   locked files did.

9. **The secret scan** has one more reviewed exception (`.gitleaks.toml`): gitleaks'
   `generic-api-key` read eleven of the manifest's new `sourceKey` values as credentials. It
   covers that rule, that file and values of the key's shape (`smpc` and dotted lower-case parts)
   only.

**Why.** Real EMA ePIs use codes the mapping lacked (Jentadueto's 4.6 Pregnancy, Breast-feeding
and Fertility), and the published StructureMap said it was never executed.

**Impact assessment (step 0).**

- Crosswalk, measured against `origin/main` (`73e338e`) for the four synthetic products at both
  versions: the source SHA-256, the `outputHash` and the SHA-256 of the mapping decisions are
  identical. No document the pipeline has published or would publish from those sources changes.
- Importer: the synthetic import's vector changes in its hashes only (the extractor name and the
  mapping version in its terminology references); every refusal of the other ten vectors, the five
  EMA labels among them, is unchanged. `test/fixtures/contracts/*-type1.json` move with it.
  `contracts/generated/**`, `test/fixtures/fidelity/vectors.json` and
  `zone-a/tests/fixtures/differential-smoke.jsonl` are unchanged.
- QRD check results (`labels/ema-epi/checks/`): Jentadueto's three `unmapped-code` findings are
  gone (3 → 0; no pinned label has one); `smpc.12#0` is `not-checked` for `section-absent` instead
  of `section-not-mapped` in all five; every other finding and status is unchanged.
- Official validation, run locally 2026-10-06: 0 errors on 21 resources, 65 warnings, all
  allowlisted; the two new ones are the validator's "Transform translate/c not checked yet" on the
  StructureMap, reviewed.
- Step 7 (re-approval): `mappingVersion` is not part of a drawn submission's approved content,
  and no drawn fixture's approved hash moved. An authority import's submission names the importer
  version and the mapping version, so, as with every importer version, one made by 2.3.2 is not
  the submission the gate recomputes under 2.4.0 (imports run dry today).

**Blast radius.** A source that the previous crosswalk accepted and this one refuses: a mapped
section with child rules, none of whose subsections the source has, without narrative (4.4, 4.5,
4.6, 4.9, 5.1, 5.2, 5.3, 6.6 and 2 now have child rules). The synthetic fixtures and the import
meet it. A source this one accepts that the previous refused: one with optional sections. An EMA
document with an optional section the template allows now passes the importer's tree check.

**Tests.** `test/official/profile-slots.test.ts` (the profile read from the pinned EUePI package:
103 slots, 59 mapped with title, place, order and cardinality, 44 custom and unmapped, none
neither; it fails when a rule or an unmapped entry is removed); `test/official/structuremap-twin.test.ts`
(the compiled map is the committed one; 156 fixtures, 155 equal and one the reviewed difference; 7 refused by both, the 5 labels refused
by the crosswalk and refused or carried byte for byte by the twin; it fails when the crosswalk's QRD
template version is changed); `test/transform.test.ts`, `test/mapping.test.ts`,
`test/preflight.test.ts`, `test/fhir-artifacts.test.ts`, `test/type2-conformance.test.ts`,
`test/authority/import.test.ts`, `zone-a/tests/test_qrd_check.py`, `test_qrd_registry.py`,
`test_structure.py`.

**Approval (step 8).** Not obtained: the author and the releaser are the same identity (see
"Release criteria").
