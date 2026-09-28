# Recorded change: run manifest 4.0.0, the Global ePI package at its versioned URL, and the image runtimes, 2026-09-28

**What changed.**

1. **Run manifest 4.0.0**, a major under ADR 0002. `RUN_MANIFEST_VERSION` moved from `3.0.0` to
   `4.0.0`, and the manifest now names the standards that validated the run instead of literals:
   - `standards.packages`: every package the official validator loads, each as
     `{ package: "<id>#<version>", sha256 }` with the SHA-256 its lock records. That is the four
     packages of `fhir/standards.lock.json` (the `-ig` packages) and the nine of
     `fhir/validator-packages.lock` (the packages the validator resolves itself). One package is in
     both, with the same SHA-256 (`hl7.fhir.uv.extensions.r5#5.3.0`), so the list has twelve
     entries: the count the validator's own Package Summary reports. Every SHA-256 is the one its
     lock records; none is computed or assumed here. The worker image ships both locks, and the
     pipeline reads them before it writes anything (`src/fhir/standards-lock.ts`); only the document
     gate runs first, and for an authority import its fetch of the authority's files only reads. A
     package the two locks record with different SHA-256s fails the run. (Corrected by the #142
     follow-up: this said "before any side effect".)
   - `standards.globalEpiPackage` and `standards.emaPackage` are taken from that list. The schema
     refuses a manifest whose named packages are not among `packages`, and one that lists a package
     (an `id#version`) twice; one id at several versions is allowed, and `hl7.terminology.r5` is at
     four. JSON Schema cannot state either rule. The published schema states them in the
     `ManifestStandards` description, and Zone A's verifier (`zone_a.run_manifest_rules`,
     `VerifiedRunManifest`) enforces them, so an outside verifier refuses what the worker refuses.
     (Review round 1 of #142: the first draft named four of the twelve packages, and keyed
     uniqueness on the id.)
   - `runtime.validatorImageDigest`: the validator sidecar's image digest
     (`VALIDATOR_IMAGE_DIGEST`, set by `infra/run.tf` from the validator image reference, which must
     now carry a digest; `development` off Cloud Run, as for the others).
   - The free-form `GLOBAL_EPI_PACKAGE` environment variable is gone: it named one package,
     unchecked, in every manifest.
   - `QRD_TEMPLATE_VERSION` (`src/fhir/standards.ts`) is the one constant the transform stamps on
     the EMA Composition and the manifest records.

   3.0.0 is frozen as `RunManifestV3Schema`; 1.0.0, 1.1.0, 2.0.0 and 3.0.0 stay readable through
   `AnyRunManifestSchema`.

2. **The HL7 Global ePI package is pinned at its versioned URL.** `Dockerfile.validator` and
   `fhir/standards.lock.json` fetched `https://hl7.org/fhir/uv/emedicinal-product-info/package.tgz`
   (SHA-256 `6cdbc95f…`), HL7's unversioned "current" URL. Both now fetch `…/STU1/package.tgz`,
   SHA-256 `c997673388d2c53bd7dad43777a7589443e4d27f56a661a90aa0638bbce1cf3a`, which is also what
   `packages2.fhir.org` serves for `hl7.fhir.uv.emedicinal-product-info#1.0.0`. The two tarballs are
   two builds of 1.0.0, fifteen minutes apart on 2023-07-26. Compared file by file on 2026-09-28,
   they have the same files, and every difference is a build time:
   - `date` of 34 conformance resources (`2023-07-26T13:32:34+00:00` against `13:47:48`);
   - `date-time` in `other/spec.internals` and `timestamp` in `other/validation-oo.json`;
   - the "(built …)" time at the end of `package.json`'s `description`.

   No profile, element, binding, value set or example differs. The next HL7 release will move the
   unversioned URL; until this change that would have broken the validator image build, the
   Official validation check and `standards:fetch` at once, with no repository change.
   `test/ci/validator-pins.test.ts` now holds the sidecar's downloads equal to the lock's (URL and
   SHA-256), so the two pins cannot drift apart again; the EMA has already shipped two different
   1.0.0 editions under one version.

3. **The worker's and the query service's runtime** (not a normalisation change; recorded because
   ADR 0003's runtime is involved). The two images are one `Dockerfile` with a target each. The
   runtime stage is `debian:bookworm-slim` by digest, which Dependabot moves weekly, with the Node
   binary copied from the same `node:22.22.0-bookworm-slim@sha256:dd9d…` image as before. The binary
   carries its own ICU, so Node, ICU 77.1 and Unicode 16.0 are unchanged; the image build now asserts
   all three inside the image it ships, and fails otherwise. `npm ci` runs without install scripts
   and with `--engine-strict`; `package.json` accepts Node 22.22.0 only, with its npm (10.9.4) and
   Node 22 types.

**Why.** Audit batch B07 (S-1, S-3, S-4, S-5): a signed manifest could name a standard other than
the one that ran; the Global ePI pin would break on HL7's next release; the images' Debian had been
frozen since February 2026, with 34 fixed advisories it could never receive.

**Impact assessment (step 0).**

- Importers of `src/contracts/run-manifest.ts`: the pipeline, the contract generator and Zone A's
  generated model. No ledger column changes: the ledger row does not carry the manifest's standards
  or runtime.
- Previously produced evidence: 3.0.0 manifests and their ledger rows are unchanged and still parse.
- The Global ePI re-pin changes the validator's inputs by build times only. Official validation was
  run on the new pin (`npm run validate:official`, 2026-09-28): 0 errors across all 10 resources,
  as before. The deploy's profile import fingerprints the imported files, so the first deploy after
  this change re-imports the profile set (34 files differ, by `date`), then skips again.
- No fidelity vector, contract fixture, golden vector or approved hash moves. The transform's output
  is unchanged (`QRD_TEMPLATE_VERSION` is `10.4`, as the literal was).

**Steps 1–6.**

1. `RUN_MANIFEST_VERSION` is `4.0.0`.
2. `contracts/generated/run-manifest.schema.json`, `contracts/generated/index.json` and
   `zone-a/src/zone_a/contracts/run_manifest.py` are regenerated.
3. No fidelity vector changed.
4. Adversarial tests:
   - `test/standards-lock.test.ts`: the manifest names both locks' twelve packages with their hashes
     and the validator's digest; it ignores `GLOBAL_EPI_PACKAGE`; the schema refuses a named package
     that is not pinned, an empty list and a repeated package; 3.0.0 stays readable and is not
     accepted as 4.0.0; the readers accept one id at several versions and refuse a package listed
     twice, two SHA-256s for one package, a malformed validator-lock line, no package, a versionless
     package, plain HTTP and a missing SHA-256.
   - `test/ci/validator-pins.test.ts`: the sidecar and the lock pin the same URLs and hashes.
   - `test/infra/worker-provenance.test.ts`: `VALIDATOR_IMAGE_DIGEST` from a digest-only validator
     reference; no `GLOBAL_EPI_PACKAGE`.
   - `zone-a/tests/test_run_manifest_status.py`: the generated model requires the packages, their
     hashes and the validator's digest.
   - `zone-a/tests/test_run_manifest_rules.py`: `VerifiedRunManifest` refuses the named packages not
     pinned and a package listed twice, which the generated model alone accepts; the published
     description states both rules.
   - `test/ci/images.test.ts` and CI's Images job (`scripts/ci/build-images.sh`): the runtime
     assertion, the targets, the standards lock in the worker image.
5. ADR 0002's versioning rule is unchanged. ADR 0003's runtime is unchanged.
6. None.

**Blast radius.** A worker without `fhir/standards.lock.json` or `fhir/validator-packages.lock`
refuses every run before it writes anything (an authority import's fetch, which only reads,
runs first). A deploy whose validator image reference has no digest is refused at plan time (the deploy
passes one by digest already).

**Step 7.** Not applicable: no approved content changes.

**Approval (step 8).** Not obtained: the author and the releaser are the same identity (see
"Release criteria").
