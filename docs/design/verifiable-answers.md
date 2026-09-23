# Design note: verifiable answers on Google-native surfaces

- Status: Proposed 2026-09-20. The query service is deployed (`ema-flow-dev-query`, first
  2026-09-20; all four tools answered live 2026-09-21). The agent is deployed to Agent Engine
  (`reasoningEngines/6226059359072288768`, europe-west4, Python 3.14), registered in Gemini
  Enterprise, and redeployed on 2026-09-23 with the draft-hold fix (PR #98); its post-check has
  not yet run in a live Gemini turn. Both are delivered (were roadmap items 1 and 1b). Google
  product capabilities below were read from Google's public documentation (dated per source,
  inline); the MCP connector, Agent Engine and agent registration have since been exercised in
  this project's tenant, the rest has not and must be re-confirmed in the console before it is
  relied on
- Related: `docs/design/epi-mcp-query-service.md`, `docs/adr/0004-service-boundaries-and-shared-code.md`,
  `docs/roadmap.md` (the query service, the agent and the demonstration enablers: delivered, were
  items 1, 1b and 1c; the live post-check is current item 1)

## The promise, stated narrowly enough to be true

An assistant's prose cannot be made trustworthy, and no one should claim it in front of a
regulated buyer. What can be made true is this:

> Every span the assistant presents as a quotation is verbatim from the store, carries a hash
> a reader can recompute, and has been mechanically re-checked against the store after the
> assistant wrote it. Whether the assistant marks label content as a quotation is a model
> behaviour, not a guarantee: what is not marked is the assistant's own words, and is
> unverified.

That is a different promise from "the assistant is correct", and a narrower one than "every
sentence is checked". It is achievable, it is testable, and it is the promise a QA function
can put an intended-use statement around. Two design consequences keep it honest: the agent
fills a quotation slot only by reference to a tool result received in that turn — it never
copies text into one — so an unverified string cannot be presented as a quotation; and an
acceptance test flags an answer whose free-text part contains a span matching a stored section
above a threshold, which is the paraphrase the post-check would otherwise miss. The assistant is
never the source of truth; it is a guide to the source of truth, and every pointer it gives
can be checked. This is the brand rule — AI proposes, math proves, humans decide — applied to
reading.

## Three layers of enforcement, from hard to soft

1. **Hard: the tool surface.** The query service returns narrative verbatim with
   `narrativeDivSha256` and `normalizedTextSha256`, names the document version, and marks
   content fields with `ContentNotice`. It has no tool that summarises, drafts, or writes. A
   client cannot obtain paraphrased label text from it because none exists.
2. **Mechanical: the post-check.** After the assistant composes an answer, every span it
   presents as label content is sent back through `verify_quote`. A span that does not match
   is flagged in the answer as unverified, and the audit record shows it. This check does not
   depend on the model: a model update can make the assistant less helpful, but it cannot make
   an altered quote pass as verified. It is the single most important piece and it is small.
3. **Soft: the instruction.** The agent's system instruction tells it to answer from tool
   results, to quote rather than paraphrase, and to cite. This is the least reliable layer and
   is treated as such — it improves quality; it proves nothing.

Layers 1 and 2 are ours; layer 3 and everything a user sees are Google's. The **validated
boundary** is narrower than "ours": it is the query service, the fidelity library, and the
store. The post-check is our code, but it runs inside the agent at the model's discretion, so
its _invocation_ lies outside the boundary — an answer card without a verification stamp is,
procedurally, unverified.

## Google-native surfaces, no custom frontend — what is confirmed

The standing decision is that no custom UI is built where a Google surface serves the
purpose. For an assistant that is the strongest version of the decision, because the surface
is where a product would otherwise sink most of its effort. Every row below was read from
Google's public documentation on 2026-09-20; nothing here has yet been exercised in this
project's tenant.

| Role                        | Google component                                                                                                                                                         | Ours?                         |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------- |
| Chat surface for staff      | **Gemini Enterprise**: connects a custom MCP server directly as a tool source, and lists a registered Agent Engine agent in the organisation's Agent Gallery (confirmed) | No                            |
| Chat surface in Workspace   | A **Google Chat app** backed by the same Agent Engine agent, via Google's own quickstart (confirmed)                                                                     | No                            |
| The agent's logic (layer 2) | **Agent Development Kit** (Python, `google-adk`) agent with an MCP toolset over streamable HTTP (confirmed)                                                              | **Yes — small, and the moat** |
| Agent runtime               | **Vertex AI Agent Engine**; `europe-west4` reported available since 2025-11 (partially confirmed — confirm in console before committing to it)                           | No                            |
| The model                   | Gemini, pinned by version                                                                                                                                                | No                            |
| The tools                   | The read-only query service                                                                                                                                              | **Yes**                       |
| Structured display          | **A2UI**: an agent emits a structured UI message that Gemini Enterprise renders as a card — the mechanism for showing hashes and citations as widgets (confirmed)        | Card definitions only         |
| The record                  | Cloud Healthcare API FHIR store, Provenance, evidence bucket, ledger                                                                                                     | Yes — already built           |
| Analytics beside the chat   | Looker / Looker Studio over the BigQuery stream                                                                                                                          | No                            |

Not used: the Conversational Agents (Dialogflow CX) messenger widget — it fronts a CX
playbook, not an Agent Engine agent, so it would add a hop for nothing. Grounding-style
inline citations do not coexist with strict JSON output, which is why structured display goes
through A2UI cards instead.

Sources: Gemini Enterprise custom MCP server set-up and Agent Gallery registration
(docs.cloud.google.com/gemini/enterprise, 2026-09-18); ADK MCP tools
(google.github.io/adk-docs/tools-custom/mcp-tools); Agent Engine Agent Identity
(docs.cloud.google.com/agent-builder/agent-engine/agent-identity, 2026-09-18); Chat + ADK
quickstart (developers.google.com/workspace/add-ons/chat/quickstart-adk-agent, 2026-09-03);
A2UI (cloud.google.com/blog, Gemini Enterprise and A2UI integration).

The MCP service is model-agnostic by construction: the same server serves Gemini Enterprise,
Claude, or an in-house agent. That is a sales point, not an accident.

## The identity question, settled

Entitlements are per principal, so the question was whose identity reaches the query service.
**Answer: the end user's, natively.** When Gemini Enterprise calls a custom MCP server hosted
on Cloud Run it sends two headers on every request: `X-Serverless-Authorization`, a
Google-signed ID token for the Gemini Enterprise service agent, and `Authorization`, the end
user's own OAuth 2.0 token, forwarded intact after a consent flow Gemini Enterprise manages
(confirmed, docs dated 2026-09-18). That is exactly the two-layer model the query service was
designed for:

- **Edge:** Cloud Run IAM authenticates the service agent from `X-Serverless-Authorization`.
  The Gemini Enterprise service agent is granted `roles/run.invoker` through the existing
  `query_invokers` variable — no new mechanism.
- **In-service:** the query service verifies the end user's token itself and resolves
  entitlements for that principal, as designed.

**The access-token path is implemented** (`src/query/auth.ts`, tested in
`test/query/auth.test.ts`). The service accepts both credential kinds on `Authorization:
Bearer`, told apart by shape:

- a bearer that is three base64url segments is verified as a Google-signed OIDC _ID_ token for
  `QUERY_AUDIENCE` through `google-auth-library`'s `verifyIdToken`; the service additionally
  requires a Google issuer and a `sub` that satisfies the contract's `PrincipalId`;
  `credentialType` is `id-token`. This is the path a Workspace user, a service account, or the
  ADK agent uses.
- any other bearer is treated as a Google OAuth 2.0 _access_ token — what Gemini Enterprise
  forwards. It is sent to Google's tokeninfo endpoint (`OAuth2Client.getTokenInfo`, token in a
  request header, never in a URL the service builds) and accepted only when the response
  carries an `expiry_date` in the future, an `aud` or `azp` present in `QUERY_OAUTH_CLIENT_IDS`
  (Terraform `query_oauth_client_ids`), and a `sub` satisfying `PrincipalId`; `credentialType`
  is `access-token`. With the list empty — the default — every access token is rejected
  without a call to Google.
- successful access-token verifications are cached in process memory, keyed by the SHA-256 of
  the token (never the token), holding only the principal and an expiry equal to the smaller
  of the token's own `expiry_date` and 300 seconds from verification; at most 1,000 entries,
  oldest evicted first; failures are never cached. Revocation inside that window is therefore
  not seen until the entry expires — a bounded, stated residual.

Same principal namespace, same entitlements, same audit record. Not tested: signature and
ID-token expiry rejection, which are the library's behaviour behind a stub in every test, and
the live tokeninfo endpoint, which has not been called from this project.

For the ADK agent path, the `header_provider` route was built: `tools.begin_turn`
(the agent's `before_agent_callback`) generates a UUID and puts it in session state; the
per-request `bearer_header_provider` reads the user's token and that id from session state and
sends `Authorization` and `X-Query-Turn-Id` on every request of the turn. Agent Engine's
brokered Agent Identity was not used. The delegated-trust fallback (the agent asserting a user
under its own identity) is **not needed** and is not built. The wiring is proven with
in-process ADK contexts, not against a deployed runtime.

## Rollout, in two steps

**Step 1 — Gemini Enterprise straight onto the MCP service (layers 1 and 3).** No agent code.
Register the query service as a custom MCP server with OAuth 2.0 in Gemini Enterprise; grant
its service agent `run.invoker`; set `query_oauth_client_ids` to the connector's client id
(the access-token path is built; the id is the one thing it waits on). Staff ask questions in
Google's chat; every tool result is verbatim with hashes. This is the fastest demonstrable
form of (a), and it already proves the tool surface and the tenant wall in a real UI. One
thing the tool surface now says that an integrator must relay: `find_product` answers
`truncated: true` whenever the caller's entitlement holds more documents than the call actually
searched — the scan horizon of 200, the `limit` argument, or the request's store-read budget can
each cut it short — so an empty `products` with `truncated: true` is not "no such product".

**Step 2 — the ADK agent adds layer 2.** A Python `google-adk` agent, its own
deployable under ADR 0004 (`agent/`, own identity, shares only the published contracts),
deployed to Agent Engine and registered in the Agent Gallery, doing four things and nothing
else:

1. Calls the query service's tools through the MCP toolset over streamable HTTP, passing the
   user's token per request.
2. Composes answers in a fixed shape: verbatim blocks from tool results, each with its
   citation (document, version, `sourceKey`, hash), rendered as A2UI cards; the assistant's
   own words in a separate, labelled part.
3. Runs the post-check: every verbatim block back through `verify_quote`; flags any
   `no-match` on the card.
4. Emits one structured record per turn through the same no-narrative logging discipline as
   everything else. Its shape is a published contract like every other evidence artefact —
   `AgentTurnRecord` (`src/contracts/agent-turn.ts`, version 1.0.0): `service`
   (`ema-flow-agent`), `serviceVersion`, `at`, `principal`, `turnId`, `tools` (per call: tool
   name, outcome, duration, result count — at most 200), `spansVerified`, `spansFlagged`,
   `sectionsDropped`, `flags` (the distinct closed flag names raised), `durationMs`. It is
   narrower than first sketched here: no model id, no per-span entries, and no argument digest
   — a `verify_quote` argument _is_ narrative, and a digest of a quote is a way of asking
   whether a document contains a sentence. Which spans failed is on the card; how many, and
   why, is in the record. Without it, the demonstration's first two scenes would produce no
   assessable evidence, so it is part of the agent, not an afterthought.

The two audit trails join on one value. The agent generates `turnId` before the model runs and
sends it as `X-Query-Turn-Id` on every request of the turn; the query service copies it into
every `QueryAuditRecord` of that request as `turnId` (and refuses a header that is not a UUID
with `400`). A reader holding both records can reconstruct which tool calls a turn made and
what the post-check concluded, without either record carrying a word of what was asked or
answered. `find_product`'s `truncated` flag is handled at layer 3 only: the agent composes
blocks from `get_section` results alone, so the instruction tells the model to say a search was
cut short and never to say there is no such product when `truncated` is true; a test checks the
instruction text, nothing checks a model's obedience.

The user's token reaches the query service through the `header_provider` path that holds the
token and the turn id in session state for the turn (see "The identity question, settled");
Agent Engine's brokered Agent Identity was not used.

The same agent is exposed as a Google Chat app through Google's quickstart when a Workspace
surface is wanted.

## Human and organisational prerequisites

- Gemini Enterprise is a licensed product. Confirm the organisation (or the demonstration
  tenant) has it before step 1 is scheduled; the MCP service and the demonstration set do not
  depend on it. (Confirmed 2026-09-21: a trial, active until 2026-10-20.)
- An OAuth 2.0 client (internal consent screen) for the MCP connector — created in the Cloud
  console, not by Terraform; its client id goes into `query_oauth_client_ids`, the list the
  service checks a token's `aud` or `azp` against. Until it is set, access tokens are rejected.
- EU residency: Gemini Enterprise offers the `eu` multi-region and `europe-west2` (London,
  not EU); some features fall back to global. Confirm against the client's residency bar
  before promising EU-only.

## Intended use

Written so that a risk assessment has something to assess rather than a sentence to argue
with.

- **Intended use.** A read-only information-retrieval aid that helps named, trained users in
  Regulatory Affairs, Medical Information, and Promotional Review locate approved ePI content
  and verify quotations against the validated store.
- **Users.** Access is granted per principal (`query_entitlements_json` in phase 1, a
  Firestore-backed grant in phase 2). Only staff who have completed the assistant training —
  what the hash proves, what the post-check does not prove, and when to open the cited
  section — are granted access. Training records are held by the owning organisation.
- **Procedural control.** An answer is a pointer, never a record. Any use of retrieved content
  in a regulatory, medical, promotional, or quality decision requires the user to open the
  cited section by `bundleId`, `versionId`, and `sourceKey` and confirm the displayed hash.
  Assistant answers are not filed as evidence and are not a controlled copy of the product
  information.
- **It must not be used for** regulatory decisions, promotional-copy approval, responses to
  health authorities, safety reporting, patient- or healthcare-professional-facing
  communication, or any use where the _absence_ of information matters: the post-check
  detects alteration, never omission.
- **Outputs and retention.** The system retains only the per-turn record (who, which tools,
  which spans verified); answer text is not retained by this system.
- **Periodic review.** Annually, and on any change to the model version, the normalisation
  version, or the tool contract.

## Residual risks, stated plainly

- **Prompt injection** through document content is not mitigated by the server;
  `ContentNotice` is a declaration the client may ignore. The server removes the consequences
  it can (no write tool, no write role, no cross-request state); a steered assistant on the
  client side remains the integrator's risk.
- **Omission.** The post-check catches alteration, never absence. The assistant may leave
  something out, which is why "the answer is the tool result" matters more than "the assistant
  is careful", and why absence-dependent uses are excluded above.
- **Mis-attribution.** `verify_quote` proves a span is in the document the assistant _named_;
  it does not prove the assistant named the right product, version, or language. The card
  displays product, `bundleId`, `versionId`, and language for the user to confirm, and the
  demonstration shows this being checked.
- **Data handling outside the boundary.** Regulated narrative leaves the validated boundary at the
  tool-result hop and is processed by Gemini and Agent Engine. Before anything but synthetic data
  crosses that hop, three things must be written down here: the contractual basis on which prompts
  and tool results are processed (no-training, no human review, prompt-log retention), the confirmed
  region of each component and whether it matches the query service's `europe-west4` (Gemini
  Enterprise offers the `eu` multi-region and London; some features fall back to global), and the
  data-processing agreement relied on. Until that paragraph exists, the assistant path handles
  synthetic data and, for roadmap item 3a, an authority's published ePI (public, approved text) only
  — no client or confidential content. This is the item a security assessor will hold the assistant
  path on, and it is independent of the query service, which stays within the project's own region.

## The demonstration (delivered, was item 1c)

The line for the client: **FHIR gives every sentence in a label a stable address — product,
version, section — and everything here hangs off that address. PDFs do not have addresses.**

1. **One truth, three windows.** Section 4.4 of a synthetic label as the validated EMA ePI, as
   the BigQuery row that appeared seconds after the write, and as an assistant answer with its
   hash and approver. Same resource, same hash, no copies. The approver shown is synthetic —
   `api-attestation` by a placeholder principal, because the approval service (roadmap item 2)
   is not built — and the presenter says so _before_ the card appears: "no human approved this
   content; what is real is that the store refuses content without an approval." Showing a
   synthetic approver as "who signed off" without that sentence is the most damaging thing
   this demonstration could do in front of a QA lead.
2. **Change one word, watch the world know.** Version 2 of the same label with one sentence
   changed. FHIR history holds both; the ledger shows two hashes; `get_section` gives
   different hashes per version; `verify_quote` with the old sentence matches version 1 and
   not version 2. The assistant is then asked to _quote_ the sentence from each version — two
   verified quotations — rather than to characterise the change: "what changed" is a
   difference, and the post-check verifies presence, never completeness. The list of changes
   comes from the hash comparison on screen; the assistant's part is two checked quotes.
3. **A question a regulator cannot ask a PDF.** Across all products, which section 4.3
   contains the exact phrase "hepatic impairment" — answered with one `verify_quote` per
   product, match or no-match per product, each a mechanical result with a citation. Not "which
   products mention hepatic impairment": that is an absence claim over model judgement, the one
   class of answer this architecture cannot verify, and it must not be the closing scene.

Enablers, all synthetic: a second version of the synthetic label differing by one sentence
(consistently in source text and narrative, so it passes the gate honestly); two or three more
synthetic products; a seeding script that publishes them through the real document path so
Provenance exists; and a written demo script.
