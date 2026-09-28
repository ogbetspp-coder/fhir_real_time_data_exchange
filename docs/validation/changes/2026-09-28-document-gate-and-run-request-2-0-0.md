# Recorded change: the document gate's lossless parse and key budget, run request 2.0.0, importer 2.1.1, 2026-09-28

**What changed.**

- **A lossless parse.** Both gates (`verifyDocumentSubmission` in
  `src/contracts/canonical-submission.ts`, `verifyAuthorityImport` in `src/authority/gate.ts`) now
  require each parsed part to be the input: `sha256(parsed) === sha256(input)` for the submission
  and the fidelity report (refused at once) and for the page text (an issue). Every hash the gate
  checks and every walk it makes is over the parsed value, while what is stored is the input.
- **Reserved property names.** `jsonShapeIssues` (`src/lib/json-shape.ts`) refuses the keys
  `__proto__`, `constructor` and `prototype` at any depth, in every part of a submission and in
  what `src/gcp/submission-reader.ts` reads (`malformed-json`). No FHIR element or contract field
  has these names.
- **Property names count toward the aggregate budget.** The unverified-text walk adds every key
  it passes, each time it is written, to the 40,000-character total. Keys do not count toward
  the 3,000-string count. The message is now "Unverified strings and property names exceed 40000
  characters in total".
- **The authority gate bounds the shape first.** `verifyAuthorityImport` runs the shape bound
  (depth 48, 200,000 nodes, the reserved keys) over all three parts before it parses, because the
  parse's refinement hashes the Bundle. A pathological document is the classified "submission
  nesting exceeds depth 48", not a `RangeError`. It is also refused before anything is fetched.
- **The manifest's fidelity status is the report's own.** `src/pipeline.ts` takes the run
  manifest's `ingestion.fidelity.status` from the gate's report and throws "Only a passed fidelity
  report reaches run evidence" (reason `fidelity-not-passed`) for any other. It used to write the
  literal `passed`.
- **`RunRequest` 2.0.0.** `bundleId` of a `healthcare-api` run is an `AddressableFhirId`
  (`src/contracts/common.ts`): the R5 id grammar, beginning with a letter or a digit. `.` and `..`
  are refused with 400. `FhirId` itself is unchanged; `query-tools` still uses it (below).
- **The source read checks its URL.** `HealthcareApiClient.readSourceResource` refuses an id whose
  resolved URL path does not end with the encoded `Type/id` ("A source resource id must be a
  single path segment", reason `source-id-not-a-segment`), before any request.
- **Importer 2.1.1.** `src/authority/gate.ts` is under the importer lock, so `IMPORTER_VERSION`
  moves from `2.1.0` to `2.1.1` and `npm run authority:lock` records it. The importer's output
  changes only in its extractor name (`authority-import/2.1.1`).

**Why.** The audit of 2026-09-27 (batch B05):

- **C-1.** zod's `looseObject` drops an own `__proto__` member, which `JSON.parse` creates. A
  Bundle carrying `"__proto__": {"note": <prose>}` was accepted with hashes of the view without it,
  so `sha256(stored bundle)` differed from the recorded `bundleSha256`, and the member skipped the
  prose walk.
- **C-2.** 5,000 CamelCase keys (~289,000 characters) on one resource passed the 40,000-character
  budget, because keys were checked only against their grammar.
- **C-3.** Deleting the gate's `report.status` check or its out-of-section `text.div` check left
  every test green. The pipeline wrote `passed` into the signed manifest whatever the report said.
- **C-4.** The authority gate parsed and hashed before the shape bound, contrary to
  `src/lib/json-shape.ts`'s own contract. `FhirId` admits `.` and `..`, which encoding leaves as
  they are and the URL parser resolves: `Bundle/..` reads the store root.

**Impact assessment (step 0).**

- `src/contracts/canonical-submission.ts`: imported by the pipeline, the authority gate, the app
  and the contract generator. The schema does not change, so `CanonicalSubmission` stays 2.0.0
  and no approved hash moves. The gate accepts less.
- `src/lib/json-shape.ts`: the document gate, the authority gate and the submission reader.
- `src/contracts/common.ts`, `src/contracts/run-request.ts`: the app (`POST /v1/runs`), the
  config's run sources, the contract generator and Zone A's generated model
  (`zone-a/src/zone_a/contracts/run_request.py`). The Workflows definition passes `bundleId`
  through; the ids it is given (`synthetic-type2-smpc`) begin with a letter.
- `src/authority/`: importer 2.1.1; the golden vectors move because the extractor name is part of
  the submission.
- `query-tools`: not changed. `FhirId` is untouched, so its schema is byte-identical.
- `agent/`: not affected.
- Zone A's Python port: no gate rule is implemented there.

Previously produced evidence: no manifest, ledger row or approval changes. Every accepted
submission's hashes were over its parsed view. A submission with an own `__proto__` member would
have recorded hashes its stored object does not reproduce. Nothing in this repository produced
one. A recorded authority import under importer 2.1.0 re-verifies only with a 2.1.0 worker (D10),
as for every importer version.

**Budget headroom.** Counted the new way, the synthetic Type 2 Bundle uses about 11,200 of the
40,000 characters: ~8,300 in strings and ~2,800 in keys. The synthetic publication's import uses
about 8,500. No real EMA label is imported end to end yet (each pinned label is refused at a
recorded stage), so a real label's use is not measured.

**Steps 1–6.**

1. `RUN_REQUEST_VERSION` is `2.0.0`; `IMPORTER_VERSION` is `2.1.1`.
2. `contracts/generated/run-request.schema.json`, `contracts/generated/index.json`,
   `zone-a/src/zone_a/contracts/run_request.py`, `test/fixtures/authority/vectors.json` and
   `src/authority/importer.lock.json` are regenerated.
3. No fidelity vector changed. The three authority vector hashes changed with the extractor name
   only.
4. Adversarial tests, each failing against the code before this change (the C-3 tests fail when
   the check they name is deleted):
   - `test/contracts/canonical-submission.test.ts`: "counts property names toward the aggregate
     budget", "rejects an own \_\_proto\_\_ member the parse would drop", "rejects a
     self-consistent fidelity report that failed", "rejects a narrative outside the verified
     sections".
   - `test/contracts/lossless-parse.test.ts`: with the shape bound lifted, both gates still refuse
     a parse that drops a member.
   - `test/authority/gate.test.ts`: "bounds the submission's shape before it parses or hashes it,
     and fetches nothing", "refuses a member the parse would drop, and fetches nothing".
   - `test/json-shape.test.ts`: the three reserved names at any depth.
   - `test/pipeline-fidelity-status.test.ts`: past a gate that lost its status check, the run
     still stops before the manifest.
   - `test/healthcare-client.test.ts` and `test/app.test.ts`: `.` and `..`.
5. ADR 0002's invariant 6 needs a clause saying property names count toward the character
   budget. Not amended here (the documentation batch owns the ADRs' wording); the rule is stated in
   `src/contracts/canonical-submission.ts`.
6. None changed here.

**Blast radius.**

- A submission whose unverified strings and keys together exceed 40,000 characters is refused.
  It used to pass if its strings alone did not.
- A submission, report or page text with a `__proto__`, `constructor` or `prototype` key is
  refused, by the gate and by the submission reader.
- A `healthcare-api` run with `bundleId` `.` or `..`, or any id beginning with `-` or `.`, answers 400.
- A run past a gate that did not check its report's status fails with `fidelity-not-passed`
  before any evidence is written.

**Step 7.** Not applicable: `CanonicalSubmission` stays 2.0.0 and no approved content changes.

**Approval (step 8).** Not obtained: the author and the releaser are the same identity (see
"Release criteria").
