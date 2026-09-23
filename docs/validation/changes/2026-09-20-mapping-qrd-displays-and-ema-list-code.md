# Recorded change: `fhir/mappings/cap-smpc-en.json` QRD coding displays and EMA List code, 2026-09-20

_Moved verbatim from `docs/validation/README.md`, whose "Change control for shared, evidenced
libraries" section holds the procedure (steps 0–8) this record follows. The UR- rows and
section names it cites ("above", "Release criteria") are in that file._

**What changed.** Three things, one in the manifest, one in the transform, one in the synthetic
fixture the demonstration runs on.

1. The manifest's `mappingVersion` moved from `1.0.0` to `1.1.0`, and two section rules gained a
   `display` field: `smpc.6.5` (`200000029841`) and `smpc.6.6` (`200000029842`). `display` is the
   EMA QRD code system's own string for the code and is what the transform now writes into
   `Composition.section.code.coding.display` (`rule.display ?? rule.title`, in
   `src/fhir/transform.ts` and in the ConceptMap generator `scripts/fhir/generate-artifacts.ts`).
   The `title` of every rule is unchanged, so `Composition.section.title` — the heading the label
   carries — is unchanged in both the source and the EMA output. The loader `src/fhir/mapping.ts`
   accepts the optional field; the other thirty rules' titles already equal the code system's
   displays, which `test/type2-conformance.test.ts` now asserts for all thirty-two against
   `test/fixtures/terminology/ema-displays.json`, a verbatim extract of the two EMA code systems
   from the pinned `EUePI#1.0.0` package. The split follows the EMA EPI-23-1022 English sample
   (`fhir/standards.lock.json`), whose section 6.6 is titled "6.6 Special precautions for
   disposal" while its coding display is "6.6 Special precautions for disposal [and other
   handling]".
2. `src/fhir/transform.ts` codes the EMA List with `100000155539` "Combined File of all
   Documents" instead of `100000155527` "ePI Master List". The EMA Document Type code system
   `http://ema.europa.eu/fhir/CodeSystem/100000155531` has no `100000155527`; `100000155539` is
   its concept for the whole set of a product's documents, and the EMA sample's own List carries
   exactly this coding.
3. `src/fixtures/synthetic.ts` and `src/fixtures/synthetic-products.ts`: the Type 2 Bundle now
   declares `language`, every product-graph entry declares its Global ePI profile in
   `meta.profile`, the Organization, ManufacturedItemDefinition, AdministrableProductDefinition
   and PackagedProductDefinition (resource and `packaging`) carry invented identifiers under
   `https://khs.dev/fhir/identifier/...`, the package has a name, the package's
   `packaging.containedItem` names the manufactured item and the administrable product is
   `producedFrom` it, and the empty `MedicinalProductDefinition.name.type.coding` array is gone.
   No narrative, no id, no Bundle identifier and no date changed.

**Why.** The official HL7 validator (`validator_cli.jar` 6.10.4 with the four packages pinned in
`Dockerfile.validator`), run over the four resources a fixture run sends the worker, reported 20
distinct errors, and the deployed worker had therefore never completed a run. The three mapping
errors, quoted:

- `Wrong Display Name '6.5 Nature and contents of container and special equipment for use,
administration or implantation' for http://ema.europa.eu/fhir/CodeSystem/200000029659#200000029841.
Valid display is one of 2 choices: '6.5 Nature and contents of container [and special
equipment for use, administration or implantation]' or ... (en)`
- `Wrong Display Name '6.6 Special precautions for disposal and other handling' for
http://ema.europa.eu/fhir/CodeSystem/200000029659#200000029842. Valid display is one of 2
choices: '6.6 Special precautions for disposal [and other handling]' or ... (en)`
- `Unknown code '100000155527' in the CodeSystem 'http://ema.europa.eu/fhir/CodeSystem/100000155531'
version '1.0.0'`

The fixture errors were `Bundle.language: minimum required = 1, but only found 0`,
`Organization.identifier: minimum required = 1, but only found 0`,
`PackagedProductDefinition.name: minimum required = 1, but only found 0`,
`PackagedProductDefinition.packaging.identifier: minimum required = 1, but only found 0`,
`ManufacturedItemDefinition.identifier: minimum required = 1, but only found 0`,
`AdministrableProductDefinition.identifier: minimum required = 1, but only found 0`,
`Unable to find a profile match for https://khs.dev/fhir/Organization/synthetic-pharma among
choices: ...Organization-uv-epi` (twice: `Composition.author[0]` and
`RegulatedAuthorization.holder`), `Unable to find a profile match for
https://khs.dev/fhir/ManufacturedItemDefinition/synthetic-tablet among choices: ...` on
`Ingredient.for[0]`, `Array cannot be empty - the property should not be present if it has no
values` on `MedicinalProductDefinition.name[0].type.coding`, and `Entry '...' isn't reachable by
traversing links (forward or backward) from the Composition` for the ManufacturedItemDefinition,
the Ingredient and the SubstanceDefinition, in both the source and the EMA Bundle. After the
change all four validations report `Success: 0 errors` (source Type 2 Bundle, EMA List, EMA
document Bundle, EMA Composition against its four profiles).

**Impact assessment (step 0).** Importers of the mapping: the worker (`src/app.ts`,
`src/pipeline.ts` through `src/fhir/transform.ts` and `src/fhir/preflight.ts`), the fixtures
(`src/fixtures/*`), the artifact generator, the contract-fixture exporter and the Document AI
spike scripts. The Python agent and Zone A read only the exported fixtures. What moved:

- `test/fixtures/contracts/canonical-submission.json`: `bundleSha256`
  `8891a69b297b5bf2f054684102273866ef91215b743ca2a82d8630a15ef9aa6a` →
  `e95421e1d5de87f5e637edb5900487ab0bd5cc6942e0861c629d129f03836490` and
  `approvedContentSha256`
  `203155ef2032bc13f15f1c33e89c598a2078e410e956bc07d9d232be3a48ecac` →
  `350d284889933aed5a835a77e0060c82f8b90382f098f6a61c5401759a9a5027`, because the Bundle's
  product graph is part of the approved content; `test/fixtures/contracts/run-request.json`:
  `sha256` `f07f2d19333a435863a9e3f8fce0fd3b371f1b85488bb03339dfd9803452db5e` →
  `90b8d3a123938bddb0ea8dd88ccbd5248940e70e054077cccf822333eea9abf2`.
- `fhir/generated/ConceptMap-canonical-to-ema-cap-smpc-en.json`: the two target displays and the
  version.
- The transform's `outputHash` for every synthetic product (not pinned anywhere; the
  `transform.test.ts` determinism test compares two runs, not a literal).
- Unchanged, byte for byte: `test/fixtures/contracts/fidelity-report.json`,
  `test/fixtures/contracts/source-document-text.json`, `test/fixtures/fidelity/vectors.json`,
  `zone-a/tests/fixtures/differential-smoke.jsonl` — every hash that is computed over narrative
  or extracted text is the same as before, which is the first proof that no narrative changed.
- The second proof is a test: `test/fixtures/narrative/section-divs.json` holds every QRD
  section `div` of every product and version (3 × 2 × 32), captured from the tree at commit
  `1b58a79` before any of this change, and `test/type2-conformance.test.ts` "carries
  byte-identical section divs, source and EMA target, for every product and version" compares
  every source div and every EMA target div against it.
- No literal pin in `test/**` changed: the decision count is still 32, the section count 32,
  the page count 3. The Document AI spike (`test/spikes/document-ai-verdict.test.ts`) still
  replays its recorded response against the regenerated PDF unchanged, which is what fixed the
  design: a first attempt that changed the two rule titles broke it, because the PDF headings
  are the titles, and the recorded verdict's numbers are evidence, not pins.
- EMA document Bundle ids are unchanged, because `Bundle.identifier.value` is unchanged:
  `0c18c50e-a284-5d5c-a570-ba8519726c75` (paracetamol),
  `2ee34ea0-41f7-587c-a373-0891d915192e` (demoxetine),
  `a5363206-eb44-5bed-a6e4-b109fd539d66` (placebolol). The deployed entitlement map keyed by
  them needs no change. The Type 2 Bundle ids are unchanged too.
- Step 7 (re-approval): `approvedContentSha256` moved for every synthetic submission, so any
  synthetic submission seeded before this change would be rejected by the ingress gate and has to
  be re-seeded (`scripts/demo/*` recompute it). No document has ever been persisted by the
  deployed pipeline, so nothing in a store needs re-approval. Zone A's parity suite was run
  against the regenerated fixtures (221 passed, 1 skipped) and `npm run check` is green.

**Blast radius.** A real label's Composition now carries the code system's display on 6.5 and
6.6 and its own heading in `title`; a consumer that read `coding.display` as the heading will see
the bracketed form. The List code changes the meaning recorded on every future List from an
undefined code to "Combined File of all Documents"; nothing persisted carries the old one.

**Approval (step 8).** Not obtained: author and releaser are the same identity. Branch protection
on `main` does exist and requires status checks, but it requires no reviewer, so nothing forces a
second pair of eyes (see "Release criteria").
