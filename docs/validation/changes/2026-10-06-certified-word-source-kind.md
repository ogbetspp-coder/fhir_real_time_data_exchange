# Recorded change: the `certified-word` source kind and its importer, 2026-10-06

`CanonicalSubmission` and `ingestion-provenance` 3.0.0, run manifest 6.0.0, the authority importer
2.4.1, the certified Word importer 1.0.0, `dev.khs.fhir.epi#0.4.0`.

**What changed.** ADR 0006 prerequisite P4, D1 (`docs/design/certified-word-import.md`).

1. **A third source kind** (`src/contracts/ingestion-provenance.ts`, 2.0.0 → 3.0.0):
   `sourceDocument` may be `certified-word`, which pins the uploaded .docx (`sha256`,
   `byteLength`, `filename`, `storageUri`), the request `zone_a.recompute` made the sections with
   (`document` `smpc` | `pl`, `view`, `part`, `assignments`, and `versions`, every version the
   recompute names), the TypeScript importer's version (`importer`), the number of tracked changes
   the view settled, one page per section (`sectionPages`: page, section key, EMA code) and the
   page text (`extractedText`).
2. **`CanonicalSubmission` 2.0.0 → 3.0.0** (`src/contracts/canonical-submission.ts`), and the
   gate's rules for the new kind (ADR 0002's amendment of 2026-10-06):
   - invariant 7: a `type1` graph, an attestation, no model or prompt template, the mapping its
     recompute used as the terminology service, and the extractor `certified-word` whose version
     is the SHA-256 of the canonical JSON of `{ importer, recompute: recompute.versions }` (the
     token `certified-word/<hash>`); the certified Word gate refuses a submission another importer
     version made;
   - invariant 10: a synthetic one's identifier value is `certified-word:` and a document id in
     the reserved block `00000000-5979-4e74-8000-`, with the marker in every narrative;
   - invariant 11: accepted only as a dry run, by `src/certified-word/gate.ts`, which refuses one
     when `DRY_RUN` is false with the closed code `certified-word-not-recomputed`, which the HTTP
     answer carries (`SubmissionRejectedError.reason`, `src/app.ts`), and the ordinary gate only
     with `GateOptions.certifiedWordDryRun` bound to its hash;
   - invariants 12 and 13: one page per section, each wholly body, every page no narrative
     covers blank, page i the record's i-th section in pre-order and each span on its own
     section's page; the identifier value exactly `certified-word:<uuid>`, a namespace no other
     source kind or route may write (`src/pipeline.ts`, as for `authority-import:`).
3. **The importer** (`src/certified-word/`): a pure, deterministic function of the recompute's
   bytes and what a person confirmed (`CertifiedWordRequestSchema`: the upload, the recompute's
   request, the ePI's document id, the canonical product and the approval placeholder) to a
   submission, its page text and its fidelity report. It checks, and refuses at the first that
   fails, by stage and closed reason: the request; the bytes (strict UTF-8 and JSON, the
   authority's reader); the recompute's own refusal; the result's shape; that the result is the
   one the request names (versions, document, view, part, the assigned headings both ways, this
   build's mapping, and the SmPC only); the mapping's tree and codes; each title one line of plain
   text that draws something; each narrative reading as its page under Zone B's scanner, a
   section without one having the empty page and being neither a leaf nor one whose narrative the
   mapping requires; the product (its name beginning section 1's first line and ending where a
   word does, never in punctuation; its holder section 7's first line exactly; its EU
   authorisation numbers exactly those standing alone on section 8, with no other `E U /` there
   in any case or spacing). Narratives, pages and titles are the recompute's, unchanged. Its
   version, `certified-word-import/1.0.0`, is locked to the hash of `src/certified-word/` and of
   its golden vectors (`src/certified-word/importer.lock.json`, `npm run certified-word:lock`). The Type 1 graph is the confirmed product only: the
   MedicinalProductDefinition (our id, the EU product numbers), the Organization (our id), one
   RegulatedAuthorization per EU authorisation number; packs, ingredients and substances are
   declared not supplied.
4. **Titles** (D6; `src/fhir/mapping.ts` `TitleRule`, `src/fhir/transform.ts`,
   `src/fhir/preflight.ts`): for a certified Word source the crosswalk carries each section's
   title as written and the EMA preflight does not hold it to the template's; for every other
   source, as before.
5. **The run manifest** 5.0.0 → 6.0.0 (`src/contracts/run-manifest.ts`): the ingestion block's
   `sourceKind` may be `certified-word`, and `contractVersion` is 3.0.0. 5.0.0 is frozen
   (`RunManifestV5Schema`, `src/contracts/run-manifest-frozen.ts`) and its emitted manifests stay
   readable as 5.0.0; `test/fixtures/run-manifest/` gains 6.0.0's, emitted by this code, a dry run
   of a certified Word submission among them, and a persist-mode 5.0.0 manifest derived as the
   older versions' were.
6. **The FHIR Provenance** of an attested certified Word source: activity `structuring`, the
   attester, and the .docx by its SHA-256 (`src/fhir/provenance.ts`).
7. **Our package** `dev.khs.fhir.epi` 0.3.0 → 0.4.0: NamingSystems for the three identifier
   systems the record writes (`certified-word`, `canonical-product`, `canonical-organization`).
   Its SHA-256, `add2311a9de6abc2db2d2b8b85f0f48e83a48f2a191ca8ce29e3def65c61d90d`, is pinned in
   `Dockerfile.validator` and `fhir/standards.lock.json`.
8. **Cross-language fixtures**: `zone-a/scripts/certified_word_fixtures.py` builds five synthetic
   Word labels in Python (an SmPC, the same with tracked changes, with an assigned heading, with a
   section the recompute refuses, and a leaflet) and commits what `python -m zone_a.recompute`
   writes for each, byte for byte, in `test/fixtures/certified-word/recompute/`; its `--check` and
   `zone-a/tests/test_certified_word_fixtures.py` fail on drift. The importer's golden vectors
   (`src/certified-word/vectors.ts`, `test/fixtures/certified-word/vectors.json`) are regenerated
   and compared under `npm run contracts:check`.
9. **Official validation**: the certified Word record's source, EMA List, Bundle, Composition
   and Provenance join the set (`scripts/ci/emit-validation-set.ts`), with ten reviewed `dom-6`
   warnings in the allowlist, the same best-practice warning the authority import's record has.

**Why.** ADR 0006 decision 1 admits a company's Word label read exactly; D1 is the contract the
recompute (D2), the bytes (D4) and the drawing (D3) then complete.

**Versions: majors, and the reservation they move.** The first draft of this change made the three
contracts minors (2.1.0, 5.1.0), on the ground that a new member of a discriminated union refuses
nothing that parsed before. The independent review of #193 held them to ADR 0002's rule ("Enum
additions on fields that Zone B branches on are major"): Zone B branches on `sourceDocument.kind`
and on the run manifest's `sourceKind`, and 5.1.0 also refused the `contractVersion` 2.0.0 that
5.0.0 accepted. So they are 3.0.0 and 6.0.0, and 2.1.0 and 5.1.0 were never published. The
renderer gate's and the withheld design's contract change
(`docs/design/authority-import-renderer.md` R10, `docs/design/authority-import-withheld.md` W7,
ADR 0002's amendment of 2026-09-25), decided and not built, had reserved `CanonicalSubmission`
3.0.0 and the run manifest's next major after 5.0.0; that reservation moves to the following
majors, `CanonicalSubmission` 4.0.0 and the run manifest's after 6.0.0, and the three documents
say so. The `CanonicalSubmission` refinement note in `src/contracts/json-schema.ts` that listed a
change "for 3.0.0" now lists it for 4.0.0.

**The independent review of #193**, each finding fixed with a test that fails without the fix
(`test/certified-word/`, `test/app.test.ts`, `test/namespace.test.ts`,
`zone-a/tests/test_certified_word_fixtures.py`):

1. the name and the holder were checked as substrings ("Synthetic Exampli", "mg", the holder's
   address imported): now the rules in item 3, and no whitespace at either end of either;
2. the assigned headings were bound one way only: now the result's assigned headings are exactly
   the request's;
3. the versions were minors: now majors (above);
4. the EU number guard missed `eu/`, `EU /` and `001.3`: now any `E U /` in any case or spacing
   must start a number read, and a full stop may follow a number only at a line's end or before
   whitespace;
5. the document id is bound to nothing: recorded as open for P5's canonical-product registry and
   the gate before anything persists (the design note, "D1, as built");
6. the TypeScript importer had no version lock: now `certified-word-import/1.0.0`, locked, and in
   the extractor token beside the recompute's versions;
7. the dry-run refusal's code reaches the HTTP caller; UR-48 names `certified-word:` and the
   namespace test covers both ungated routes; a title of whitespace (a no-break space included) or
   holding U+2028 or U+2029 is refused; the gate checks each page's section key;
8. the Python `--check` compares bytes.

**Impact assessment (step 0).**

- Zone B (`src/`): the gate, the pipeline, the crosswalk and the preflights. For a drawn source
  or an authority import nothing changes but the contract version: the crosswalk's title rule
  defaults to the template's, and the existing tests and the official validation set pass
  unchanged (no allowlisted warning went stale, so no id or output moved).
- Every 2.0.0 submission is refused (`schemaVersion`); no submission is persisted today but the
  synthetic one, which the producer makes again at 3.0.0. Step 7 below.
- Authority importer (`src/authority/`): unchanged in what it does; 2.4.1, its synthetic
  vector's three hashes moving with the contract version and its extractor name, every refusal
  vector unchanged.
- Zone A: its generated models (`zone-a/src/zone_a/contracts/`) are regenerated; the fidelity
  vectors, the differential corpus and the recompute are unchanged.
- Agent: unchanged; it vendors `query-tools` and `agent-turn` only.
- Query service: unchanged; a certified Word submission never persists.
- Signed approvals (`src/approval/`, `src/signer/`, `APPROVAL_ENFORCEMENT`): unchanged. The gate
  refuses a certified Word submission that is not a dry run before any approval is asked for,
  with enforcement on or off, and a dry run is not asked; the signer, which runs the ordinary
  gate without the dry-run proof, refuses one as `submission-refused`, and its review is built
  for a drawn record only.
- The run manifest: the worker writes 6.0.0 from its first run after the deploy; a reader of
  5.0.0 reads it through `AnyRunManifestSchema`; a verifier reading 6.0.0 must be regenerated
  from `contracts/generated/`.
- The HTTP answer to a rejected submission gains an optional closed `reason`, given only for
  `certified-word-not-recomputed`; every other rejection answers as before.

**Steps 1–7.** 1: the versions above. 2: `npm run contracts:generate`, `npm run vectors:generate`
(no change), `npm run contracts:fixtures`, `npm run authority:vectors`,
`npm run certified-word:vectors`, `npm run contracts:lock -- --record` this file,
`npm run authority:lock` and `npm run certified-word:lock`; the Zone A models with `uv run --frozen python
scripts/generate_models.py`. 3: the authority vector's three hashes, reviewed: the same import at
another contract version and extractor name; the contract fixtures likewise; the new vectors are
reviewed in the tests that read them (`test/certified-word/import.test.ts`). 4: a negative test
for each refusal the importer and the gate add (`test/certified-word/import.test.ts`,
`gate.test.ts`, `titles.test.ts`); two importer checks are defences no input reaches
(`record: fidelity-not-passed`, `record: uncovered-page-not-blank`, held by construction, as the
authority importer's are), and the gate re-checks the second. 5: ADR 0001 and ADR 0002 amended
(2026-10-06, ADR 0006). 6: UR-11 and UR-14. 7: an approval of a 2.0.0 submission does not carry
forward.

**Blast radius.** A source the previous gate accepted and this one refuses: a 2.0.0 submission,
and a drawn source whose identifier value begins `certified-word:`. One it accepts that the
previous refused: a certified Word submission, as a dry run only.

**Tests.** `test/certified-word/` (40 tests, the importer lock's among them),
`zone-a/tests/test_certified_word_fixtures.py`, `test/contracts/run-manifest-frozen.test.ts`
(5.0.0 frozen; 6.0.0 reads a certified Word run that 5.0.0 refuses), `test/version-identity.test.ts`
(the record's identifier systems have their NamingSystems), `test/ci/validation-set.test.ts` (the
certified Word case), `test/app.test.ts` (the closed code), `test/namespace.test.ts` (the
namespace on both ungated routes).

**Official validation.** Run locally 2026-10-06 with `node scripts/ci/official-validate.mjs
--offline`: 0 errors and 75 warnings across 56 resources, every one allowlisted;
`npm run test:official` passed.

**Approval (step 8).** Not obtained: the author and the releaser are the same identity (see
"Release criteria").
