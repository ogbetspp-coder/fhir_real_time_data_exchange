# Recorded change: our own FHIR package, the Provenance validated, warnings gated, 2026-10-05

**What changed.**

1. **Our own definitions are a FHIR package**, `dev.khs.fhir.epi#0.1.0`.
   `scripts/fhir/generate-artifacts.ts` now also writes, into `fhir/generated/`:
   - the CodeSystem `https://khs.dev/fhir/CodeSystem/canonical-smpc-sections`: every `sourceKey`
     of `fhir/mappings/cap-smpc-en.json`, its `title` as display, nested as the sections are
     (`hierarchyMeaning` `part-of`), `content` `complete`, `caseSensitive` true, `version` the
     mapping's (`1.3.0`);
   - the CodeSystems `document-type` (`smpc`, the one code the repository writes under it),
     `provenance-activity` (`structuring`, `authority-import`) and `approver-role` (the contract's
     `ApproverRole` values);
   - a ValueSet of all codes for each of the four;
   - the extension `https://khs.dev/fhir/StructureDefinition/ext-approval-content-sha256`:
     context `Provenance`, `value[x]` string only, invariant `khs-sha256-1`
     (`value.matches('^[0-9a-f]{64}$')`, error);
   - the ConceptMap gains `sourceScopeCanonical`, the section ValueSet.

   Identifier systems (`https://khs.dev/fhir/identifier/...`) need no definition and have none: no
   NamingSystem was added. No canonical URL moved.

   The generator packs the eleven resources, `package/package.json` (FHIR `5.0.0`, depending on
   `hl7.fhir.r5.core#5.0.0` only) and `package/.index.json` into
   `fhir/generated/dev.khs.fhir.epi.tgz`: a ustar archive with sorted members, mode 0644, owner and
   group 0 without names, and npm's fixed time (1985-10-26T08:15:00Z), in a gzip stream with no
   time, OS byte 255 and stored (uncompressed) deflate blocks, so the bytes depend on the inputs
   alone. zlib's would not: Node's gzip wrote OS byte 19 on the macOS machine this was written on
   (zlib writes 3 on Linux), and compressed output depends on the zlib build, while CI runs Node
   22.22.0 and this change was written on 24. Two builds gave the same SHA-256. The archive is
   39 KB.

2. **The archive is committed and pinned like the four downloaded packages.** Committed, not
   built at image time: Cloud Build and CI's Images job build `Dockerfile.validator` from a
   checkout with no Node step, and the validator image has no Node. `npm run artifacts:check`
   regenerates it and fails on any byte that differs. Its SHA-256,
   `368f98f10d82ef196ed97db4c1107513789e5dba9f7873c95fd647a47e2e35ff`, is pinned in
   `Dockerfile.validator` (`ARG KHS_EPI_SHA256`, a `COPY` from the build context and
   `sha256sum --check` in the fetch stage; the image keeps its two stages and its user without
   root) and in `fhir/standards.lock.json`, whose entry has a `path` in place of a `url`. The
   sidecar's `CMD` loads it as a fifth `-ig`. `scripts/ci/validator-pins.mjs` reads checksummed
   copies beside checksummed downloads, so `npm run validate:official` copies it from the checkout
   and loads it as the sidecar does; `scripts/dev/validator-server.sh` loads it too.

3. **The FHIR store does not import it.** The profile import (`scripts/gcp/bootstrap.sh`) takes
   four packages by name from the deploy inputs, which `scripts/gcp/deploy-inputs.sh` fetches with
   `--used-by bootstrap`; the new entry has no `usedBy` and no URL, so it is neither fetched nor
   imported, and `scripts/fhir/fetch-standards.mjs` without `--used-by` says it is in the
   repository and skips it. Importing it is not clearly correct (the store would then hold
   experimental `0.1.0` definitions, and the import's fingerprint would move), so the store's
   behaviour does not change.

4. **Everything persisted is validated.** `officialValidationTargets` (`src/pipeline.ts`) takes a
   `document` run's Provenance and validates it against base R5
   (`http://hl7.org/fhir/StructureDefinition/Provenance`). It goes to the official validator only:
   the store has no definition of its extension or code systems, so `$validate` is not asked
   about it. The rest of the transaction (`buildPersistTransaction`) is the List, the document
   Bundle and the Bundle's entries with their references resolved to `Type/id`; the List, the
   Bundle and its entries as the Bundle holds them were validated already.
   `scripts/ci/emit-validation-set.ts` adds a Provenance to each case, built by
   `toProvenanceResource` from the contract fixtures: the drawn, attested submission
   (`canonical-submission.json`) and the authority import (`canonical-submission-type1.json`).
   The run manifest's `validation.profiles` names the base Provenance profile for a `document`
   run, and `standards.packages` names thirteen packages, the new one with its SHA-256. The
   manifest's schema does not change.

5. **Warnings are gated.** `scripts/ci/official-validate.mjs` reads every `Warning @` line, its
   location without line and column, and fails on any warning that
   `scripts/ci/official-validation-warnings.json` does not name by file, location and message, and
   on an entry no warning matched. The list was seeded with the warnings that remained after (1),
   each with a one-line reason. The gate now validates the files of one profile set in one run of
   the validator, reading each file's own report: six runs instead of one per file.

**Why.** The Provenance the pipeline persists failed official validation
(`The extension https://khs.dev/fhir/StructureDefinition/ext-approval-content-sha256 could not be
found`), and nothing validated it; the gate counted only errors, so warnings, among them one for
every coding in our own unpublished code systems, were invisible; and the ConceptMap had no
source scope because no value set of the section keys existed.

**Impact assessment (step 0).**

- Official validation, run locally 2026-10-05 with `node scripts/ci/official-validate.mjs`:
  before, on the ten files of `main` with the four packages, 0 errors and 126 warnings, 66 of them
  about our own unpublished code systems: 64 `could not be found` for `canonical-smpc-sections`, 1
  for `document-type`, and 1 on the ConceptMap, whose source code system was "not fully defined".
  After, on 21 files with the five packages: 0 errors and 63 warnings, every one allowlisted (63
  entries, 0 stale). 36 are on the synthetic fixture's product graph and its copy in the EMA
  Bundle (24 codings without a system, 12 with a display and no code); 23 are `dom-6`
  best-practice notes (no `Resource.text`, two of them on the Provenances); 2 are UCUM codes the
  validator cannot check with `-tx n/a`; 1 is the SPOR URL form of the document type the import
  keeps; 1 is a validator defect on the extension's type code. The gate took 4 min 52 s for 21
  files in six validator runs, against 6 min 53 s for ten files in ten runs before.
- Failure paths exercised with the five packages: a Provenance whose extension value is `ABC`
  fails `khs-sha256-1`; one with `valueCode` fails ("allows for the types [string]"); one with an
  activity code not in `provenance-activity` fails ("Unknown code"). Without the package (`main`),
  the Provenance fails with `The extension ... could not be found`.
- No contract, schema, fidelity vector, golden vector, approved hash or importer version changes.
  The transform's output is unchanged.
- The worker image ships `fhir/standards.lock.json` already; it reads the new entry.

**Blast radius.** The validator sidecar loads one more package (11 resources, under a second). A
`document` run makes one more call to the sidecar. A warning the validator newly gives, on a
change to the mapping, the fixtures or the pins, fails CI until it is fixed or reviewed into the
allowlist. A change to any generated resource changes the package's SHA-256, which must be re-pinned
in `Dockerfile.validator` and `fhir/standards.lock.json` (`test/ci/validator-pins.test.ts` fails
otherwise), and `PACKAGE_VERSION` should move with it.

**Tests.** `test/fhir-artifacts.test.ts` (the code system is the mapping's tree; the ConceptMap's
scopes; the archive's gzip header, member order, owner, mode and time, and its members are the
committed resources byte for byte), `test/ci/validator-pins.test.ts` (the sidecar loads the package
at the committed bytes, the lock and the sidecar agree, the local helper loads every package, the
warning reader and verdict, the allowlist's shape), `test/standards-lock.test.ts` (thirteen
packages; a lock entry needs a URL or a path, not both), `test/ci/validation-set.test.ts` and
`test/persisted-run.test.ts` (the Provenance is a target, validated officially and not by
`$validate`).

**Not done.** No Docker on the machine this was written on, so the validator image was not built
here; CI's Images job builds it and starts it offline.

**Approval (step 8).** Not obtained: the author and the releaser are the same identity (see
"Release criteria").
