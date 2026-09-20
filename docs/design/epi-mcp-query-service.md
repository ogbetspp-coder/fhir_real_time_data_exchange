# Design note: ePI query service (Model Context Protocol)

- Status: Proposed; phase 1 in implementation on branch `mcp-query-phase1`. "Phase 1 as
  built" below is the build specification and its acceptance tests are criteria until the
  named test files under `test/query/` are merged — this line is updated when they are.
- Date: 2026-09-20
- Related: `docs/architecture.md`, `docs/adr/0002-two-trust-zones-and-canonical-submission.md`,
  `docs/adr/0003-mechanical-narrative-fidelity.md`

## What it is

A read-only service that lets an AI assistant answer questions about product information by
querying this hub instead of recalling them. Every answer it returns is a verifiable fact: the
FHIR resource and version it came from, the QRD section key, the SHA-256 of the narrative, and
the approval record that put that narrative in the store.

The Model Context Protocol (MCP) is an open standard for exposing a set of tools to an AI
assistant over a documented interface. A "server" here is an ordinary HTTPS service; the
protocol only fixes how tools are advertised, called, and authorised. Publishing one means any
MCP-capable assistant — Claude, Gemini, or an in-house agent — can use the hub without a custom
integration per client.

## Why it belongs on this hub rather than anywhere else

A general-purpose model asked "what does the German SmPC for DrugX say about hepatic
impairment?" will produce fluent text that nobody can check. This hub holds the one thing that
makes the question answerable: narrative bound by hash to an approved source document, with a
per-section fidelity report and a Provenance resource naming the approver. The query service is
the window onto that chain.

Two consequences follow, and they are the whole point:

- **The assistant never has to be trusted about content.** It receives verbatim text plus a
  hash and a citation; a reviewer can verify any sentence against the approved source without
  trusting the model at all. This is the same principle as the Zone A/Zone B split (ADR 0002)
  applied to reading instead of writing: the model proposes, the hash proves.
- **It converts an integration backend into something a person uses daily.** Regulatory affairs
  reviewers do not see a FHIR store. They would see this.

## Non-negotiable constraints

These are design constraints, not preferences. They follow from `AGENTS.md`.

1. **Read-only.** The service exposes no write tool of any kind. Content reaches the store only
   through the Zone A → human approval → Zone B pipeline. An MCP server that could write would
   re-open every boundary this repository exists to enforce.
2. **No server-side summarisation of regulated narrative.** Tools return narrative verbatim with
   its hash. If a client assistant paraphrases, that paraphrase is visibly the assistant's own
   words, and the hash lets anyone compare it to the source. The service itself never generates,
   summarises, or infers regulated text.
3. **Every returned fact is citable.** No tool returns bare prose. Each result carries the
   document reference, version, `sourceKey`, and `narrativeDivSha256`.
4. **Entitlement filtering happens before the query, not after.** A caller's organisation and
   market entitlements are resolved first and pushed into the FHIR search; results are never
   fetched and then filtered.
5. **Every call is audited** — principal, tool, argument digest, result count — and no returned
   narrative is logged. `src/lib/logger.ts` drops fields whose names match a forbidden pattern
   and any value over 512 characters or containing `<`; that is a defence-in-depth guard, not a
   proof. The proof is that no code path passes narrative to the logger, asserted by the
   narrative-leak tests.

## Tool surface

| Tool                     | Returns                                                                                                                         | Narrative? |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------- | ---------- |
| `find_product`           | Product and authorisation identifiers, MA holder, status, available document types and languages                                | No         |
| `list_document_versions` | Version history from the transformation ledger: run id, completion time, manifest hash, approval hash                           | No         |
| `get_section`            | One QRD section verbatim, with version, `sourceKey`, `narrativeDivSha256`, and its Provenance reference                         | Verbatim   |
| `search_sections`        | Ranked section references with verbatim, length-capped, hashed snippets                                                         | Verbatim   |
| `diff_sections`          | Per-section changed/unchanged between two versions, by hash; verbatim texts only when explicitly requested                      | On request |
| `get_provenance`         | Source document hash and filename, spans, extractor/model/prompt identifiers, fidelity report hash, approver role and timestamp | No         |

`get_provenance` is the differentiator. "Show me that this sentence came from the approved
source document, and who signed off" is a question no competing system can answer, and this hub
answers it mechanically.

Tools that must never exist: any write or amend tool; any tool that drafts, rewrites, or
summarises a section; any tool that returns narrative without its hash.

## Google Cloud components

Fit-for-purpose managed services throughout; nothing custom that Google already operates.

| Concern                       | Component                                                                                                                                                      |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hosting                       | Cloud Run (streamable HTTP MCP endpoint, same deployment pattern as the worker)                                                                                |
| End-user identity             | Identity Platform for external users; Workforce Identity Federation for staff SSO                                                                              |
| Edge protection and quotas    | External Application Load Balancer with Cloud Armor; API Gateway where per-client API keys and quotas are needed (Apigee only if a full API product is wanted) |
| Canonical content             | Cloud Healthcare API FHIR store (existing) — R5 search, version-aware reads                                                                                    |
| Version history and analytics | BigQuery: the transformation ledger and the native FHIR stream (existing)                                                                                      |
| Semantic section search       | Vertex AI Search over section documents, or BigQuery vector search with Vertex AI text embeddings where the corpus stays small                                 |
| Entitlements                  | Firestore (per-organisation product and market entitlements, low-latency reads)                                                                                |
| Optional response signing     | Cloud KMS — sign the tool result payload so a recipient can prove what was returned                                                                            |
| Audit trail                   | Cloud Audit Logs plus the existing retained log sink (`infra/security.tf`)                                                                                     |
| Secrets                       | Secret Manager                                                                                                                                                 |
| Dashboards and alerting       | Cloud Monitoring (existing dashboards extended with query volume and latency)                                                                                  |
| Infrastructure                | Terraform, deployed by the existing pipeline                                                                                                                   |

## Authorization model

A caller presents an OIDC token from Identity Platform or Workforce Identity Federation. The
token subject maps to an organisation and an entitlement set. That set is resolved once per
request and pushed into every FHIR search and BigQuery query as a filter. The service's own
service account holds read-only access to the FHIR store and the ledger, and nothing else — it
is a reader. Its Terraform-declared role set contains no write role and a test asserts that set
exactly; the _effective_ IAM policy can still be widened outside Terraform, so each
deployment's evidence includes an effective-policy export for this service account.

Cross-tenant isolation is the highest-risk area of this design and needs its own test suite:
every tool needs a negative test proving that an entitled caller cannot reach an unentitled
product through any argument.

## Residual risks, stated plainly

- **Prompt injection.** Narrative is text a human author wrote, and it can contain anything that
  looks like an instruction. The service returns it as clearly-marked content with a hash and
  never as instruction, but a client assistant that treats tool output as instruction can still
  be steered. This is a client-side property the server cannot enforce; it must be documented
  for anyone integrating.
- **Client-side paraphrase.** The server cannot stop an assistant from summarising badly. The
  mitigation is structural, not technical: every result carries a hash and a citation, so a bad
  paraphrase is detectable rather than invisible.
- **MCP authorization is still maturing** as a specification. Pin the protocol version and
  re-review when it changes.
- **This is an information-retrieval aid.** It is not a regulatory decision system, it is not
  validated, and it does not replace the approved ePI or the authorised product information. No
  compliance claim is made or implied.

## Phasing

| Phase | Scope                                                                                                                                                                                    | Size |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 1     | Cloud Run MCP endpoint, Identity Platform auth, `find_product`, `get_section`, `get_provenance`; reads the existing FHIR store; entitlement filter and audit logging from the first line | M    |
| 2     | `list_document_versions` and `diff_sections` from the transformation ledger; Cloud Armor quotas; cross-tenant negative test suite                                                        | M    |
| 3     | `search_sections` via Vertex AI Search (or BigQuery vector search), language-scoped                                                                                                      | M    |
| 4     | KMS-signed tool results; per-client API products and quotas                                                                                                                              | S    |

Phase 1 is demonstrable in a meeting: ask a plain-English question, get an answer whose every
sentence links back to a hash and an approver. That demo is the reason to build it.

## Phase 1 as built (2026-09-20)

The phasing table above is the original plan; this section is the authoritative shape of what
phase 1 delivers. It follows ADR 0004: the query service is its own deployable with its own
identity, and shares only pure libraries with the worker.

- **Tools: four**, one more than planned. `find_product`, `get_section`, `get_provenance`, and
  `verify_quote` — "is this quote what the label says?" — which compares a caller's text with a
  section under the same normalisation the publishing gate uses and answers match or no-match
  with offsets, never a paraphrase. It is read-only, it is the fidelity library doing double
  duty, and it is the tool a medical-information or promotional-review team would use daily.
  The full surface — inputs, outputs, the closed error codes, the audit record — is a published
  contract: `src/contracts/query-tools.ts` → `contracts/generated/query-tools.schema.json`.
- **Identity.** The service is its own Cloud Run service (`ema-flow-<env>-query`) with its own
  service account holding `roles/healthcare.fhirResourceReader` on the dataset and
  `roles/logging.logWriter`, and nothing else — no write role anywhere, no BigQuery, no
  buckets. A negative test asserts the role set across every file under `infra/`.
- **Image.** `cloudbuild.images.yaml` builds `Dockerfile.query` and publishes it as the `query`
  path of the shared `ema-flow` Artifact Registry repository (`<region>-docker.pkg.dev/<project>/
<repository>/query:<tag>`), following the worker and validator convention; `ema-flow-query`
  is the service name the audit record carries, not an image name. Both build stages are
  pinned to the worker's Node image digest, because `verify_quote` depends on the runtime's
  Unicode database (ADR 0003); `npm run images:check` (`scripts/ci/check-dockerfiles.mjs`, part
  of `npm run check`) fails if any root `Dockerfile*` has a `FROM` without a `@sha256` digest or
  if two Node-based Dockerfiles pin different digests.
- **Authentication.** Cloud Run requires authentication at the edge, and the service verifies
  the credential again itself: the edge is not trusted alone. Two credential kinds are accepted
  on `Authorization: Bearer`, distinguished by shape (`src/query/auth.ts`):
  - a bearer that parses as a JWT (three base64url segments) is verified as a Google-signed
    OIDC ID token for `QUERY_AUDIENCE` — a Workspace user, a service account, or the Google
    service agent on the assistant path; `credentialType` is `id-token`;
  - anything else is treated as a Google OAuth 2.0 access token — the end user's token as
    Gemini Enterprise forwards it (its own service-agent ID token arrives in
    `X-Serverless-Authorization`, which Cloud Run IAM checks and the service ignores). It is
    verified with `OAuth2Client.getTokenInfo` (the token travels in a request header, never in
    a URL the service builds) and accepted only when it carries a `sub`, an expiry in the
    future, and an `aud` or `azp` listed in `QUERY_OAUTH_CLIENT_IDS` — a comma-separated list
    of OAuth 2.0 client ids that is optional and defaults to empty, in which case every access
    token is rejected and the ID-token-only posture stands; `credentialType` is
    `access-token`. Successful access-token verifications are cached in process memory, keyed
    by the SHA-256 of the token, for the smaller of the token's remaining lifetime and 300
    seconds, at most 1,000 entries with the oldest evicted first; failures are never cached.
    The cache holds principals and expiry times only — no token, no narrative, no result — and
    lives beside the startup-parsed entitlement map and the SDK's signing-key cache as the
    state the process keeps across requests.

  A credential that fails is answered `401 {"error":"unauthenticated"}` before the transport
  is connected. Identity Platform as the issuer for external users is phase 2.

- **Entitlements** are an interface — `entitlementsFor(principal) → { bundles } | undefined` —
  resolved once per request, immediately after the principal, and applied before any store
  read. A phase 1 entitlement is a list of document Bundle ids per principal and nothing else.
  It carries no organisation: no authorization decision would read one, and a field that is
  parsed but never consulted would only look like a control. Organisation scoping arrives with
  the Firestore directory in phase 2, together with a document-to-organisation binding; the
  interface does not change for callers. An authenticated principal with no entitlement at all
  is answered `403 {"error":"not-entitled"}` before the transport is connected, so it never
  reaches `tools/list`. Inside the protocol, a document outside the caller's entitlement is
  `document-not-found` from every tool: existence is not disclosed. Phase 1 backs the
  interface with a Terraform-managed map (`QUERY_ENTITLEMENTS_JSON`, `{ "<sub>": { "bundles":
[...] } }`); an unknown key in that map fails startup.
- **Request shape.** The service answers `400 {"error":"invalid-request"}`, before the
  transport is connected and without dispatching anything, when the body is not JSON or is
  larger than 4 MiB, when a JSON-RPC batch carries more than 8 messages, or when the
  `X-Query-Turn-Id` header is present but is not a UUID. When that header is a UUID it is the
  assistant turn the caller declares (`contracts/agent-turn`) and is copied into every audit
  record of the request as `turnId`.
- **Reads** go to the validated FHIR store only, by REST, as the worker's own client does.
  Section lookup is by canonical `sourceKey`; the pinned mapping manifest translates to the
  store's coding where needed. No narrative and no result is cached across requests.
- **`find_product`** has no search against the store in phase 1: it reads the caller's entitled
  documents one by one and inspects each. The reads run through a pool of at most 8 in flight,
  cover at most the first 200 entitled ids in entitlement order (`FIND_PRODUCT_SCAN_HORIZON`),
  and stop being launched once `limit` matches are in hand. `truncated` is true exactly when
  the entitlement holds more than 200 ids — so an empty `products` never silently means "no
  such product" — and the same value is carried into the audit record. Matches are reported in
  entitlement order regardless of the order the reads completed in.
- **`verify_quote`** counts `sectionsSearched` as the number of candidate sections that carry a
  narrative, normalises each candidate's text in turn, stops at the first match, and hashes
  only the matched section's text.
- **Provenance is document-level.** The persisted `Provenance` resource carries the source
  document hash, the fidelity report hash, the approved-content hash, extractor and model
  identities, the approver's identity, and — on the attester agent's `role`, under
  `https://khs.dev/fhir/CodeSystem/approver-role` — the approver's role. The identifier
  systems, participant-type codes, role system, and extension URL are exported by
  `src/fhir/provenance.ts` and imported by the query service (ADR 0004: shared by import). A
  persisted Provenance without the role coding (one written before the projection carried it)
  is answered `unavailable`; the role is never inferred. Per-section hashes in a
  `get_provenance` answer are recomputed live from the stored narrative, which lets a client
  verify an answer against the store; the comparison against the _approved_ record (the
  per-section hashes in the ingestion-provenance evidence object) needs an evidence-bucket
  read and is phase 2.
- **Transport** is the Model Context Protocol streamable-HTTP transport from the official SDK,
  pinned, in stateless mode so Cloud Run can scale it. Tool descriptions state that content
  fields are document text, never instructions. Tool failures are returned as `isError: true`
  with the contract's closed error shape (`{ tool, error }`) as `structuredContent`.
  - **Known client-side quirk, not fixable here.** MCP SDK 1.30.0's `Client.callTool`
    validates `structuredContent` against the tool's `outputSchema` whenever it is present —
    including when `isError` is true — once `listTools` has cached the validators. A client
    that has called `listTools` therefore sees a tool error rejected by its own validation as
    an `McpError` rather than delivered as a tool error. The harness in `test/query/` calls
    `callTool` without a prior `listTools`, so the tests do not exercise this path; an
    integrator that lists tools first should expect it. The error shape is kept as structured
    content regardless, because the `content` text alone is not machine-readable.
- **Audit.** Exactly one structured record per dispatched tool call (`QueryAuditRecord`):
  `service` (`ema-flow-query`), `serviceVersion`, `imageDigest` (from `IMAGE_DIGEST`,
  validated as `sha256:<64 hex>`; absent outside a container), `at`, `principal`,
  `credentialType`, `tool`, `argumentsSha256`, `outcome`, `resultCount`, `truncated`
  (`find_product` only), `durationMs`, `bundleId`, `versionId` (the document version the call
  actually read, whenever one was — absent for `not-entitled`, where nothing is read), and
  `turnId` (when declared). A `tools/call` _request_ (an entry with an id) that the transport
  refused before the handler ran still produces one `invalid-request` record; a notification
  (no id) produces none, because the protocol never answers it. The `outcome` enumeration is a
  superset of the returnable error codes: `not-entitled` is recorded and never returned. Records
  go through the same logger the worker uses, so its no-narrative guard applies; one
  consequence is that the logger's forbidden-key pattern drops `credentialType` from the
  written line (the key contains "credential"), so the line carries every other field of the
  record but not that one — widening the logger is a shared-library change and has not been
  made. Cloud Audit Logs record the store reads themselves.
- **Structured log lines** the service writes all carry `service: "ema-flow-query"`. A refused
  authentication is `severity: WARNING, stage: "query-http", event: "unauthenticated"` with no
  principal, no reason, and nothing derived from the credential; a refused entitlement is
  `severity: WARNING, stage: "query-http", event: "not-entitled", principal: <sub>` — the
  opaque subject an operator would entitle; tool audit records are `stage: "query-tool"` with
  `outcome` as above. These are the fields the infrastructure's log-based metrics filter on.

### Acceptance tests (phase 1)

Each of these is an acceptance criterion for phase 1 — it exists as a named test under
`test/query/` — and each is also a demonstration. The first seven are the original criteria;
the rest were added with the adversarial review of 2026-09-20 and pin the behaviour described
above.

1. **Verbatim with citations.** `get_section` returns the stored narrative byte for byte, with
   `narrativeDivSha256` and `normalizedTextSha256` that the test recomputes independently
   from `div` and finds equal — the client never has to trust the service's arithmetic. The
   audit record carries `credentialType` and `versionId`. (`acceptance.test.ts`, "verbatim with
   citations")
2. **Is this quote accurate?** `verify_quote` says match for a fragment of a section, with the
   right offsets; no-match for the same fragment with one character changed, a straightened
   quotation mark, a flattened superscript, or a word removed; and match for the same fragment
   with different whitespace or a ligature, because that is what the publishing gate accepts.
   `sectionsSearched` equals the number of narrative-bearing sections whether the match is in
   the first section or the last. (`acceptance.test.ts`, "is this quote accurate?")
3. **Prove where it came from.** `get_provenance` returns the approver and role, approval
   time, source document hash, and fidelity report hash exactly as the persisted Provenance
   resource carries them, and a section hash that equals the one `get_section` reports.
   (`acceptance.test.ts`, "prove where it came from"; and "answers unavailable when the
   persisted Provenance omits the approver role" for a resource written without the role)
4. **The injection test.** A synthetic section whose narrative contains an instruction-shaped
   sentence comes back byte-identical, hash attached, `contentNotice` set — and nothing else
   in the response changes. (`acceptance.test.ts`, "the injection test")
5. **The tenant wall.** Two principals with disjoint bundle lists. A caller entitled to one
   receives `document-not-found` for the other's document from every tool, for every argument
   shape that could name it, and the audit record shows the attempt with outcome
   `not-entitled` and no `versionId`, internally mapped to what was returned.
   (`acceptance.test.ts`, "the tenant wall")
6. **No narrative anywhere but the answer.** The audit record for every call, and every error
   response, passes the same narrative-leak scan the pipeline's evidence is held to.
   (`acceptance.test.ts`, "no narrative anywhere but the answer")
7. **Least privilege, proven.** Every `infra/*.tf` is read; at least one declares the query
   service account; the `*_iam_member`, `*_iam_binding`, and `*_iam_policy` resources that
   name it bind exactly the two roles above, the reader on the dataset and the log writer on
   the project. The test fails, rather than skips, when the files are absent.
   (`acceptance.test.ts`, "least privilege, proven")
8. **The scan horizon.** With more entitled ids than the horizon, `find_product` reads exactly
   the first 200 and answers `truncated: true`, in the result and in the audit record; with
   `limit: 1` against an entitlement where every document matches, no more reads are launched
   than one pool's worth and the answer is the first entitled id; within the horizon
   `truncated` is false. (`acceptance.test.ts`, "find_product reads at most the scan horizon,
   and stops at the limit")
9. **The batch cap.** A batch of nine `tools/call` requests is answered `400` with no tool run,
   no store read, and no audit record; a batch of eight is answered with one record per entry.
   (`http.test.ts`, "caps a JSON-RPC batch before the transport sees it")
10. **Notifications are not calls.** Eight `tools/call`-shaped notifications produce zero audit
    records and zero reads; a request with an id that is not JSON-RPC produces exactly one
    `invalid-request` record. (`http.test.ts`, "audits no record for a notification, and
    exactly one for a request the transport refused")
11. **Entitlement before the protocol.** An authenticated principal with no entitlement is
    answered `403 {"error":"not-entitled"}` for a tool call and for `tools/list`, no record is
    written, and the warning line carries the principal. (`http.test.ts`, "refuses an
    authenticated principal with no entitlement before the protocol")
12. **The 401 line.** A refused authentication writes one `WARNING` line whose only fields are
    `event`, `message`, `service`, `severity`, `stage`, `timestamp`. (`http.test.ts`, "logs a
    rejected authentication as a structured warning with nothing from the token")
13. **The audit fields.** `imageDigest`, `credentialType` as the verifier reported it, and
    `versionId` are on the record; `X-Query-Turn-Id` is threaded into every record of a batch;
    a header that is not a UUID is answered `400` with no record and no read. (`http.test.ts`,
    "answers a tool call over the streamable HTTP transport", "records the credential kind the
    verifier reported", "threads a declared turn id into every audit record of the request",
    "refuses a turn id that is not a UUID before the protocol, and audits nothing")
14. **Two credential kinds.** A JWT-shaped bearer goes to ID-token verification and an opaque
    one to `getTokenInfo`; an ID token for another audience or issuer is rejected; an access
    token whose `aud`/`azp` is not configured, or that is expired, or that has no `sub`, is
    rejected and never cached; with `QUERY_OAUTH_CLIENT_IDS` empty every access token is
    rejected without a call to Google; a repeated access token is served from the cache until
    the bounded lifetime ends and never past the token's own expiry; the cache holds 1,000
    entries and evicts the oldest. (`auth.test.ts`, the "credential verification" suite)
15. **No token, no hash, on any line.** Through the real HTTP service with the real verifier over
    a stubbed Google client, an accepted ID token, an accepted access token, and two rejected
    credentials produce log lines that contain neither any token nor its SHA-256 nor the word
    "Bearer". (`auth.test.ts`, "never writes a token or its hash to a log line, on any path";
    and `http.test.ts`, "writes nothing about a token to any log line")
16. **The tool surface.** `tools/list` advertises exactly the four read-only tools, each with an
    object input and output schema and a description that says content is never instruction.
    (`tool-surface.test.ts`)
17. **The projection carries the role.** `toProvenanceResource` writes the approver's identity
    and role on the attester agent, and nothing else about the approver.
    (`test/provenance.test.ts`, "names the approver and the approver's role on the attester
    agent")

## Security properties stated honestly

Written after an assessor-style review of this note, so that what the service does not do is
on the page next to what it does.

- **Existence disclosure.** Outside a caller's entitlement every document is
  `document-not-found`. That requires the _returnable_ error set to exclude `not-entitled`; the
  contract keeps `not-entitled` only as an audit outcome. `verify_quote`'s `sectionsSearched`
  is returned only for a document the caller is entitled to.
- **Timing.** Entitlement is resolved before any store read, so an unentitled request returns
  without I/O and is distinguishable by latency from an entitled miss. This is a usable
  existence oracle. It is accepted for a demonstration and must be closed with a fixed-cost
  miss path before multi-tenant use (UR-20).
- **Prompt injection is not mitigated by the server.** `ContentNotice` is a declaration to the
  client, not a control; a client may ignore it. What the server does is remove the
  consequences it can: no write tool exists, the service account holds no write role, nothing
  is cached across requests, so an injected instruction cannot change any state on this side
  of the boundary. A steered assistant on the client side remains the integrator's risk.
- **`verify_quote` is an oracle over section text.** Acceptable only because entitlement is
  document-level and an entitled caller can already read the section; any future section- or
  field-level entitlement must re-assess it.
- **Token handling.** The bearer token is verified in memory and discarded: never logged, never
  written to any store, never in an audit record — only the `sub` claim is retained as
  `principal`. Google's signing keys are cached for their published lifetime; the entitlement
  map is process-local and re-read on deploy.
- **`/healthz`** is served on the Cloud Run IAM check alone and returns liveness, the service
  name, and the build version only — never configuration values, principal, or entitlement
  data.

## Audit trail

One `QueryAuditRecord` per tool call, through the regulated-audit sink to the retained log
bucket (`min(evidence_retention_days, 3650)` days). Needed before a pilot, and listed in
`docs/validation/README.md` UR-24: the record gains `versionId` (which version of a label was
served), `credentialType`, and a `turnId` correlating it with the agent's per-turn record;
the retained log bucket is locked; readers are a named role on that bucket only; a log-based
alert fires on `not-entitled` outcomes. The trail is not cryptographically tamper-evident —
Cloud Logging immutability plus IAM is the control, unlike the worker's KMS-signed manifest —
and this note says so rather than implying otherwise.

## Entitlement changes are change-controlled

In phase 1 entitlements are a Terraform variable, which makes an access-control decision a
code change. It therefore goes through the same pull-request, review, and promotion path as
code, with an approver who is not the requester, and phase 2's Firestore backing exists
precisely so entitlements can be granted by a role separate from the developer.

## Decided

- The tool schemas are a published, versioned contract (`contracts/generated/query-tools.schema.json`),
  as the Zone A hand-off already is. Decided 2026-09-20.
- A phase 1 entitlement is a bundle list per principal, without an organisation field. The
  field was parsed and stored but no authorization decision read it, so it was removed rather
  than left looking like a control. Organisation scoping arrives with the Firestore directory
  in phase 2, together with a document-to-organisation binding. An older map that still
  carries the key fails startup. Decided 2026-09-20.
- An authenticated principal with no entitlement is refused with `403 {"error":"not-entitled"}`
  before the transport is connected: any Google identity can mint an ID token for the
  service's audience, and authentication alone must not reach `tools/list`. Decided
  2026-09-20.
- `find_product` keeps per-document reads in phase 1 (no new search semantics against the
  store) and bounds them: a pool of 8, a horizon of 200 entitled ids, an early stop at `limit`,
  and `truncated` reported in the result and the audit record. Decided 2026-09-20.
- Google OAuth 2.0 access tokens are accepted on `Authorization` only when
  `QUERY_OAUTH_CLIENT_IDS` names the client they were issued to; absent or empty keeps the
  ID-token-only posture. `QUERY_AUDIENCE` keeps its meaning (a single string the infra
  computes). Decided 2026-09-20.
- The JSON-RPC batch cap is 8 messages, refused with `400` before the transport is connected.
  Decided 2026-09-20.
- `X-Query-Turn-Id`, when present, must be a UUID or the request is `400`; it is never dropped
  silently. Decided 2026-09-20.
- `Dockerfile.query` pins both stages to the worker's Node image digest, and
  `npm run images:check` enforces digest pinning and digest agreement across the root
  Dockerfiles as part of `npm run check`. Decided 2026-09-20.
- The Cloud Run service is `ema-flow-<env>-query`; the image is the `query` path of the shared
  `ema-flow` Artifact Registry repository. Decided 2026-09-20.

## Open questions

- Snippet length cap for `search_sections`, and whether snippets need their own span hashes.
- Whether version history should be served from the ledger (fast, indexed) or from FHIR
  `_history` (authoritative), and how to keep the two consistent in the answer.
- Language scoping: ePI is per-language, so every search and section read must name a language
  explicitly rather than defaulting.
