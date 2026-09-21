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

| Requirement                                                                | Risk                                       | Design control                                                                                                                                                                                                                                                                                                                                                                                                  | Automated evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| -------------------------------------------------------------------------- | ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| UR-01 Preserve authored narrative                                          | Incorrect labeling                         | Byte-preservation assertion                                                                                                                                                                                                                                                                                                                                                                                     | `transform.test.ts`, mapping decisions                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| UR-02 Reject missing QRD sections                                          | Incomplete SmPC                            | Fail-closed mapping                                                                                                                                                                                                                                                                                                                                                                                             | Negative fixture test, OperationOutcome                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| UR-03 Require all selected profiles                                        | Invalid EMA output                         | Explicit profile-by-profile validation                                                                                                                                                                                                                                                                                                                                                                          | HL7 and Healthcare outcomes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| UR-04 Prevent unvalidated writes                                           | Uncontrolled record                        | Write gate after outcomes                                                                                                                                                                                                                                                                                                                                                                                       | Pipeline tests and transaction receipt                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| UR-05 Prove source-to-target identity                                      | Untraceable transformation                 | SHA-256 hashes and lineage                                                                                                                                                                                                                                                                                                                                                                                      | Signed run manifest                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| UR-06 Preserve audit history                                               | Data-integrity loss                        | FHIR versioning, retained logs/artifacts                                                                                                                                                                                                                                                                                                                                                                        | Audit log sink and retention in infra/security.tf; restore test not yet automated                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| UR-07 Observe analytical availability                                      | Stale downstream data                      | Native stream and Workflow query                                                                                                                                                                                                                                                                                                                                                                                | Recorded stream lag                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| UR-08 Control software changes                                             | Unapproved executable                      | Cloud Build image builds; Binary Authorization configurable but not enforced in the prototype (enforce_binary_authorization defaults to false); SLSA provenance and SBOM not yet configured                                                                                                                                                                                                                     | Build records, infra/variables.tf                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| UR-09 Narrative fidelity to source document                                | AI-altered labeling                        | Every narrative block byte-matches one source span after pinned normalization; any miss rejects                                                                                                                                                                                                                                                                                                                 | test/fidelity.test.ts, test/fixtures/fidelity/vectors.json                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| UR-10 Provenance completeness                                              | Untraceable section origin                 | Section-to-provenance bijection and per-section hashes enforced at ingress                                                                                                                                                                                                                                                                                                                                      | test/contracts/canonical-submission.test.ts, test/pipeline.test.ts                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| UR-11 Approval boundary and attribution                                    | Unreviewed content persisted               | Zone B recomputes `approvedContentSha256` and rejects mismatches before transform                                                                                                                                                                                                                                                                                                                               | test/contracts/canonical-submission.test.ts, test/pipeline.test.ts                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| UR-12 Contract versioning and compatibility                                | Silent hand-off drift                      | Zod source of truth, generated JSON Schema checked in, `contracts:check`                                                                                                                                                                                                                                                                                                                                        | test/contracts/schema-generation.test.ts, contracts/generated/index.json, scripts/ci/check-generated.mjs                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| UR-13 Codes only from terminology                                          | Invented or incorrect coding               | Code-mapped decisions require a terminology lookup receipt and a declared terminology service                                                                                                                                                                                                                                                                                                                   | test/contracts/canonical-submission.test.ts                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| UR-14 AI output non-authoritative                                          | AI proposal treated as record              | No write path accepts an unapproved submission; the gate precedes transformation                                                                                                                                                                                                                                                                                                                                | test/contracts/canonical-submission.test.ts, test/pipeline.test.ts                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| UR-15 Continuous integration gate                                          | Unchecked change merged                    | GitHub Actions runs `npm run check` on pull requests and pushes                                                                                                                                                                                                                                                                                                                                                 | .github/workflows/ci.yml, .github/workflows/deploy.yml (Quality gate step)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| UR-16 No narrative in logs or manifests                                    | Regulated text leakage                     | Key-name redaction plus a value guard (strings over 512 characters or containing '<' are dropped from log fields and redact the message); manifests, reports, and Provenance carry hashes, counts, enumerations, identifiers only                                                                                                                                                                               | test/logger.test.ts, test/no-narrative-leak.test.ts                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| UR-17 Submission transport integrity                                       | Substituted or oversized hand-off          | Reads confined to the configured submission bucket, object size capped, JSON depth bounded before hashing, and every part hash-checked against the value the caller or the submission pinned; failures are closed reason codes carrying no document content                                                                                                                                                     | test/submission-reader.test.ts, test/app.test.ts                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| UR-18 Query service is read-only                                           | Uncontrolled record change                 | No write tool in the published contract (`query-tools` 2.0.0); service account holds `fhirResourceReader` (dataset) and `logWriter` (project) only                                                                                                                                                                                                                                                              | `test/query/tool-surface.test.ts` "advertises exactly the four read-only tools of the contract"; `test/query/acceptance.test.ts` "least privilege, proven"; effective-IAM export after each successful apply — `export_effective_iam` in `scripts/gcp/deploy.sh` runs `gcloud projects get-iam-policy` and `gcloud healthcare datasets get-iam-policy` filtered to the worker, the query service and the impersonation-only caller accounts (the caller's two policies are expected to be empty, which is the point of exporting it), prints the JSON into the deploy log and copies it to `gs://<evidence bucket>/deploy-evidence/<YYYY>/<MM>/<DD>/<UTC stamp>-<env>-<commit>/`; every step is warning-only and never fails a deploy, so a missing permission leaves a `::warning::` and no file. No deployment has run, so no export exists yet |
| UR-19 Entitlement resolved before any store read                           | Cross-tenant disclosure                    | `entitlementsFor(principal)` resolved once per request, immediately after the principal, before any read; outside entitlement every document is `document-not-found`                                                                                                                                                                                                                                            | `test/query/acceptance.test.ts` "the tenant wall" (two principals with disjoint bundle lists, every tool, every argument shape that can name a document — including a `verify_quote` whose quote carries a character the normalisation forbids, because entitlement is decided before the quote is normalised; audit outcome `not-entitled` with no `versionId`; no response body carries `not-entitled`)                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| UR-20 Existence not disclosed by error code, count, or latency             | Cross-tenant inference                     | Closed returnable error codes exclude `not-entitled`; a fixed-cost miss path is phase 2 — timing is a disclosed residual until then                                                                                                                                                                                                                                                                             | `test/query/acceptance.test.ts` "the tenant wall" (error code); `agent/tests/test_contract.py::test_not_entitled_is_no_longer_an_error_code_a_caller_can_see`; no latency-distribution test exists                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| UR-21 Returned narrative byte-identical to the store, hashes recomputable  | Incorrect labelling via retrieval          | `get_section` returns `div` verbatim with `narrativeDivSha256` and `normalizedTextSha256`                                                                                                                                                                                                                                                                                                                       | `test/query/acceptance.test.ts` "verbatim with citations" (byte equality; both hashes recomputed independently in the test)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| UR-22 Quote verification uses the publishing gate's normalisation          | False assurance to a reviewer              | `verify_quote` runs `src/fidelity/` at the current `NORMALIZATION_VERSION`; candidates are narrative-bearing sections, first match wins, only the matched text is hashed                                                                                                                                                                                                                                        | `test/query/acceptance.test.ts` "is this quote accurate?" (ligature, whitespace, straightened quote, flattened superscript, one-word deletion; `sectionsSearched` for a first- and a last-section match)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| UR-23 No narrative or argument value in audit records or error bodies      | Regulated text leakage                     | `QueryAuditRecord` carries `argumentsSha256` only; logger guard as defence in depth                                                                                                                                                                                                                                                                                                                             | `test/query/acceptance.test.ts` "no narrative anywhere but the answer"; `test/query/http.test.ts` "writes nothing about a token to any log line"                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| UR-24 Query audit trail retained, attributable, reviewable                 | Loss of accountability                     | Regulated-audit sink to the retained log bucket (`infra/security.tf`); `principal` = token `sub`; `sub` → person mapping held by the organisation's identity provider; log-bucket lock available behind `lock_regulated_audit_log_bucket` (default `false`)                                                                                                                                                     | No sink-filter test exists. Lock: variable present, not set (irreversible once set). Alert on `not-entitled`: log metric created on every apply, e-mail channel and policy only when `alert_notification_email` is set (`infra/observability.tf`) — `terraform validate` only, no apply. The written line now carries `credentialType` (see UR-43). Open gap: no reader role scoped to the retained log bucket exists, so read access is whatever the project's logging roles allow                                                                                                                                                                                                                                                                                                                                                               |
| UR-25 Caller authentication verified in-service, not only at the edge      | Edge bypass                                | JWT-shaped bearer verified as an ID token against `QUERY_AUDIENCE` (signature and expiry by `google-auth-library`, issuer and `sub` shape by the service); any other bearer verified as an OAuth access token through tokeninfo against `QUERY_OAUTH_CLIENT_IDS`                                                                                                                                                | `test/query/auth.test.ts` "routes a JWT-shaped bearer to ID-token verification and anything else to tokeninfo", "rejects an ID token for another audience or issuer, and one whose sub is not a principal id"; `test/query/http.test.ts` "rejects a token issued for another audience", "rejects a token the verifier does not accept". The Google client is stubbed in every test: signature, `alg:none`, and ID-token expiry rejection are the library's behaviour and are not tested here                                                                                                                                                                                                                                                                                                                                                      |
| UR-26 Agent answers carry a verification stamp per quoted span             | Unverified content presented as label text | Post-check through `verify_quote`; card marks `no-match`; quote slots filled by reference to tool results only; `AgentTurnRecord` published as contract `agent-turn` 1.0.0                                                                                                                                                                                                                                      | `agent/tests/test_postcheck.py::test_an_altered_block_comes_back_no_match_and_is_flagged`; `agent/tests/test_turn.py::test_a_block_the_store_no_longer_contains_is_flagged_on_the_card`; `agent/tests/test_compose.py::test_every_block_is_verbatim_from_its_tool_result_with_its_citation`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| UR-27 Assistant output is not a record and is not relied on unchecked      | Misuse of an aid as a source               | Procedural control in the intended-use statement of `docs/design/verifiable-answers.md`                                                                                                                                                                                                                                                                                                                         | training record; SOP; periodic review — organisational evidence, not repository evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| UR-28 JSON-RPC batch bounded before dispatch                               | Resource exhaustion, unaudited fan-out     | A body that is an array longer than `MAX_BATCH_MESSAGES` (8) is answered `400` after the body is read and before the transport is connected                                                                                                                                                                                                                                                                     | `test/query/http.test.ts` "caps a JSON-RPC batch before the transport sees it" (nine: no tool run, no read, no record; eight: one record per entry)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| UR-29 Audit records are truthful for notifications and refused requests    | Forged or missing audit entries            | Only `tools/call` entries with a JSON-RPC id are pending; a request the transport refused before the handler ran gets one `invalid-request` record; a notification gets none                                                                                                                                                                                                                                    | `test/query/http.test.ts` "audits no record for a notification, and exactly one for a request the transport refused"                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| UR-30 Authentication and entitlement refused before the protocol           | Authenticated stranger enumerates tools    | `401` with a log line carrying nothing from the credential; `403` for a verified principal with no entitlement, before the transport is connected, so `tools/list` is never reached                                                                                                                                                                                                                             | `test/query/http.test.ts` "logs a rejected authentication as a structured warning with nothing from the token", "refuses an authenticated principal with no entitlement before the protocol"                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| UR-31 `find_product` bounded and honest about truncation                   | Silent "no such product"; unbounded reads  | Reads at most the first 200 entitled ids (`FIND_PRODUCT_SCAN_HORIZON`) through a pool of 8, stops launching at `limit`, stops when the request's read budget is spent; `truncated` true whenever the answer is shorter than the entitlement holds — documents left unsearched, or matches dropped by `limit` — in result and audit record; the agent's instruction covers it                                    | `test/query/acceptance.test.ts` "find_product reads at most the scan horizon, and stops at the limit", "find_product reports truncated whenever the limit stopped the scan short"; `agent/tests/test_contract.py::test_find_product_requires_truncated_and_accepts_either_value`; `agent/tests/test_toolset_wiring.py::test_a_truncated_product_search_is_available_and_says_so`; `agent/tests/test_agent.py::test_the_instruction_covers_a_truncated_product_search` (instruction text only — model behaviour is not tested)                                                                                                                                                                                                                                                                                                                     |
| UR-32 Access-token verification: routing, audience, expiry, cache, no leak | Wrong-client or expired token accepted     | Access token accepted only with `expiry_date` in the future, `aud` or `azp` in `QUERY_OAUTH_CLIENT_IDS`, a `sub` satisfying `PrincipalId`; rejected outright when the list is empty; cache keyed by token SHA-256, at most 300 s, never past expiry, at most 1,000 entries, failures never cached                                                                                                               | `test/query/auth.test.ts` "rejects an access token presented to a client id that is not configured", "rejects an expired access token and one without a sub", "rejects every access token when no OAuth client id is configured, without asking Google", "serves a repeated access token from the cache until its bounded lifetime ends", "caches no longer than the token's own remaining lifetime", "bounds the cache and evicts the oldest entry first", "never writes a token or its hash to a log line, on any path"                                                                                                                                                                                                                                                                                                                         |
| UR-33 Least privilege asserted across every infrastructure file            | Role widened in a file the test ignores    | The test reads every `infra/*.tf`, requires at least one, requires a query service account block, and matches `*_iam_member`, `*_iam_binding`, and `*_iam_policy` resources naming it                                                                                                                                                                                                                           | `test/query/acceptance.test.ts` "least privilege, proven" (fails rather than skips when files are absent)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| UR-34 Approver role carried on the persisted Provenance                    | Approver role inferred or missing          | The projection writes the role coding on the attester agent under `APPROVER_ROLE_SYSTEM`; `get_provenance` reads only that coding and answers `unavailable` without it                                                                                                                                                                                                                                          | `test/provenance.test.ts` "names the approver and the approver's role on the attester agent"; `test/query/acceptance.test.ts` "prove where it came from", "answers unavailable when the persisted Provenance omits the approver role". Deployment note: every document persisted before this projection change carries no role coding and is answered `unavailable`, so the worker must be redeployed and the documents re-ingested before `get_provenance` answers an approver — the order is in `docs/roadmap.md` and `docs/demo/verifiable-label.md`                                                                                                                                                                                                                                                                                           |
| UR-35 Container images pinned by digest and in agreement                   | Unpinned base image; Unicode drift         | `scripts/ci/check-dockerfiles.mjs` (`npm run images:check`, part of `npm run check`) fails any root `Dockerfile*` that pulls image bytes from a reference without `@sha256:<64 hex>` — `FROM`, `--from=` on `COPY` and `ADD`, and `from=` inside `RUN --mount=` — skipping references naming a stage declared in the same file or a stage index, and fails any digest disagreement among Node-based Dockerfiles | `test/ci/check-dockerfiles.test.ts` "passes a file whose every image reference is a digest or a declared stage", "fails an unpinned FROM", "fails an unpinned image pulled through COPY --from", "fails an unpinned image pulled through ADD --from", "fails an unpinned image mounted into a RUN", "fails an unpinned reference written after a line continuation", "fails when two Dockerfiles start FROM node images pinned to different digests", "fails when the directory holds no Dockerfile at all". The scan is textual: it joins line continuations and drops comments but does not resolve build arguments (`FROM ${BASE}`, `--from=$STAGE`) and contacts no registry, so it proves a digest was written, not what the registry serves. No image was built                                                                             |
| UR-36 Run sources outside the document gate can be disabled                | Ungated source accepted in production      | `ENABLED_RUN_SOURCES` (Terraform `enabled_run_sources`) checked after body parsing and before any reader, fixture, client, or store; a disabled source answers `422 source-disabled`                                                                                                                                                                                                                            | `test/run-sources.test.ts` "rejects a disabled source before any reader, fixture, or client is touched", "rejects a disabled document source ahead of the bucket-not-configured answer", "rejects an unknown source name, an empty value, and a dangling comma", "lists exactly the sources the RunRequest contract names" — merged into this tree; the Terraform variable was checked by `terraform validate` only, and the default is still all three sources                                                                                                                                                                                                                                                                                                                                                                                   |
| UR-37 The agent's turn record is the published contract                    | Unassessable agent evidence                | `TurnAuditRecord` mirrors `AgentTurnRecord` (`agent-turn` 1.0.0): four tool names, six flags, contract patterns for `serviceVersion`, `principal`, `turnId`, `tools` capped at 200; a turn over the cap gets no record rather than a cut one                                                                                                                                                                    | `agent/tests/test_audit.py::test_an_emitted_record_validates_against_the_published_agent_turn_contract`, `::test_a_record_with_no_tool_calls_and_no_flags_also_validates`, `::test_the_record_carries_exactly_the_contracts_fields`, `::test_the_patterns_the_model_enforces_are_the_contracts`, `::test_a_turn_id_that_is_not_a_uuid_makes_no_record`, `::test_a_tool_outside_the_four_makes_no_record`, `::test_a_flag_outside_the_six_makes_no_record`, `::test_more_tool_calls_than_the_contract_allows_makes_no_record_rather_than_a_cut_one`; `agent/tests/test_contract.py::test_each_vendored_contract_is_the_published_one`, `::test_the_two_schemas_are_the_versions_the_agent_was_adapted_to`                                                                                                                                          |
| UR-38 Query and agent records join on one turn id                          | Unjoinable audit trails                    | The agent generates a UUID before the model runs, sends `X-Query-Turn-Id` on every request of the turn, and records it as `turnId`; the service copies a UUID header into every record of the request and refuses a non-UUID with `400`                                                                                                                                                                         | `test/query/http.test.ts` "threads a declared turn id into every audit record of the request", "refuses a turn id that is not a UUID before the protocol, and audits nothing"; `agent/tests/test_agent.py::test_the_turn_id_is_generated_before_the_agent_runs`; `agent/tests/test_toolset_wiring.py::test_begin_turn_puts_a_fresh_uuid_in_state_that_the_header_provider_then_sends`, `::test_the_turn_id_reaches_the_service_on_every_request_of_the_turn`, `::test_a_turn_id_that_is_not_a_uuid_fails_the_call_closed`, `::test_the_missing_token_is_checked_before_the_turn_id`; `agent/tests/test_turn.py::test_an_honest_turn_verifies_every_block`. Proven piecewise with in-process ADK contexts; not exercised on a deployed runtime                                                                                                     |
| UR-39 Audit record names the code, image, credential kind, and version     | Untraceable answer                         | `serviceVersion`, `imageDigest` (`sha256:<64 hex>`), `credentialType` as the verifier reported it, `versionId` of the document actually read                                                                                                                                                                                                                                                                    | `test/query/http.test.ts` "answers a tool call over the streamable HTTP transport", "records the credential kind the verifier reported"; `test/query/acceptance.test.ts` "verbatim with citations". The written log line carries `credentialType` too (UR-43)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |

| UR-40 A body the transport cannot answer is refused before dispatch | Hung request; audit trail wider than what the caller saw | `refusedBodyShape` answers `400 invalid-request` after the batch cap and before the transport is connected for a body that repeats a JSON-RPC id and for one carrying a `notifications/cancelled` that names a request id in the same body; ids are compared by value and type, as the SDK's own map keys them | `test/query/http.test.ts` "refuses a batch that cancels one of its own requests, before the transport sees it", "refuses a batch that repeats a JSON-RPC id, before the transport sees it" — no tool run, no store read, no record; a cancellation naming an id the body does not carry is not refused; the number `1` and the string `"1"` are different ids. The guard was shown not to be vacuous by removing it, which made the test time out instead of pass |
| UR-41 A request the transport never answers ends bounded and audited exactly once | Request slot held open; missing or duplicated audit record | `awaitTransport` races the transport against a 30 s deadline (`REQUEST_DEADLINE_MS`, half the Cloud Run request timeout in `infra/query.tf`) and the response's `close` event, classified by `writableFinished`; on either end it closes the transport and the per-request server, answers `503 unavailable`, writes one record for every pending call (`unavailable` for a dispatched call, `invalid-request` for one the transport refused) and seals the request so a later finish adds no second record | `test/query/http.test.ts` "answers at the deadline instead of holding the request open, and audits the call once", "stops waiting when the client disconnects, and says so on one warning line". Not covered: a SIGTERM mid-request writes no record at all (`src/query/server.ts` has no graceful shutdown); memory release after the SDK's abandoned promise is not measured; a partially written response would be classified as a disconnect and was not constructed |
| UR-42 Store reads bounded per request | Read amplification through a batch | `REQUEST_READ_BUDGET` = 400 store reads per HTTP request, one budget shared by every call in the JSON-RPC batch, taken after the entitlement decision so an exhausted budget can never turn a `not-entitled` record into `unavailable`; `find_product` stops scanning and reports `truncated: true`, every other tool answers `unavailable` without reading; a `get_section` costs 2 because its provenance lookup is a read | `test/query/acceptance.test.ts` "bounds the store reads one request may make, and says so"; `test/query/http.test.ts` "shares one store-read budget across every call in a batch". The figure 400 is a judgement, not a measurement, and it bounds one request only — nothing limits how many requests a principal sends |
| UR-43 The written audit line is the published record | Retained evidence narrower than the contract claims | `credentialType` is exempt from the shared logger's forbidden-key pattern (`src/lib/logger.ts`), and the exemption is a name paired with a value shape: a value that is not a short lowercase token is dropped, so the key cannot carry text whatever its length. `credential` and `credentials` are still dropped | `test/query/acceptance.test.ts` "the written audit line is the published record" (lines parsed back, the logger's four fields stripped, validated against `QueryAuditRecordSchema` and compared field for field with the records the service built); `test/logger.test.ts` "keeps credentialType while still dropping credential and credentials" |
| UR-44 A person authenticates with their own Google account only through the access-token path | A documented path that cannot work; an audience widened without intent | `gcloud auth print-identity-token --audiences=` is refused for a user account, so the only route for a human's own Google account is an access token whose tokeninfo `aud`/`azp` is listed in `QUERY_OAUTH_CLIENT_IDS`; that list is empty by default and an entry there never widens the accepted ID-token audiences | `test/query/auth.test.ts` "accepts a user access token whose tokeninfo names a configured client id" — refused while the list is empty, accepted as `credentialType: access-token` once the id is configured, never routed to ID-token verification. Tested against a fake tokeninfo response with an invented client id; Google's tokeninfo endpoint was not called from the tests and nothing was run against a deployed service. The gcloud client id is shared by every gcloud installation, so it identifies the tool and not the caller: `run.invoker` and the per-subject entitlement are the only remaining walls |
| UR-45 The ID token audience is one named string, not "the service URL" | 401 on every call, looking like a service fault | `QUERY_AUDIENCE` is Cloud Run's deterministic `https://<service>-<project number>.<region>.run.app`; Terraform publishes it as output `query_audience`, the request endpoint as `query_service_url` (the same string unless `var.query_audience` overrides it) and every hostname as `query_service_urls`, whose description states it is not an audience; a postcondition fails the apply if the audience is not a URL Cloud Run reports for the service | No test. `infra/outputs.tf`, `infra/query.tf`, `terraform validate` only. An apply on 2026-09-20 created `ema-flow-dev-query`, so the postcondition passed against a real Cloud Run URL and both outputs carry applied values; the URL shapes were confirmed against the already-deployed worker in the same project and region. No request has yet been made against the deployed query service, so the 401 path remains reproduced in review against the service's verification code rather than observed |
| UR-46 Official HL7 validation runs before merge, and a deploy proves one run completes | Non-conformant content reaches the pipeline unseen; a deploy reads as green while the pipeline has never completed a run | CI job "Official validation" (`scripts/ci/official-validate.mjs`, `npm run validate:official`) runs the pinned `validator_cli.jar` with the four pinned packages and the sidecar's flags, all parsed from `Dockerfile.validator`, over the four resources a fixture run sends the worker, against the worker's profiles; fails on any `Error @` or `Fatal @` line and on a run that did not load every package. After every apply, `scripts/gcp/deploy.sh smoke` POSTs `{"source":"fixture"}` to the deployed worker as the deployer and requires HTTP 200 with status `persisted`; `422 source-disabled` skips with a notice | Run locally 2026-09-20 against the pinned validator: 22 error lines on the tree before the conformance change (13 source Type 2, 1 EMA List, 6 EMA Bundle, 2 EMA Composition — the Composition's two are the same display-name errors the Bundle reports, so 20 distinct), 1 on a hand-fixed copy of the Composition, and 0 on the tree as it now stands, so the verdict comes from the output and not the exit code. Failure paths exercised: the List code and `Bundle.language` put back produced exactly those two validator errors and exit 1; a package altered by one byte failed on its pinned checksum. First GitHub Actions run 2026-09-20T23:44Z on `c7e1ebc` (run 35545408243): "Official validation" green, all five artefacts checksum-verified, `total: 0 errors across 4 resources`. The smoke step has not run: no deploy has followed these commits. Branch protection on `main` exists but lists contexts by display name (`Check`, `Zone A`, `Agent`), so adding "Official validation" is a setting a person must make (see "Official validation gate") |

## Official validation gate

Until 2026-09-20 the official HL7 validator ran in exactly one place: the sidecar of the deployed
worker. `npm run check` and `npm run demo` run the local structural preflights only. The worker
had never completed a run in this project, and the reason was that its own synthetic fixture
did not conform: 20 distinct errors across the source Type 2 Bundle, the EMA List and the EMA
document Bundle. Three of those were defects that would reach a real label, because they live in
the mapping and the transform rather than the fixture: two QRD display names, and one List code
absent from the EMA code system. The rest, including the empty coding array, were defects of the
synthetic fixture's own product graph and could not affect a real submission. The pipeline failed
closed, as designed, and nothing outside the pipeline had ever asked the validator the question.
Two gates close that.

**Before merge — `npm run validate:official`** (`scripts/ci/official-validate.mjs`, the
"Official validation" job in `.github/workflows/ci.yml`):

- One source of truth. The validator version, the four package URLs, every SHA-256 and the
  sidecar's validation flags (`-version 5.0.0 -tx n/a` and the ordered `-ig` list) are parsed
  from `Dockerfile.validator` at run time. A Dockerfile whose `ARG`, `RUN` or `CMD` lines cannot
  be parsed fails the run; nothing is restated in the script or the workflow.
- The same four resources, against the same profiles. `scripts/ci/emit-validation-set.ts`
  builds the set the way `src/app.ts` and `src/pipeline.ts` do for `{"source":"fixture"}`:
  `createSyntheticType2Bundle` through `transformType2ToEma`, the two structural preflights
  first, then the source Bundle against the Global ePI Bundle profile, the List and the document
  Bundle against the EMA profiles, and the Composition against its four profiles in one run.
- The verdict is read from the output, not the exit code. Every `Error @` and `Fatal @` line is
  printed and counted; a run that does not report every `-ig` package loaded fails as a
  validator failure (a validator that runs without its packages reports zero errors and exits
  0); a non-zero exit with no error line is a validator failure, not a pass.
- Downloads are cached by the Dockerfile's content and verified against the pinned checksum
  before every use, so a stale or tampered cache entry is re-downloaded, never trusted. The job
  is separate from `npm run check` because it needs a JVM and about 200 MB of downloads.
- It validates the synthetic fixture only. A real document run's content is validated by the
  worker at run time; this gate proves the mapping and the fixture conform, and nothing about
  any particular submission.
- **It is hermetic, as of 2026-09-21, and so is the deployed sidecar.** Until then it was not.
  Measured that day in a clean sandbox with exactly the gate's flags: alongside the four
  checksum-pinned packages the validator installed **nine** further packages from the FHIR
  registry over the network — `hl7.fhir.r5.core` 5.0.0 (the base specification itself),
  `hl7.fhir.xver-extensions` 0.1.0, `hl7.terminology` 7.3.0, `hl7.terminology.r5` 5.0.0, 6.2.0
  and 7.1.0, and `hl7.fhir.uv.extensions.r5` 1.0.0, 5.2.0 and 5.3.0. An earlier measurement had
  counted seven, missing the first two. The pinned `extensions-package.tgz` loaded 0 resources,
  because the identical package had been fetched first. The CI job also restored an earlier
  copy of `~/.fhir/packages` from any previous key, so on a warm runner the downloads were
  hidden and the cache could hold packages nobody had pinned.

  The deployed sidecar did the same **on every cold start**: its log for the revision deployed
  at 17:16 UTC shows each package installed from the network, the validator ready at 48
  seconds and the worker at 57. So the pipeline's first run after an idle period waited about a
  minute, could not start at all if the registry was unreachable, and validated against
  whatever the registry served. The same log showed a second gap: with no flags to say
  otherwise, the validator took its locale and jurisdiction from the container — **United
  States** — while validating EMA content.

  The fix, one list read three ways:

  - `fhir/validator-packages.lock` pins the nine packages by the SHA-256 of their registry
    tarballs. Every content file of each tarball was compared with what the validator had
    installed from the network and found byte-identical; only the validator's own
    `.index.json` files differ, and it regenerates those.
  - `Dockerfile.validator` installs them into the image's package cache at build time, each
    verified before it is unpacked. The validator runs with `-no-http-access` — its own switch,
    which refuses every HTTP(S) request inside the application — and, as a second layer, a JVM
    proxy on a closed local port. It validates with `-jurisdiction uv -locale en-US`, which it
    reports as `Jurisdiction: Global (Whole world)` and `Locale: United States/US`.
  - This gate seeds its own cache from the same list and runs with the sidecar's own JVM
    properties and flags. It fails if the validator installs anything (a needed package is not
    listed), if any fetch gets as far as a socket, or if the validator's own `Package Summary`
    names any package outside the pinned set — the nine listed plus the four `-ig` files, by the
    id each declares in its `package.json`. A lookup the policy refused is reported, not failed:
    the validator checks for a newer `hl7.terminology` on every run, the first CI run with the
    network closed showed it, and it recovers from the refusal with the pinned version.
  - Every image build starts the validator image with `--network none` in Cloud Build and fails
    unless it comes up without reaching for the network (`cloudbuild.images.yaml`,
    step `validator-starts-offline`).

  `-no-http-access` also closes a request-forgery path. The validator's own documentation warns
  that content being validated can direct it to fetch URLs of the content's choosing, including
  internal network addresses — on Cloud Run that includes the metadata server, which serves
  credentials over plain HTTP — and the sidecar validates content that arrives in submissions.

  Blocking the network was tested before it was relied on: with an empty cache and the closed
  proxy, the validator refused to start — `Error fetching … Failed to connect to /127.0.0.1:9`,
  `Unable to load validationEngine` — rather than proceeding without its packages.

**After deploy — `scripts/gcp/deploy.sh smoke`** (step "Smoke run through the deployed worker"
in `.github/workflows/deploy.yml`, after the stores are reconciled and the profiles imported):
one `{"source":"fixture"}` POST to the deployed worker, authenticating as the deployer service
account. That account can already invoke the worker through its project-level `roles/run.admin`,
which contains `run.routes.invoke`; `infra/run.tf` additionally declares an explicit
`roles/run.invoker` binding (`google_cloud_run_v2_service_iam_member.deployer_invoker`, from the
`deployer_account` the apply passes), which states the entitlement at the resource level and is
what the call would fall back on if that project role were ever narrowed. The ID token is minted by the workflow's `google-github-actions/auth` step and
carried to the script in `WORKER_ID_TOKEN`, not minted by gcloud: a deploy runs under an
external-account credential and gcloud refuses `print-identity-token --audiences=` for those,
exactly as it refuses a human's account. The script falls back to gcloud only for the credential
kinds that do support the flag. The deploy fails unless the answer is HTTP 200 with status `persisted`; any other
answer prints the closed `reason` (for example `official-validation-failed`) and nothing else
from the body. A `422 source-disabled` answer — an environment whose `enabled_run_sources`
excludes `fixture`, as production should — skips the step with a notice, because that answer is
the allowlist working as configured.

**Status (2026-09-20).** The two gates are evidenced to different depths, and the difference
matters.

The CI gate was exercised locally against the pinned validator: 22 error lines on the tree
before the conformance change, 1 on a hand-fixed copy, and 0 on the tree as it now stands. Its
failure paths were exercised too — a copy with the List code and `Bundle.language` put back
produced exactly those two validator errors and exit 1, and a package altered by one byte failed
on its checksum. It first ran in GitHub Actions on 2026-09-20 (run 35545408243, green on
`c7e1ebc`, every artefact checksum-verified and 0 errors across 4 resources), but it is not a
required check: branch protection lists contexts by display name, so "Official validation" must
be added by a person or the job will run without blocking a merge.

The smoke step is evidenced far more thinly. Its verdict logic was exercised on sample answers
only; no run against a deployed worker has happened. The ID-token source was corrected after
review — gcloud cannot mint one from the external-account credential a deploy runs under — but
that correction is itself reasoned from gcloud's documented behaviour and its source, not from
an observed green run. The IAM propagation retry and the `run.invoker` grant are likewise
written from documentation and review. Treat the first deploy after this change as the test of
this step, not as a confirmation of it.

## Runs of record

The runs this document's claims rest on, so a reader can check them rather than take them. All
are in project `sage-ship-509104-b8`, region `europe-west4`, against synthetic product
information only. Evidence artefacts are under
`gs://sage-ship-509104-b8-ema-flow-dev-evidence/runs/<run id>/`; ledger rows are in
`ema_flow_ledger_dev.transformation_runs`.

| Run id                                 | Source     | Date       | What it establishes                                                                                                                                                                                                                                                                                                                                                     |
| -------------------------------------- | ---------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `c0648d78-5a4e-403e-af70-31c3775ccd92` | `fixture`  | 2026-09-21 | The first run ever to complete in this project. `persisted`; 0 preflight, official and Cloud Healthcare validation errors; 32 mapping decisions; 7 artefacts. Its manifest hash was recomputed from the manifest with the repository's own hash function and its signature verified against the KMS public key with RSA-PSS SHA-256 (`Signature Verified Successfully`) |
| `23481d23-90be-45a3-884e-b83384837fd1` | `document` | 2026-09-21 | The first `document` run to complete: `synthetic-paracetamol` v1 through the ingress gate                                                                                                                                                                                                                                                                               |
| `30f08ec0-a23e-4736-8cb1-58d9ea0540bc` | `document` | 2026-09-21 | `synthetic-demoxetine` v1. The document `get_section`, `get_provenance` and `verify_quote` were proved against                                                                                                                                                                                                                                                          |
| `a6ea3c5d-4a90-4d27-95ac-68d7f10dbb7c` | `document` | 2026-09-21 | `synthetic-placebolol` v1                                                                                                                                                                                                                                                                                                                                               |
| `24e8aa7d-b588-4fc6-8186-abc7ecc5239c` | `document` | 2026-09-21 | `synthetic-paracetamol` v2, a second version of an existing document rather than a new one                                                                                                                                                                                                                                                                              |
| `627d1dbb-348f-43c3-9535-1d2a64594286` | `fixture`  | 2026-09-21 | The local runner (`scripts/dev/run-pipeline.ts --persist`) executing the whole pipeline from a laptop against the deployed environment own configuration: both validators executed with 0 errors, signed, `persisted`, in **13.7 seconds**                                                                                                                              |
| `95250c7b-51ef-4e5f-8d76-14e97cc96354` | `document` | 2026-09-21 | `synthetic-paracetamol` v1, re-seeded after the store rebuild                                                                                                                                                                                                                                                                                                           |
| `f4e6b7e3-97d5-47bf-8dfc-cf02b53f745b` | `document` | 2026-09-21 | `synthetic-demoxetine` v1, re-seeded                                                                                                                                                                                                                                                                                                                                    |
| `10aeaa11-a548-42f8-ac43-dba26d99d975` | `document` | 2026-09-21 | `synthetic-placebolol` v1, re-seeded                                                                                                                                                                                                                                                                                                                                    |
| `7494fd1b-ae01-4005-a31d-2c57b790d785` | `document` | 2026-09-21 | `synthetic-paracetamol` v2, re-seeded. These four are the documents the store holds now                                                                                                                                                                                                                                                                                 |

Each `document` run wrote 11 evidence artefacts — the 7 a fixture run writes plus
`canonical-submission.json`, `ingestion-provenance.json`, `fidelity-report.json` and
`provenance-resource.json` — and a ledger row carrying `source_kind = document`,
`contract_version = 1.0.0`, `fidelity_status = passed`, and the approval and ingestion source
hashes. The fixture run's row leaves those four columns null, which is what distinguishes the
two paths in the ledger.

**What `verify_quote` was shown to do**, on `2ee34ea0-41f7-587c-a373-0891d915192e` section 4.4:
a verbatim span answered `match` with offsets 0–82 and the section's normalised hash; the same
sentence with the strength changed from 10 mg to 20 mg answered `no-match`; the same sentence
with the negation removed from "not for clinical use" answered `no-match`. That is the system's
central claim — that a quote can be checked rather than trusted — exercised against a published
document rather than a fixture.

Scene 2 of the demonstration was proved the same way on `0c18c50e…` section 4.4 after version 2
was published: the version 2 sentence answered `match` at offsets 54–109, and the version 1
sentence it replaced answered `no-match`. A quote from a superseded version of a label does not
verify against the label as published, which is the point of the scene.

**A traceability wrinkle, stated rather than hidden.** The validated FHIR store was deleted and
rebuilt twice on 2026-09-21 — once to remove writes made by hand while diagnosing the transaction
defect, and once after the `fixture` run source was moved onto its own product. Evidence
artefacts and ledger rows survive a rebuild, because they live in Cloud Storage and BigQuery; the
documents do not. So for the earlier `document` runs there is now evidence of a run whose output
is no longer resolvable in the store. That is acceptable in a demonstrator being rebuilt
deliberately. It would not be acceptable in a regulated environment, where the store is the
record and deleting it is not a routine act.

### The Workflows `document` branch

Every run above reached the worker by a direct POST from the seeding script or the local runner,
so until 2026-09-21 the orchestration in `workflows/epi-pipeline.yaml` that builds a `document`
request had never run. It was exercised deliberately, and deliberately as a negative case:
execution `8a14b585-2b95-454c-8b77-44571e3ae304` of `ema-flow-dev-pipeline` was started with a
real submission URI and a `sha256` of sixty-four zeroes.

The workflow took the `document` branch, assigned the reference into the request body, and
called the worker, which read the object, hashed it, and refused with HTTP 422 and the closed
body `{"error":"submission-unreadable","part":"submission","reason":"hash-mismatch"}`.

That establishes three things and no more. The orchestration passes `submissionRef` through
correctly. The ingress gate checks the hash of a by-reference submission and fails closed with a
reason code rather than an unclassified error. And the refusal costs nothing: the referenced
label's Bundle was untouched, and no ledger row was written, because a submission refused at the
gate never becomes a run.

A _successful_ `document` run through Workflows is still unexercised, because the only thing it
would add over the direct-POST runs above is persistence, and executing it would publish another
version of a seeded demonstration label. That is a deliberate deferral, not an oversight.

**What these runs do not establish.** The content is synthetic throughout, so nothing here says
anything about a real label. And the validator's package resolution is not hermetic (see
"Official validation gate"), so these outcomes are reproducible only as far as the FHIR registry
is stable.

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
`QUERY_TOOLS_VERSION`, currently `2.0.0`, and `AGENT_TURN_VERSION`, currently `1.0.0`, both of
which have an external consumer — the Python agent under `agent/` vendors a byte copy of each
generated schema into `agent/src/verifiable_answer_agent/contracts/` through
`agent/scripts/sync_contract.py`, and its CI step `sync_contract.py --check` fails on any
drift), the fidelity normalisation procedure
(`docs/fidelity-normalization.md`, `NORMALIZATION_VERSION` — its section 8 is the single
statement of what triggers a change, including a runtime whose Unicode version drifts with no
code change), canonical JSON and hashing (`src/lib/hash.ts` — a change there moves every hash
in every artefact ever produced, including `approvedContentSha256`), and the no-narrative
logging guard (`src/lib/logger.ts`). A change to any of them is a controlled event, not a
routine edit:

0. impact assessment: list every service that imports the module (`src/`, `zone-a/`, `agent/`),
   state for each whether previously produced evidence remains reproducible, and record the
   outcome with the change;
1. bump the version literal (`schemaVersion`, `QUERY_TOOLS_VERSION`, `AGENT_TURN_VERSION`, or
   `NORMALIZATION_VERSION`);
2. run `npm run contracts:generate` and `npm run vectors:generate`, and commit the regenerated
   `contracts/generated/**` and `test/fixtures/fidelity/vectors.json`; for a change to
   `query-tools` or `agent-turn`, also run `uv run --frozen python scripts/sync_contract.py`
   in `agent/` and commit the regenerated vendored copies — the agent's tests validate its tool
   results and its turn record against those copies, and CI refuses a copy that differs from
   `contracts/generated/`;
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

### Recorded change: `src/lib/logger.ts` allow-list, 2026-09-20

**What changed.** `credentialType` was added to `allowedFieldNames`. The forbidden-key pattern
matches the substring "credential", so before this change the logger dropped that field from
every written line and the retained audit line was a strict subset of the published
`QueryAuditRecord`. The pattern itself, the other two allowed names (`resourceType`,
`resourceId`), and the value guard (over 512 characters, or containing `<`, is dropped) are
unchanged; `credential` and `credentials` are still dropped, and a test asserts it.

**Why.** UR-24 and UR-39 both recorded the same gap: the record named a field that the retained
log line did not carry, so the evidence was narrower than the contract said it was. Either the
contract or the logger had to move. The field's value is one of the two members of the
contract's `CredentialType` enum (`id-token`, `access-token`) and cannot carry text or a token,
so widening the logger was the smaller change.

**Impact assessment (step 0).** `src/lib/logger.ts` is imported by the worker (`src/server.ts`,
`src/app.ts`, `src/pipeline.ts`, `src/gcp/submission-reader.ts`) and by the query service
(`src/query/app.ts`, `src/query/server.ts`). The Python agent under `agent/` does not import it.
`credentialType` appears nowhere in `src/` outside `src/contracts/query-tools.ts`,
`src/query/`, and the logger's own allow-list, so no worker log line changes; the full suite is
green. No previously produced evidence is affected: this changes what may be written to a log,
never a hash, a manifest, an approved content hash, or a contract version, so no version
literal was bumped and steps 1–3 and 7 do not apply.

**Blast radius.** Any service importing the logger may now write a field named
`credentialType`, and the key filter will no longer stop it. What keeps that safe is the field's
type at each call site plus the value guard, not the key name — a future caller that put free
text under that key would defeat the guard unless the text were over 512 characters or carried
`<`. This is the cost of the change and is stated rather than assumed away. **Superseded by the
next record**, which replaced the bare name exemption with a name paired with a value shape, so
free text under that key is now dropped whatever its length.

**Approval (step 8).** Not obtained: author and releaser are the same identity. Branch protection
on `main` does exist and requires status checks, but it requires no reviewer, so nothing forces a
second pair of eyes (see "Release criteria"). This record is the impact assessment
step 0 requires, not the approval step 8 requires.

### Recorded change: `src/lib/logger.ts` value-shape exemptions, 2026-09-20

**What changed.** `allowedFieldNames` went from a set of names to a map of name to value shape,
and the filter now drops a forbidden-looking key unless its value also matches the shape
recorded for it: `credentialType` must be a short lowercase token, `resourceType` letters only,
`resourceId` an identifier. A 512-character string under `credentialType` is now dropped where
the previous record says it would have been written.

**Why.** The previous record's own blast-radius paragraph named the hazard: the exemption was
by key name, while the guarantee that made it safe (the field's type at its one call site) lived
in another file and was stated only in a comment. A reviewer reading the logger could not see
why the exemption was safe. Enforcing the shape where the exemption is made puts the claim and
the code in the same place.

**Impact assessment (step 0).** Importers are unchanged from the previous record: the worker
(`src/server.ts`, `src/app.ts`, `src/pipeline.ts`, `src/gcp/submission-reader.ts`) and the query
service (`src/query/app.ts`, `src/query/server.ts`); the Python agent does not import it. No
call site in `src/` logs `resourceType` or `resourceId` today, and the only `credentialType`
call site passes a `CredentialType` enum member, so no line any service writes changes. The
change can only narrow what is written, never widen it, so no previously produced evidence is
affected and no version literal moved. Steps 1–3 and 7 do not apply.

**Blast radius.** A future call site that puts a value of the wrong shape under one of the three
exempt keys loses that field from the line rather than leaking it. The failure mode is a missing
field, which a contract check on the written line catches, rather than content in the log.

**Approval (step 8).** Not obtained, for the same reason as the record above.

### Recorded change: `fhir/mappings/cap-smpc-en.json` QRD coding displays and EMA List code, 2026-09-20

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
