# Validation lifecycle starter pack

These documents support a future risk-based validation effort. They are templates, not
executed validation or evidence that the system is in a validated state.

## Intended use

EMA Flow deterministically converts approved, authored ePI product and document inputs into
an EMA-profiled FHIR representation, rejects nonconforming outputs, stores successful outputs,
and exposes their near-real-time analytical representation.

It is not intended to:

- author or approve regulated clinical narrative;
- replace regulatory, medical, quality, or legal review;
- submit directly to EMA;
- provide patient-specific data or clinical decisions;
- provide a human electronic-signature workflow; or
- structure documents without human review and approval (Zone A proposals are never
  persisted unapproved).

## Required lifecycle records

Before regulated use, the owning organization should approve:

1. GxP impact and data-integrity risk assessment;
2. user requirements and acceptance criteria;
3. functional/configuration specification and system inventory;
4. supplier and Google Cloud service assessment;
5. requirements-to-test traceability;
6. installation/configuration qualification;
7. operational and negative-path qualification;
8. performance qualification using representative approved data;
9. security, access, backup/restore, disaster-recovery, and audit-trail tests;
10. deviations, CAPA, release, change-control, and periodic-review procedures;
11. retention, legal hold, data export, and decommissioning procedures; and
12. role training and segregation-of-duties evidence.

## Initial traceability matrix

| Requirement                                                               | Risk                                       | Design control                                                                                                                                                                                                                                              | Automated evidence                                                                                                         |
| ------------------------------------------------------------------------- | ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| UR-01 Preserve authored narrative                                         | Incorrect labeling                         | Byte-preservation assertion                                                                                                                                                                                                                                 | `transform.test.ts`, mapping decisions                                                                                     |
| UR-02 Reject missing QRD sections                                         | Incomplete SmPC                            | Fail-closed mapping                                                                                                                                                                                                                                         | Negative fixture test, OperationOutcome                                                                                    |
| UR-03 Require all selected profiles                                       | Invalid EMA output                         | Explicit profile-by-profile validation                                                                                                                                                                                                                      | HL7 and Healthcare outcomes                                                                                                |
| UR-04 Prevent unvalidated writes                                          | Uncontrolled record                        | Write gate after outcomes                                                                                                                                                                                                                                   | Pipeline tests and transaction receipt                                                                                     |
| UR-05 Prove source-to-target identity                                     | Untraceable transformation                 | SHA-256 hashes and lineage                                                                                                                                                                                                                                  | Signed run manifest                                                                                                        |
| UR-06 Preserve audit history                                              | Data-integrity loss                        | FHIR versioning, retained logs/artifacts                                                                                                                                                                                                                    | Audit log sink and retention in infra/security.tf; restore test not yet automated                                          |
| UR-07 Observe analytical availability                                     | Stale downstream data                      | Native stream and Workflow query                                                                                                                                                                                                                            | Recorded stream lag                                                                                                        |
| UR-08 Control software changes                                            | Unapproved executable                      | Cloud Build image builds; Binary Authorization configurable but not enforced in the prototype (enforce_binary_authorization defaults to false); SLSA provenance and SBOM not yet configured                                                                 | Build records, infra/variables.tf                                                                                          |
| UR-09 Narrative fidelity to source document                               | AI-altered labeling                        | Every narrative block byte-matches one source span after pinned normalization; any miss rejects                                                                                                                                                             | test/fidelity.test.ts, test/fixtures/fidelity/vectors.json                                                                 |
| UR-10 Provenance completeness                                             | Untraceable section origin                 | Section-to-provenance bijection and per-section hashes enforced at ingress                                                                                                                                                                                  | test/contracts/canonical-submission.test.ts, test/pipeline.test.ts                                                         |
| UR-11 Approval boundary and attribution                                   | Unreviewed content persisted               | Zone B recomputes `approvedContentSha256` and rejects mismatches before transform                                                                                                                                                                           | test/contracts/canonical-submission.test.ts, test/pipeline.test.ts                                                         |
| UR-12 Contract versioning and compatibility                               | Silent hand-off drift                      | Zod source of truth, generated JSON Schema checked in, `contracts:check`                                                                                                                                                                                    | test/contracts/schema-generation.test.ts, contracts/generated/index.json, scripts/ci/check-generated.mjs                   |
| UR-13 Codes only from terminology                                         | Invented or incorrect coding               | Code-mapped decisions require a terminology lookup receipt and a declared terminology service                                                                                                                                                               | test/contracts/canonical-submission.test.ts                                                                                |
| UR-14 AI output non-authoritative                                         | AI proposal treated as record              | No write path accepts an unapproved submission; the gate precedes transformation                                                                                                                                                                            | test/contracts/canonical-submission.test.ts, test/pipeline.test.ts                                                         |
| UR-15 Continuous integration gate                                         | Unchecked change merged                    | GitHub Actions runs `npm run check` on pull requests and pushes                                                                                                                                                                                             | .github/workflows/ci.yml, .github/workflows/deploy.yml (Quality gate step)                                                 |
| UR-16 No narrative in logs or manifests                                   | Regulated text leakage                     | Key-name redaction plus a value guard (strings over 512 characters or containing '<' are dropped from log fields and redact the message); manifests, reports, and Provenance carry hashes, counts, enumerations, identifiers only                           | test/logger.test.ts, test/no-narrative-leak.test.ts                                                                        |
| UR-17 Submission transport integrity                                      | Substituted or oversized hand-off          | Reads confined to the configured submission bucket, object size capped, JSON depth bounded before hashing, and every part hash-checked against the value the caller or the submission pinned; failures are closed reason codes carrying no document content | test/submission-reader.test.ts, test/app.test.ts                                                                           |
| UR-18 Query service is read-only                                          | Uncontrolled record change                 | No write tool in the published contract; service account holds `fhirResourceReader` (dataset) and `logWriter` only                                                                                                                                          | test asserting the Terraform role set exactly and the contract's tool list; effective-IAM export at deployment             |
| UR-19 Entitlement resolved before any store read                          | Cross-tenant disclosure                    | `entitlementsFor(principal)` applied before any read; outside entitlement every document is `document-not-found`                                                                                                                                            | two-organisation negative suite, every tool × every argument shape; assertion that no response body carries `not-entitled` |
| UR-20 Existence not disclosed by error code, count, or latency            | Cross-tenant inference                     | Closed returnable error codes; fixed-cost miss path (phase 2 — timing is a disclosed residual until then)                                                                                                                                                   | code-path test; latency-distribution test entitled miss vs unentitled miss                                                 |
| UR-21 Returned narrative byte-identical to the store, hashes recomputable | Incorrect labelling via retrieval          | `get_section` returns `div` verbatim with `narrativeDivSha256` and `normalizedTextSha256`                                                                                                                                                                   | byte-equality test and independent hash recomputation in the test                                                          |
| UR-22 Quote verification uses the publishing gate's normalisation         | False assurance to a reviewer              | `verify_quote` runs `src/fidelity/` at the current `NORMALIZATION_VERSION`                                                                                                                                                                                  | positive/negative matrix: ligature, whitespace, straightened quote, flattened superscript, one-word deletion               |
| UR-23 No narrative or argument value in audit records or error bodies     | Regulated text leakage                     | `QueryAuditRecord` carries `argumentsSha256` only; logger guard as defence in depth                                                                                                                                                                         | narrative-leak scan over every audit record and error body emitted by the test suite                                       |
| UR-24 Query audit trail retained, attributable, reviewable                | Loss of accountability                     | Regulated-audit sink; `principal` = token `sub`; `sub` → person mapping held by the organisation's identity provider                                                                                                                                        | sink filter test; locked log bucket (not yet); named reader role; alert on `not-entitled` (not yet)                        |
| UR-25 Caller authentication verified in-service, not only at the edge     | Edge bypass                                | ID token verified against `QUERY_AUDIENCE`; OAuth access token against the connector's client (follow-up)                                                                                                                                                   | negative tests: wrong audience, expired, wrong issuer, unsigned, `alg:none`                                                |
| UR-26 Agent answers carry a verification stamp per quoted span            | Unverified content presented as label text | Post-check through `verify_quote`; card marks `no-match`; quote slots filled by reference to tool results only                                                                                                                                              | `AgentTurnRecord` contract (to be published) and a test that an altered span is stamped unverified                         |
| UR-27 Assistant output is not a record and is not relied on unchecked     | Misuse of an aid as a source               | Procedural control in the intended-use statement of `docs/design/verifiable-answers.md`                                                                                                                                                                     | training record; SOP; periodic review — organisational evidence, not repository evidence                                   |

## Release criteria (target state — not implemented in this repository)

A release candidate is not production eligible until all automated gates pass, the generated
evidence is reviewed, deviations are resolved or accepted, an independent approver authorizes
deployment, and the organization’s quality process records the release decision.

Today there is no branch protection on `main`, no reviewer requirement, and no promotion
approval: author and releaser are the same identity. Closing this is a prerequisite to any
pilot and is listed under "Needs a person" in `docs/roadmap.md`.

Software promotion is currently unattended (`terraform apply` from GitHub Actions after the
quality gate); a human promotion gate is a change-control addition and controls software
promotion only. Product-content approval and electronic signature require a separately
validated business workflow with signer identity,
signature meaning, re-authentication where required, and permanent signature-to-record
linkage.

## Change control for shared, evidenced libraries

This procedure applies to every module whose output is part of an evidence artefact or an
approved hash: the contract schemas (`src/contracts/`, each `schemaVersion`, including
`QUERY_TOOLS_VERSION`, which has an external consumer), the fidelity normalisation procedure
(`docs/fidelity-normalization.md`, `NORMALIZATION_VERSION` — its section 8 is the single
statement of what triggers a change, including a runtime whose Unicode version drifts with no
code change), canonical JSON and hashing (`src/lib/hash.ts` — a change there moves every hash
in every artefact ever produced, including `approvedContentSha256`), and the no-narrative
logging guard (`src/lib/logger.ts`). A change to any of them is a controlled event, not a
routine edit:

0. impact assessment: list every service that imports the module (`src/`, `zone-a/`, `agent/`),
   state for each whether previously produced evidence remains reproducible, and record the
   outcome with the change;
1. bump the version literal (`schemaVersion`, `QUERY_TOOLS_VERSION`, or
   `NORMALIZATION_VERSION`);
2. run `npm run contracts:generate` and `npm run vectors:generate`, and commit the regenerated
   `contracts/generated/**` and `test/fixtures/fidelity/vectors.json`;
3. review every changed vector by hand and record the reason for the change;
4. add adversarial cases exercising the changed behaviour;
5. amend or supersede the relevant ADR (`docs/adr/0002-*.md` or `docs/adr/0003-*.md`);
6. update the traceability rows above whose evidence changed; and
7. treat previously approved submissions as requiring re-approval, because the version is
   part of the approved content hash — an old approval does not carry forward to a new
   version; and
8. obtain approval before merge from a named role other than the author: a quality
   representative for anything that touches an approved hash, the system owner otherwise.
   Until branch protection exists, this approval is recorded in the pull request and is a
   procedural control only.
