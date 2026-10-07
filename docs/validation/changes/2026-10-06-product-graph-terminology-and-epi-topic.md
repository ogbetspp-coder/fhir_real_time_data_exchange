# Recorded change: the product graph's terminology and the `epi-published` SubscriptionTopic, `dev.khs.fhir.epi#0.5.0`, 2026-10-06

**What changed.**

1. **Every product-graph coding names its system.** `src/fixtures/synthetic.ts` coded twelve
   elements with a code or a display and no system, which the official validator cannot check.
   Each now carries the system its element is bound to, a code, and that system's display:

   | Element                                                                                                                       | System                                         | Code               | Display             |
   | ----------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | ------------------ | ------------------- |
   | `MedicinalProductDefinition.type`                                                                                             | `http://hl7.org/fhir/medicinal-product-type`   | `MedicinalProduct` | `Medicinal Product` |
   | `MedicinalProductDefinition.domain`                                                                                           | `http://hl7.org/fhir/medicinal-product-domain` | `Human`            | `Human use`         |
   | `status` of MedicinalProductDefinition, RegulatedAuthorization and SubstanceDefinition, and `SubstanceDefinition.name.status` | `http://hl7.org/fhir/publication-status`       | `active`           | `Active`            |
   | `PackagedProductDefinition.packaging.type`                                                                                    | `http://hl7.org/fhir/packaging-type`           | `100000073498`     | `Box`               |
   | `ManufacturedItemDefinition.manufacturedDoseForm`                                                                             | `http://hl7.org/fhir/manufactured-dose-form`   | `100000073664`     | `Tablet`            |
   | `ManufacturedItemDefinition.unitOfPresentation`                                                                               | `http://hl7.org/fhir/unit-of-presentation`     | `200000002152`     | `Tablet`            |
   | `AdministrableProductDefinition.administrableDoseForm`                                                                        | `http://hl7.org/fhir/administrable-dose-form`  | `100000073664`     | `Tablet`            |
   | `AdministrableProductDefinition.routeOfAdministration.code`                                                                   | `http://snomed.info/sct`                       | `26643006`         | `Oral route`        |
   | `Ingredient.role`                                                                                                             | `http://hl7.org/fhir/ingredient-role`          | `100000072072`     | `Active`            |

   The bindings are the pinned Global ePI profiles' (`hl7.fhir.uv.emedicinal-product-info#1.0.0`):
   its own value sets for the dose forms, unit of presentation, route and ingredient role, each a
   whole system; base R5's for type, domain, status and packaging type. The pinned EMA package
   (`EUePI#1.0.0`) profiles none of these resources. Every system but SNOMED CT is in
   `hl7.fhir.r5.core#5.0.0` with `content` `complete`, and each code and display is that code
   system's. The route's code and display are the ones the Global ePI package's own example uses
   (`package/example/Bundle-bundlepackageleaflet75type2.json`). The example codes the other
   elements in the same systems too, with two differences: its package type is in
   `medicinal-product-package-type`, not the bound `packaging-type`, and its substance name has no
   status.

   Two values changed meaning, not only form: the package type was the display "Carton", which
   `packaging-type` does not hold (it holds "Box"), and the substance name's status was `current`,
   which `publication-status` does not hold.

2. **The allowlist shrinks from 75 entries to 41.** The 24 "Coding has no system" warnings and the
   12 `cod-1` warnings (a display with no code), on the source Type 2 Bundle and its copy in the
   EMA Bundle, are gone, and so are their entries. `test/ci/validator-pins.test.ts` refuses either
   warning in the allowlist again. Two entries are added, for the SubscriptionTopic (item 4).

3. **The systems are classified.** `test/type2-conformance.test.ts` finds every Coding-shaped
   object of every synthetic product and version, wherever it sits (a `coding` array, `meta.tag`, an
   extension's `valueCoding`, a Quantity): any object with a string `system`, `code` or `display`,
   but a Reference or an Identifier. Each must name a system and a code in R5's form (not empty),
   and every system must be in one of two reviewed lists: checked offline (a complete code system in
   a pinned package), or not (UCUM and SNOMED CT). A new system fails until it is put in one. Since
   the validator says nothing about SNOMED CT, `test/official/route-coding.test.ts` holds the
   route's coding to the Global ePI example's, read from the pinned package.

4. **`https://khs.dev/fhir/SubscriptionTopic/epi-published`**, generated by
   `scripts/fhir/generate-artifacts.ts`: an ePI document was published or superseded. Its trigger
   is a `Bundle` of `type` `document`, created or updated; its notification shape is the Bundle.
   `docs/design/epi-published-notifications.md` is the consumer contract: what the validated
   store's Pub/Sub message holds, from Google's documentation, and what a consumer must do.
   `test/fhir-artifacts.test.ts` holds the topic to the transaction a run writes (one document
   Bundle, by `PUT`, whether the store held a version or not) and the store reconciler to names
   only (`sendFullResource` and `sendPreviousResourceOnDelete` false, on the validated store
   alone). The reconciler now sets `notificationConfigs` on the source store too, to none, so a
   notification added there by hand (the source store holds narrative) is removed by the next
   deploy; `dev`'s source store had none on 2026-10-06, so this changes nothing there. R5 defines the `resource` element as a URL relative to
   `http://hl7.org/fhir/StructureDefinition/`, so the topic writes `Bundle`, which is in the
   element's extensible value set (`subscription-types`, through `resource-types`). The validator
   still warns on both elements, and its own message shows why: "Cannot invoke
   "String.equals(Object)" because "system" is null". The absolute URL warns too, as outside the
   value set. The two warnings are allowlisted as a validator defect.

5. **The package is `dev.khs.fhir.epi#0.5.0`**, re-pinned in `Dockerfile.validator` and
   `fhir/standards.lock.json`. The resources versioned with the package move to `0.5.0`; no
   code, display or canonical URL changed. `fhir/generated/` is now in `.prettierignore`:
   Prettier puts the topic's short arrays on one line, and `npm run artifacts:check` holds the
   generated files to the generator's bytes.

**Why.** Official validation runs with `-tx n/a`. A coding without a system is never checked,
and the gate accepted 36 warnings saying so.

**What the pinned validator checks offline**, measured 2026-10-06 (validator 6.10.4, the five
packages, the sidecar's flags), one test resource per case:

- a code in a complete code system (the three EMA lists in `EUePI#1.0.0`, base R5's, our own): an
  unknown code is an error, a wrong display is an error;
- UCUM: not checked; a valid `mg` and an invalid `mgxq` get the same warning;
- SNOMED CT: not checked, and no message at any level for a valid or an invalid code.

A copy of the source graph with every code altered gave an "Unknown code" error on each of the
eleven codings outside SNOMED CT and none on the route; a copy with every display altered gave
"Wrong Display Name" on the same eleven and none on the route. `docs/design/terminology-server.md`
records the rest and proposes a local server for UCUM (and SNOMED CT, if licensed).

**Impact assessment (step 0).**

- Official validation: on `main` at `a57f545` (CI run 37566175271), 0 errors across 56 resources
  and 75 warnings, all allowlisted. After, run locally 2026-10-06 with `npm run validate:official`:
  0 errors across 57 resources and 41 warnings, all allowlisted, 0 stale (75, less the 36, plus
  the SubscriptionTopic's two). `npm run test:official` passed: 13 tests, the route test with
  them.
- The target store's `$validate` (dev, 2026-10-06, nothing written) gave the new source and EMA
  Bundles no error, as before. It gave none for the copy with every code altered either: the store
  does not check these codes, and the official validator is the only gate that does.
- Contract fixtures, regenerated by `npm run contracts:check`'s generators. The product graph is
  part of the approved content:
  - `test/fixtures/contracts/canonical-submission.json` (`CanonicalSubmission` 3.0.0):
    `bundleSha256` `e95421e1d5de87f5e637edb5900487ab0bd5cc6942e0861c629d129f03836490` →
    `4f9ae27c77f98bcde60835e31729f06cb359e7596f7101224035572bca3f5739`; `approvedContentSha256`
    `bee82adb2ad640c6d397360e4110a8fc9eae53c05803476397692748a6663164` →
    `3fce4206000da81cac491f1dee73c6abb6200367fde5681d7b361e308165daaf`;
  - `canonical-submission-decimal.json`: `bundleSha256`
    `c2a682feaca2b1506040d0239c11ac7997701d1c65ca9ec1ea6246dc20aee4ed` →
    `d0f557f72e980d4d7206cb61bdb0eb1cc9d5a8eab20f19cc41eea5261a8f142e`; `approvedContentSha256`
    `4ea279ddf4e8c032f1321358e5fb7964e8e4c7c14e35a2f353301373e601b628` →
    `4672d4832fde22779adaf1fea857835f02b388ea0e62f0c021f9df46745f707e`;
  - `run-request.json`: `sha256`
    `6d1fcb86e29979f20c1a00797c7de5cbfbd75c61102662b4278691abb31e2a9c` →
    `1e8effe488c11c9849346ba580dd021149feb7b612aa8606eb7c67a001c6cf58`;
  - the two `contract-verdicts.json` cases that quote the `bundleSha256` and the run request's hash.

  No other committed file holds any of the old values but the frozen run manifests under
  `test/fixtures/run-manifest/` and earlier change records. The run manifests are not re-emitted,
  6.0.0's included: each is the evidence of what its version's code wrote, "never regenerated"
  (`test/contracts/run-manifest-frozen.test.ts`). So the 6.0.0 manifests, emitted at `a57f545`,
  keep that commit's `dev.khs.fhir.epi#0.4.0` and its hashes, as 5.0.0's kept theirs when this
  package first appeared.

- Unchanged: every narrative div (`test/type2-conformance.test.ts`, against
  `test/fixtures/narrative/section-divs.json`), every id and identifier, so every EMA document
  Bundle id; the fidelity vectors, fidelity reports, page text, the importer's vectors and the
  differential corpus; the mapping (its output for a given input is unchanged); no contract
  schema or version.
- Step 7 (re-approval): `approvedContentSha256` moved for every synthetic submission. The demo
  submissions seeded in `dev` were made before this change; seeding again writes new versions of
  the same demo documents. The deploy's smoke run writes the smoke product with the new codings,
  a new version of its document Bundle.

**Blast radius.** Synthetic content only. A real label's product graph is not built by this code.
The validator loads one more resource from our package.

**Approval (step 8).** Not obtained: the author and the releaser are the same identity (see
"Release criteria").
