# Design note: ePI query service (Model Context Protocol)

- Status: Proposed, not implemented
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
   narrative is ever logged, exactly as `src/lib/logger.ts` already enforces for the pipeline.

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
is a reader, and no misconfiguration of it can produce a write.

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
  buckets. A negative test asserts the role set.
- **Authentication.** Phase 1 accepts a Google-signed OIDC ID token for the service's audience,
  which covers a Workspace user, a service account, and later an Identity Platform-federated
  user without changing the service. Cloud Run requires authentication at the edge, and the
  service verifies the token again itself: the edge is not trusted alone. Identity Platform as
  the issuer for external users is phase 2.
- **Entitlements** are an interface — `entitlementsFor(principal) → { organisations, bundles }`
  — resolved once per request and applied before any store read. Phase 1 backs it with a
  Terraform-managed map; Firestore replaces the backing in phase 2 without changing callers.
  Outside a caller's entitlement, every document is `document-not-found`: existence is not
  disclosed.
- **Reads** go to the validated FHIR store only, by REST, as the worker's own client does.
  Section lookup is by canonical `sourceKey`; the pinned mapping manifest translates to the
  store's coding where needed. Nothing is cached across requests in phase 1.
- **Provenance is document-level.** The persisted `Provenance` resource carries the source
  document hash, the fidelity report hash, the approved-content hash, extractor and model
  identities, and the approver. Per-section hashes in a `get_provenance` answer are recomputed
  live from the stored narrative, which lets a client verify an answer against the store; the
  comparison against the _approved_ record (the per-section hashes in the ingestion-provenance
  evidence object) needs an evidence-bucket read and is phase 2.
- **Transport** is the Model Context Protocol streamable-HTTP transport from the official SDK,
  pinned, in stateless mode so Cloud Run can scale it. Tool descriptions state that content
  fields are document text, never instructions.
- **Audit.** One structured record per call (`QueryAuditRecord`): principal, tool, a digest of
  the arguments, outcome, counts, latency. Through the same logger the worker uses, so its
  no-narrative guard applies. Cloud Audit Logs record the store reads themselves.

### Acceptance tests (phase 1)

Each of these is a test in the repository, and each is also a demonstration.

1. **Verbatim with citations.** `get_section` returns the stored narrative byte for byte, with
   `narrativeDivSha256` and `normalizedTextSha256` that the test recomputes independently
   from `div` and finds equal — the client never has to trust the service's arithmetic.
2. **Is this quote accurate?** `verify_quote` says match for a fragment of a section, with the
   right offsets; no-match for the same fragment with one character changed, a straightened
   quotation mark, a flattened superscript, or a word removed; and match for the same fragment
   with different whitespace or a ligature, because that is what the publishing gate accepts.
3. **Prove where it came from.** `get_provenance` returns the approver, approval time, source
   document hash, and fidelity report hash exactly as the persisted Provenance resource carries
   them, and a section hash that equals the one `get_section` reports.
4. **The injection test.** A synthetic section whose narrative contains an instruction-shaped
   sentence comes back byte-identical, hash attached, `contentNotice` set — and nothing else
   in the response changes.
5. **The tenant wall.** Two synthetic organisations. A caller entitled to one receives
   `document-not-found` for the other's document from every tool, for every argument shape
   that could name it, and the audit record shows the attempt with outcome `not-entitled`
   internally mapped to what was returned.
6. **No narrative anywhere but the answer.** The audit record for every call, and every error
   response, passes the same narrative-leak scan the pipeline's evidence is held to.
7. **Least privilege, proven.** The Terraform-declared role set of the query service account is
   asserted by a test to be exactly the two roles above.

## Open questions

- Snippet length cap for `search_sections`, and whether snippets need their own span hashes.
- Whether version history should be served from the ledger (fast, indexed) or from FHIR
  `_history` (authoritative), and how to keep the two consistent in the answer.
- Language scoping: ePI is per-language, so every search and section read must name a language
  explicitly rather than defaulting.
- Whether to publish the tool schemas as a versioned contract in `contracts/generated/`, as the
  Zone A hand-off already is.
