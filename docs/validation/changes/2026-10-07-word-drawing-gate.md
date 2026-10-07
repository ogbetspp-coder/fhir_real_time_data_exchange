# Recorded change: the certified Word gate's step 5, the drawing record verified, 2026-10-07

_Follows the procedure in `docs/validation/README.md`, "Change control for shared, evidenced
libraries", for the certified Word importer (`src/certified-word/`, its own version lock), which
holds the gate._

ADR 0006 P4, D3: `docs/design/certified-word-drawing.md`, build order step 5 (PR 4), "Step 4: PR 4
as built". Step 6 and run manifest 7.0.0 wait for P5.

**What changed.**

1. **Step 5** (`src/certified-word/drawing.ts`, new, and `src/certified-word/gate.ts`): after the
   recompute and the comparison (step 4), in every run that recomputes, the gate makes the record's
   key from the submission's .docx and request, reads `word/<key>/<drawing id>/<n>.json` from the
   record bucket for each pinned key version, highest first, under the worker's identity, and lets
   the first object found decide: at most 64 KiB, before parsing; strict UTF-8 and JSON, canonical,
   exactly `{ record, signatureBase64 }` of the record's shape; `keyVersion` and the request's hash
   those of its path; its RSA-PSS signature by the pinned key; and its environment, .docx, output,
   drawing and sections this submission's and this run's. A Storage error other than not-found
   fails the run.
2. **Closed codes** (`SubmissionRefusal`): `certified-word-drawing-invalid`,
   `certified-word-drawing-mismatch` (both dry or not) and `certified-word-document-unbound` (a run
   that is not dry whose record verified, until P5). `certified-word-drawing-missing` now means
   only that no record was found.
3. **The dry run's answer** (`certifiedWordCheck`): `drawn` where the record verified, beside
   `recomputed` (none found) and `submission-only`.
4. **The PSS check** (`src/approval/statement.ts`): `verifyPss` factored out of `verifySignature`,
   which calls it; behaviour unchanged.
5. **The worker**: `Dockerfile`'s worker target copies `src/render/word-drawing/`; `infra/run.tf`
   sets `WORD_DRAWING_BUCKET` and `WORD_DRAWING_ENVIRONMENT`; `src/config.ts` reads them.
6. **The certified Word importer** (1.2.3 → 1.3.0): what it makes is unchanged but for the version
   in the extractor token, so its vectors move in their hashes only; its lock covers the new file.

**Why.** ADR 0006 decision 1's third leg: Zone B may trust a Word narrative only where a drawing it
did not make says Chrome draws it as the .docx was read. PRs 1 to 3 made, deployed and pinned the
drawing build; dev holds its first signed record. This is the gate's check of it.

**Impact assessment (step 0).**

- Zone B (`src/`): the certified Word gate, which now reads the record bucket in every run that
  recomputes; the HTTP answer's closed codes; the configuration. The approval library's behaviour
  is unchanged (the signer, the pipeline and the query service verify through it as before).
- The worker image: it carries the drawing's lock and public keys. The query and signer images:
  unchanged but for the rebuilt `statement.ts`, whose behaviour is the same.
- Infrastructure: two environment variables on the worker. No grant: the worker's read of the
  record bucket was made in PR 2.
- Zone A, the drawing build and its records: unchanged.
- Contracts: no version moves. `SubmissionRefusal` and `certifiedWordCheck` are the HTTP answer's
  closed fields, not a published contract.
- Evidence: a certified Word submission made at importer 1.2.3 is refused by this build (another
  importer). None is persisted: every certified Word run is still dry, and a run that is not dry is
  refused at the gate before anything is written.

**Steps 1–7.** 1: `IMPORTER_VERSION` 1.3.0. 2: `npm run certified-word:vectors`,
`npm run certified-word:lock`, `npm run contracts:check` (no other drift). 3: every changed vector
reviewed: four submissions' submission, page text and report hashes, from the extractor token
alone; the results' hashes unchanged. 4: `test/certified-word/drawing.test.ts` (the first real
record verified; every way an object is `invalid`, a signed record a `mismatch`, or none
`missing`; the first object deciding; a Storage error; the pins) and the gate's step 5 in
`test/certified-word/recompute.test.ts` (the synthetic SmPC's dry run `drawn`, alone and through
the pipeline; not dry, `certified-word-document-unbound`); each rule removed in turn from
`drawing.ts` fails a test. 5: ADR 0002's invariant 11 restated. 6: UR-14. 7: no approval is
affected.

**Blast radius.** A dry run of a certified Word submission now reads the record bucket, and is
refused where an object there does not verify or is another submission's; a Storage outage there
fails it. A run that is not dry is refused as before, with a more exact code. Nothing persists on a
record until step 6 and P5.

**Tests.** `test/certified-word/drawing.test.ts`, `test/certified-word/recompute.test.ts`,
`test/infra/word-drawing.test.ts`, `test/ci/images.test.ts`.

**Not verified here.** The deployed worker's read of the record bucket under its own identity: the
worker runs with `DRY_RUN=false` and the run request has no dry-run field, so after the deploy a
run of the synthetic SmPC is expected to be refused `certified-word-document-unbound`, which only a
verified record gives (the pull request's post-deploy steps).

**Approval (step 8).** Not obtained: the author and the releaser are the same identity.
