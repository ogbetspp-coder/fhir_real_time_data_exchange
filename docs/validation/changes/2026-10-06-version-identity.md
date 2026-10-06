# Recorded change: an ePI's versions, the stored-version precondition, EU numbers, `dev.khs.fhir.epi#0.3.0`, 2026-10-06

**What changed.** The design is `docs/design/version-identity.md`, which quotes the pinned
definitions it rests on.

1. **The EMA Composition's identifier names one version** (`src/fhir/transform.ts`). It was
   `https://khs.dev/fhir/identifier/ema-composition` with the Composition's id, the same for every
   version; it is `https://khs.dev/fhir/identifier/ema-composition-version` with
   `stableUuid("ema-composition-version", <source identifier> + ":" + sha256(<the Composition
without its identifier>))`, as Composition-uv-epi and EUEpiComposition define it. Every
   persisted id, `Bundle.identifier`, `Bundle.timestamp` and the List's identifier are unchanged. The
   StructureMap twin (`fhir/maps/type2-to-ema-cap-smpc-en.map`, recompiled) writes the new system.
2. **The synthetic version 2** has its own `Composition.identifier` (`…-v2`) and
   `Composition.date` (2026-09-20). Version 1, the frozen fixture, is unchanged byte for byte.
3. **The persist transaction carries a precondition** (`src/gcp/healthcare.ts`,
   `src/pipeline.ts`): the run reads the target store's version of its document Bundle before it
   signs, and the Bundle's entry carries `ifMatch: W/"<versionId>"`, or `ifNoneMatch: "*"` when the
   store answers 404. `buildPersistTransaction` takes the stored version as a required argument.
   A read refused otherwise is `healthcare-read-target-refused`; a resource without a version is
   `target-version-missing`.
4. **EU numbers** (`src/fhir/standards.ts`, `src/fhir/preflight.ts`, `src/fhir/transform.ts`):
   the systems `eu-authorisation-number` (`EU/1/YY/NNN/PPP`) and `eu-product-number`
   (`EU/1/YY/NNN`); one RegulatedAuthorization per authorisation number, the product numbers
   exactly theirs, in both graphs' preflights; a Type 1 record may have several
   RegulatedAuthorizations; the List takes their holder, regulator and procedure only when all
   state the same, and writes `ext-epi-eu-number` for the product's one EU product number.
5. **`dev.khs.fhir.epi#0.3.0`**: 26 NamingSystems (every identifier system the pipeline and its
   fixtures write; `ema-composition` retired) and the profile `eu-product-identity` with the
   invariants `khs-eu-1` to `khs-eu-4`. The code systems, value sets and extension take the new
   version. The archive's SHA-256 is re-pinned in `Dockerfile.validator` and
   `fhir/standards.lock.json`. `officialValidationTargets` validates the source and the EMA Bundle
   against the profile too; the store's `$validate` is not asked about the package's profiles.

**Why.** The owner approved following HL7's version model before records accumulate. The
Composition identifier was the one element the pipeline wrote against the profiles' definitions,
and nothing stopped a run from writing over a version another run had just written. ADR 0006
decision 5 asks for product identity by EU number, and the package defined no identifier system.

**Impact assessment (step 0).**

- No contract, schema, fidelity vector, golden vector, approved hash or importer version changes.
  `src/authority/` is unchanged.
- The transform's output changes for every product: the Composition's identifier, and so the
  Bundle that holds it and `outputHash`. No output hash is pinned (the run-manifest fixtures are
  frozen versions' examples).
- Query service and agent: unchanged. Neither reads `Composition.identifier`.
- Dev store: no migration. Stored versions keep the retired identifier; the next publication of a
  document writes the new one. The note's "What the dev store needs" says why.

**Tests.** `test/version-identity.test.ts` (the source's and the output's constants and
per-version elements across both versions of every product; same content, same identifier; the
precondition on the Bundle's entry only; each EU number rule, Type 2 and Type 1; the List's EU
number and its refusal; the profile's invariants carry the preflight's patterns; every identifier
system written has an active NamingSystem), `test/healthcare-client.test.ts` (the read: URL,
404, 410, no version), `test/persisted-run.test.ts` (the read before signing, the precondition in
the signed transaction, the package's profile kept from `$validate`), `test/type1.test.ts` (two
authorisations that agree, and two that do not).

**Official validation.** Run locally 2026-10-06, on mapping 1.4.0, with
`node scripts/ci/official-validate.mjs`: 0 errors and 65 warnings across 48 resources, every one
allowlisted. `npm run test:official` passed: the StructureMap twin (`fhir/maps/`), which now writes
the Composition identifier's new system and leaves its value to the implementation as before,
gives the crosswalk's output on every case. Ten graphs with EU numbers, validated by
the same script against Bundle-uv-epi and `eu-product-identity`: the two valid ones 0 errors; each
invalid one failed with exactly the invariants the preflight names.

**Not done, needs a live check.** Whether the Cloud Healthcare API honours `request.ifMatch` and
`request.ifNoneMatch: "*"` on a transaction's PUT entries is not documented in so many words
(the note, "Needs a live check"). The first deploy's smoke run exercises `ifMatch` on its success
path; a refusal there fails every persisting run before anything is written.

**Approval (step 8).** Not obtained: the author and the releaser are the same identity (see
"Release criteria").
