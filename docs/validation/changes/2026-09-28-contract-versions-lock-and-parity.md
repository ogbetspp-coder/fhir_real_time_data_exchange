# Recorded change: contract versions held to their schemas, Python readers held to Zod, numbers, 2026-09-28

_Follows the procedure in `docs/validation/README.md`, "Change control for shared, evidenced
libraries" (steps 0–8). Audit batch B14 of the 2026-09-27 repository audit (C-6 to C-11), with the
two items carried from the review of #145 (L2-d, and ADR 0002's amendment for re-spellings)._

**What changed.**

1. **Contract versions.**
   - `ingestion-provenance` **1.0.0 → 2.0.0** (major). The schema is unchanged by this change; the
     version catches up with it. Since #113 (2026-09-24) its `sourceDocument` has been a union on a
     required `kind`, which 1.0.0 did not require, and two tightenings before that (1a70910,
     ddcbf58, 2026-09-19) were also published under 1.0.0's `$id`. A reader holding 1.0.0 refuses
     what is published now, so the version is a major (ADR 0002, "Versioning").
   - `run-manifest` **4.0.0 → 5.0.0** (major; audit C-9, C-8). `runtime.sourceCommit` is a full git
     commit id, `runtime.imageDigest` and `runtime.validatorImageDigest` are image digests (each, or
     `development` off Cloud Run), and `runtime.workflowRevision`, `standards.qrdTemplate`,
     `standards.mappingVersion` and `persistence.targetStore` are tokens, where 4.0.0 took any text
     of up to 1,024 characters. The published schema states, as `if`/`then`/`else`, that a document
     run and only one carries an ingestion block, and that an authority import and only one records
     what Zone B fetched. The worker reads the runtime values through `src/config.ts`, which refuses
     a malformed one at startup; `infra/run.tf` refuses at plan time a `service_version` that is not
     a full commit id. 1.0.0 to 4.0.0 are frozen in `src/contracts/run-manifest-frozen.ts` from
     copies of their own parts and read through `AnyRunManifestSchema`.
   - `query-tools` **4.0.0 → 4.1.0** (minor): the audit record gains an optional `contractVersion`,
     which the service always writes.
   - `agent-turn` **1.1.0 → 1.2.0** (minor): optional `contractVersion` and `queryToolsVersion`,
     which the agent always writes: the versions of its vendored copies.
   - `canonical-submission` 2.0.0, `fidelity-report` 1.0.0, `source-document-text` 1.0.0 and
     `run-request` 2.0.0: unchanged, schema and version.
2. **The version lock** (C-6): `contracts/versions.lock.json`, each version's structure hash (the
   published document without its `$id`), `npm run contracts:lock`, and
   `test/contracts/versions-lock.test.ts`, which refuses a changed schema under a locked version and
   a lock that rewrites an entry `main` released. The versions already published enter the lock at
   their current schemas; the lock has no history before this change.
3. **Refinements** (C-8): every `.refine` of a published contract is listed in
   `src/contracts/json-schema.ts` (`REFINEMENTS`) as expressed or unexpressed, and generation refuses
   an unlisted one. The schema generator's document builder (`publishedSchema`) moved out of the
   script into that module, and the test calls it rather than re-implementing it.
4. **Python readers** (C-7, L2-d): Zone A's models are generated with `--strict-types` and a base
   class that refuses `null` for a declared field; the list of contracts is derived from
   `contracts/generated/index.json`. The agent reads every `pattern` as ECMA-262 does
   (`contract.ecma_pattern`: `$` is `\Z`, `.` excludes the line terminators, `re.ASCII`). The
   contract verdict corpus (`test/fixtures/contracts/contract-verdicts.json`) records Zod's verdict
   on 954 values of every named, patterned definition and on 43 changed documents and the 8 they
   were changed from; both Python suites
   reproduce every one.
5. **Fixtures that cross languages** (C-8, C-9, C-10): an authority import's Type 1 submission, its
   fidelity report and page text; the smoke product's submission, whose strength is now 2.5 mg; the
   manifests each run-manifest version's own code emitted (a fixture run and a document run each;
   1.0.0 a fixture run only), in `test/fixtures/run-manifest/`, never regenerated.
6. **Numbers** (C-10): Zone A's `canonical_json` writes a double by RFC 8785 section 3.2.2.3, where
   it refused every non-integral float; the submission reader refuses a document part whose numbers
   are not written as JavaScript writes them (`non-canonical-number`). ADR 0002 records the decision.
7. **Stale text** (C-11): the drawn-document comment names the current normalisation version rather
   than 3.0.0; the unused `ApprovalMethod` enum is gone (it was never published); `zone-a/README.md`'s
   approved-content recipe includes `graphType`; the strictness test cites `canonical-bundle.ts`.
8. **The query service's ids** (carried from B03/B01): an entitled Bundle id is an
   `AddressableFhirId` (startup, and Terraform's `query_entitlements_json` validation), and the
   reader refuses any other before it builds a store URL. The published tool inputs are unchanged.

**Why.** Audit C-6 to C-11: a contract version did not name one schema, the Python readers of the
published schemas accepted what Zod refuses, the published schemas under-described the contracts
and no Type 1 record or manifest crossed languages, old manifest versions were built from live
parts, and Zone A could not hash a decimal.

**Version decisions, and why each is what it is.**

- `ingestion-provenance` 2.0.0 rather than no longer publishing it: Zone A generates and tests a
  model from it, and 2.0.0 is the version of the submission contract it is the provenance of.
- `run-manifest` 5.0.0, not a minor: 4.0.0 accepted values 5.0.0 refuses (free text in the runtime
  and the store, a document run without ingestion in the published schema). No reader depends on a
  manifest being a particular version (the ledger row does not carry it), and the worker's next run
  after the deploy writes 5.0.0.
- `query-tools` 4.1.0, not a major, and no field on `QuoteVerification`: C-9 asked for an optional
  `contractVersion` on `QuoteVerification` as well. The agent validates every tool answer against its
  vendored copy, whose objects are closed, so an answer carrying a field the deployed agent's copy
  lacks is refused: all `verify_quote` answers would be unavailable until the agent was redeployed,
  which is what a major means in practice. The version is on the service's audit record instead,
  which the agent never reads, and on the agent's own turn record, which is where the verification
  stamps are.
- `agent-turn` 1.2.0, a minor: two optional fields on a record only the agent writes.
- `canonical-submission` unchanged: its version is part of every `approvedContentSha256`, and no
  change here alters its schema. The decision fields it could state as `if`/`then` wait for 3.0.0.
- Adopting `AddressableFhirId` in the published tool inputs would refuse inputs 4.0.0 accepts, a
  query-tools major; the service enforces it through its entitlements instead, where a `..` has
  always been answered `document-not-found`.

**Impact assessment (step 0).**

- `src/contracts/` is imported by the worker, the query service, the contract generator and the
  scripts; `zone-a/` generates its models from `contracts/generated/`; `agent/` vendors
  `query-tools` and `agent-turn`.
- No hash moves. `src/lib/hash.ts` is unchanged, and `canonicalJson` already wrote numbers by
  RFC 8785 (`JSON.stringify`). Zone A's `canonical_json` writes every value it wrote before exactly
  as before (integers, and integral floats as the integer) and now also writes values it refused.
  `npm run contracts:check` regenerates `test/fixtures/contracts/{canonical-submission,
fidelity-report,source-document-text,run-request}.json`, `test/fixtures/fidelity/vectors.json`,
  the importer's vectors and the differential smoke corpus without a byte of difference; the
  `canonical-submission` 2.0.0, `fidelity-report`, `source-document-text` and `run-request` schemas
  are byte-identical.
- The smoke product's content changes (name and strength, `2.5 mg`): it is published by every
  deploy's smoke run and by nothing a demonstration shows, so the next deploy writes a new version
  of its Bundle, as designed. `test/fixtures/narrative/section-divs.json` records its new name.
- Neither the importer lock (`src/authority/**`, `src/fidelity/normalize.ts`,
  `src/fidelity/xhtml.ts`) nor the Zone A versions lock covers a file this change touches.
  `src/fidelity/verify.ts` takes its report types from the contract (`z.infer`) with no change of
  behaviour; it is covered by neither lock.

**Deploy order.** The agent validates the service's answers against its vendored copy; no answer
changes, so the agent may be redeployed before, with or after the service. It must be redeployed
from `main` for its records to carry the two versions. The worker writes 5.0.0 from its first run
after the deploy; a verifier reading 5.0.0 must be regenerated from `contracts/generated/`.

**Steps 1–6.** 1: versions as above. 2: `contracts/generated/**`, `contracts/versions.lock.json`,
`zone-a/src/zone_a/contracts/*.py` and the agent's vendored copies regenerated; the new fixtures
exported. 3: no fidelity vector changed. 4: adversarial tests:

- `test/contracts/versions-lock.test.ts`: a changed schema under a locked version; the released
  entries; every record exists.
- `test/contracts/run-manifest-frozen.test.ts`: every emitted manifest parses under its own
  version and no other; the frozen module imports zod alone; 5.0.0 refuses each free-text value
  4.0.0 accepted; the runtime is read through the configuration, which refuses a malformed value.
- `test/contracts/schema-generation.test.ts`: every refinement listed, an unlisted one and one
  without an id refused, the manifest's two rules published.
- `test/json-numbers.test.ts`, `test/submission-reader.test.ts`: `500.0`, `5e2`, `500.00`, `-0`,
  `1.0`, a digit beyond a double, refused; 2.5 read.
- `test/query/fhir-reader.test.ts`, `test/query/config.test.ts`, `test/infra/*.test.ts`: `.`,
  `..`, `-bundle` refused as an entitled or a read Bundle id; the plan-time preconditions.
- `zone-a/tests/test_contract_verdicts.py`: all Zod verdicts reproduced (on main's models, 14 of
  the documents disagreed, and the run-manifest primitives did not exist).
- `zone-a/tests/test_canonical_json_parity.py`: 1,037 doubles written as JavaScript writes them.
- `zone-a/tests/test_contracts_parity.py`: the Type 1 and decimal submissions hash alike and round
  trip; the emitted manifests verify and round trip.
- `zone-a/tests/test_run_manifest_rules.py`: the two rules the published `if`/`then` states.
- `agent/tests/test_contract_verdicts.py`: all Zod verdicts on the vendored contracts reproduced,
  the two values the #145 review named refused.

5: ADR 0002 amended (2026-09-28). 6: the rows whose evidence changed are UR-12, UR-17, UR-37 and
UR-39; their text, and the procedure's paragraph that names `query-tools` 2.0.1 and `agent-turn`
1.0.0 as current, are left to the documentation batch (B17).

**Blast radius.**

- A worker whose `GIT_COMMIT` is not a full commit id, or whose image digests are not digests,
  does not start; `scripts/gcp/deploy.sh` sets all three.
- A document part written with a number JavaScript would write differently is refused
  (`non-canonical-number`); every producer in this repository writes with `JSON.stringify`.
- An entitlement map naming a Bundle id that begins with `.` or `-` stops the query service at
  startup, and Terraform refuses it at plan time.
- A Zone A or other Python producer that relied on lax coercion or on `null` for an absent field is
  refused by its own model, as Zone B refused it.

**Step 7.** Not applicable: no approved content changes.

**Approval (step 8).** Not obtained: the author and the releaser are the same identity (see
"Release criteria").
