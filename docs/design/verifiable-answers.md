# Design note: verifiable answers on Google-native surfaces

- Status: Proposed 2026-09-20. The query service is deployed (`ema-flow-dev-query`, first
  2026-09-20; all four tools answered live 2026-09-21). The agent is deployed to Agent Engine
  (`reasoningEngines/6226059359072288768`, europe-west4, Python 3.14) and registered in Gemini
  Enterprise; its post-check has not yet run in a live Gemini turn. Which build the engine runs
  is recorded in `agent/deploy/README.md` and, since 2026-09-27, in every audit record's
  `serviceVersion` (`agent/<version>+<commit>`). The build deployed before 2026-09-27 predates
  that day's audit fixes — among them, it fails every turn whose user id is an e-mail address —
  so the live post-check (roadmap item 1) waits on a redeploy from `main`. Both are delivered
  (were roadmap items 1 and 1b). Google product capabilities below were read from Google's
  public documentation (dated per source, inline); the MCP connector, Agent Engine and agent
  registration have since been exercised in this project's tenant, the rest has not and must be
  re-confirmed in the console before it is relied on
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
can put an intended-use statement around. What keeps it honest, as built (2026-09-27):

- **Quotation slots are filled by code, never by the model.** Every section the model fetches
  with `get_section` in the turn becomes a block, verbatim from the tool result, with its
  citation; the model cannot write into one. So an unverified string cannot be presented as a
  quotation. The blocks are what was read, not a selection the model vouches for, and the answer
  says so; at most eight are shown, and the answer says how many more were read.
- **Verbatim display depends on the fence.** Gemini Enterprise renders the answer as Markdown,
  and label text read as Markdown changes: `<ULN and bilirubin >` is taken for an HTML tag,
  `_not_` for emphasis, `&micro;` for an entity, a line opening `# ` or `1. ` for a heading or a
  list, and a `<!--` swallows the rest of the answer. So each block — status, quotation,
  citation, checksums — is a fenced code block, in which nothing is parsed, the quotation
  wrapped at spaces so that its lines joined by single spaces are the stored text exactly. A
  test renders adversarial label text through a CommonMark parser and requires each quotation
  back out of its fence exactly.
- **The model's own words cannot render as anything but its own words.** On the text surface
  they are shown inside a fenced code block, between a label line and an end line; Markdown
  and HTML are not parsed inside a fence, and the fence is longer than any run of backticks in
  them, so nothing the model writes — emphasis, an entity, a tag, a comment, a forged end line
  — renders as markup or closes the box. That is the structural guarantee. As defence in depth
  the words are also filtered. They are shown as written — "10⁹/L" stays "10⁹/L" — less only the
  invisible code points that can hide or reorder text (the joiners some scripts need are kept);
  the patterns read a compatibility-folded copy mapped back to the original: a line that opens
  with (or has the shape of) a label reserved for checked text is removed — read with Markdown,
  HTML and look-alike Cyrillic and Greek letters set aside — and checksum-like runs of
  hexadecimal digits and document identifiers named with an identifier-like value are cut out
  where they stand. Words past 20,000 characters are cut, with a note. Each removal is said in
  the answer and recorded (`assistantFlags`). The filters are patterns and can be missed; the
  fence does not depend on them.
- **Label text in the model's own words is pointed out, not checked.** Where they share eight or
  more consecutive words with a block, the answer says they repeat label text and are not
  checked, and the record carries `label-text-repeated`. This is a runtime flag, not the
  acceptance test over model answers this note once promised; a paraphrase that shares fewer
  than eight words in a row is not caught, and nothing sends a quotation in the model's words
  through `verify_quote`. Both are open.

The assistant is never the source of truth; it is a guide to the source of truth, and every
pointer it gives can be checked. This is the brand rule — AI proposes, math proves, humans
decide — applied to reading.

## Three layers of enforcement, from hard to soft

1. **Hard: the tool surface.** The query service returns narrative verbatim with
   `narrativeDivSha256` and `normalizedTextSha256`, names the document version, and marks
   content fields with `ContentNotice`. It has no tool that summarises, drafts, or writes. A
   client cannot obtain paraphrased label text from it because none exists.
2. **Mechanical: the post-check.** At the end of every turn, every block of label content is
   sent back through `verify_quote`, in chunks of at most 2,000 UTF-16 units. A block is
   verified only when every chunk comes back `match` in the section cited, at exactly the
   code-point offsets the chunk was cut from — so the matches cover the block end to end — and
   every hash agrees: each match names the section's own `normalizedTextSha256`, each answer
   hashes the chunk that was sent, the block's text and XHTML hash to the values the reader is
   shown, and the answer was computed under the normalisation version the agent was built
   against (`fidelity-norm/3.2.0`). Anything else is flagged on the block, and the audit record
   shows it. So a normalisation-version bump in Zone B needs the agent redeployed, ported to the
   new version, **before** the service answers under it; otherwise every block shows unverified
   (`checksum-mismatch`) until it is. Each block says which checksum is confirmed by what: the
   normalised text's hash is the one every `verify_quote` match must name; the approved
   narrative's is the stored XHTML's, recomputed by the agent from what `get_section` returned
   and required to agree, not re-confirmed by the service at check time. The chunks are evened
   out in length, so a block just over one window is two halves rather than a chunk and a
   sliver. A block holding a table or a picture is flagged `table-not-quotable`: the service
   refuses a quote carrying their markers, so that part is not sent. This check does not depend
   on the model: a model update can make the assistant less helpful, but it cannot make an
   altered quote pass as verified. It is the single most important piece and it is small.
3. **Soft: the instruction.** The agent's system instruction tells it to answer from tool
   results, to fetch the sections that answer the question, and to say in its own words only
   which of them do and why — never to reproduce label text, identifiers or hashes, which the
   checked blocks carry. This is the least reliable layer and is treated as such — it improves
   quality; it proves nothing.

Layers 1 and 2 are ours; layer 3 and everything a user sees are Google's. The **validated
boundary** is narrower than "ours": it is the query service, the fidelity library, and the
store. The post-check is our code and runs on every turn, unconditionally — the agent's
`after_agent_callback` composes, checks and renders, and the model's own text is held back until
then — but it runs inside the agent, which is outside the boundary, so a block without a
verification stamp is, procedurally, unverified.

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
inline citations do not coexist with strict JSON output, which is why structured display was
planned through A2UI cards. **Not yet used either:** the deployed agent returns its answer as
plain structured text, the text of the turn's final event (`render.render_text`). Sending A2UI
needs an A2A surface that advertises the extension, and nothing in the deployed path does; the
A2UI renderer built for it was never sent and was removed in refactor R1 (history: `250d8a2`).

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
under its own identity) is **not needed** and is not built.

Cloud Run's edge does not accept the user's OAuth token, so the first live turn through the
agent was refused `401` before the service saw it (2026-09-22). The same callback therefore
also sends `X-Serverless-Authorization`: an ID token for the service's own URL, minted from the
runtime's credentials (off the event loop, cached for the hour), which Cloud Run consumes and
strips; the service still sees, verifies and entitles only the user's `Authorization`. That
runtime identity is, unless the agent is deployed with its own service account
(`AGENT_ENGINE_SERVICE_ACCOUNT`, `agent/deploy/README.md`), the project's shared Reasoning
Engine service agent — so it is the whole project's Agent Engine, not this agent alone, that
the query service's edge admits. A dedicated account holding `run.invoker` alone is the fix, and
an owner step.

The user id the agent is given is Gemini Enterprise's, and it is the user's e-mail address. The
agent's audit record never carries it: `principal` is the fixed value `session-user-withheld`
(only a numeric subject or a URN is carried as it is), with an optional digest beside it
(`principalDigest`: HMAC-SHA256 of the casefolded id under a key of at least 32 bytes), and the
principal the query service
verified from the user's own token is on the service's records of the same `turnId`. The wiring
is proven with in-process ADK contexts and a real ADK `Runner`; the edge header and the
header provider were exercised on Agent Engine, the post-check has not yet been.

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
   citation (document, version, `sourceKey`, hash) and the product and language `find_product`
   named for that document version in the turn, rendered as plain structured text (no A2UI —
   see above); the assistant's own words in a separate, labelled part.
3. Runs the post-check: every verbatim block back through `verify_quote`, held to exact
   coverage and matching hashes (layer 2 above); flags anything else on the block.
4. Emits one structured record per turn — on every way a turn can end, including those that
   show no answer — through the same no-narrative logging discipline as everything else. Its
   shape is a published contract like every other evidence artefact — `AgentTurnRecord`
   (`src/contracts/agent-turn.ts`, version 1.1.0): `service` (`ema-flow-agent`),
   `serviceVersion`, `at`, `principal` (never an e-mail address) and optionally
   `principalDigest`, `turnId`, `outcome` (`answered`, `tools-unavailable`, `model-failed`,
   `turn-id-missing`, `internal-error`) and optionally `errorClass`, `tools` (per call: tool
   name, outcome, duration, result count — at most 200 — for the model's calls and the
   post-check's alike), `spansVerified`, `spansFlagged`, `sectionsDropped`, `flags` (the
   distinct closed flag names raised), `assistantFlags`, `durationMs`. It is narrower than first
   sketched here: no model id, no per-span entries, and no argument digest — a `verify_quote`
   argument _is_ narrative, and a digest of a quote is a way of asking whether a document
   contains a sentence. Which spans failed is on the card; how many, and why, is in the record.
   The record is built apart from the answer: if the full record cannot be built, a minimal one
   with the exception's class — and, for an answered turn, the checked answer's counts and
   flags — is written, and the checked answer is still shown. Without it,
   the demonstration's first two scenes would produce no assessable evidence, so it is part of
   the agent, not an afterthought.

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
- **Outputs and retention.** The agent's own audit trail retains only the per-turn record
  (which tools, which spans verified, how the turn ended), and the query service's only its
  per-call records. **Answer text is retained elsewhere, by the platform.** Deployed on Agent
  Engine with the default session service, the agent's conversation is stored in Agent Engine
  Sessions (`VertexAiSessionService`, which `AdkApp` selects when the runtime names its engine;
  `agent/deploy/deploy_agent_engine.py` sets no other): the user's question, every tool result
  of the turn — including each `get_section` answer, the full section narrative — and the
  rendered answer, in the engine's region (`europe-west4`), for the session's lifetime, readable
  by anyone holding Agent Engine session read permissions on the project. The user's token is
  not among them (`temp:` state is not persisted). This repository sets no session lifetime and
  has not confirmed the default; Gemini Enterprise keeps its own conversation history under its
  own settings. Not retaining any of this (an in-memory session service, at the cost of
  conversation history across runtime instances) is an owner decision, open.
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
  it does not prove the assistant named the right product, version, or language. Each block
  shows `bundleId`, `versionId`, `sourceKey` and the checksums, and the product name and language
  that a `find_product` answer of the same turn gave for that exact document version — or, when
  the turn looked up no such answer (or two disagreed), says the product and language were not
  confirmed, rather than guess. The user confirms them; the demonstration shows this being
  checked. The product line is as good as `find_product`'s answer, which is not itself
  re-checked.
- **The assistant's own words.** They are shown, labelled, fenced, and never checked. What is
  enforced is that they cannot render as a checked block (the fence) and, as far as the
  filters reach, do not carry reserved labels, checksums or identifiers (see "The promise").
  What is not: a paraphrase of label text sharing fewer than eight words in a row with a block;
  a checksum or identifier spelt in a form the patterns do not know (spaced into groups, or
  named in prose — "document version 7"); a surface that does not render Markdown shows the
  fence as two lines of backticks rather than a box; and the look-alike fold covers Cyrillic and
  Greek only.
- **Exact coverage fails closed.** A chunk of a block whose text also occurs earlier in the same
  section matches there first, at other offsets, and the block is flagged `coverage-gap` although
  it is the label's text; evening out the chunks makes a short, repeatable last chunk unlikely,
  not impossible. A block holding a table or a picture cannot be checked at all yet
  (`table-not-quotable`); cell-level quoting is roadmap 3a's.
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
