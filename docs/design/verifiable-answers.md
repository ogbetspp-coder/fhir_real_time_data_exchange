# Design note: verifiable answers on Google-native surfaces

- Status: Proposed, 2026-09-20 — Google product capabilities marked _(verify)_ are being
  checked against current documentation before item 1b starts
- Related: `docs/design/epi-mcp-query-service.md`, `docs/adr/0004-service-boundaries-and-shared-code.md`,
  `docs/roadmap.md` items 1, 1b, 1c

## The promise, stated narrowly enough to be true

An assistant's prose cannot be made trustworthy, and no one should claim it in front of a
regulated buyer. What can be made true is this:

> Every sentence an answer presents as label content is verbatim from the store, carries a
> hash a reader can recompute, and has been mechanically re-checked against the store after
> the assistant wrote it. Anything else in the answer is visibly the assistant's own words.

That is a different promise from "the assistant is correct". It is achievable, it is testable,
and it is the promise a QA function can put an intended-use statement around. The assistant is
never the source of truth; it is a guide to the source of truth, and every pointer it gives
can be checked. This is the brand rule — AI proposes, math proves, humans decide — applied to
reading.

## Three layers of enforcement, from hard to soft

1. **Hard: the tool surface.** The query service (item 1) returns narrative verbatim with
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

Layers 1 and 2 are ours. Layer 3 and everything a user sees are Google's.

## Google-native surfaces, no custom frontend

The standing decision is that no custom UI is built where a Google surface serves the
purpose. For an assistant that is the strongest version of the decision, because the surface
is where a product would otherwise sink most of its effort.

| Role                        | Google component                                                                     | Ours?                         |
| --------------------------- | ------------------------------------------------------------------------------------ | ----------------------------- |
| Chat surface for staff      | Gemini Enterprise: a registered agent in the organisation's agent gallery _(verify)_ | No                            |
| Chat surface in Workspace   | A Google Chat app backed by the same agent _(verify)_                                | No                            |
| Embeddable chat on a portal | Conversational Agents messenger widget, Google-hosted _(verify)_                     | No                            |
| The agent's logic           | Agent Development Kit (Python) agent with an MCP toolset pointing at our service     | **Yes — small, and the moat** |
| Agent runtime               | Vertex AI Agent Engine, EU region _(verify availability)_                            | No                            |
| The model                   | Gemini, pinned by version                                                            | No                            |
| The tools                   | The read-only query service (item 1)                                                 | **Yes**                       |
| The record                  | Cloud Healthcare API FHIR store, Provenance, evidence bucket, ledger                 | Yes — already built           |
| Analytics beside the chat   | Looker / Looker Studio over the BigQuery stream                                      | No                            |

The MCP service is model-agnostic by construction: the same server serves Gemini Enterprise,
Claude, or an in-house agent. That is a sales point, not an accident.

## The agent (item 1b)

A Python ADK agent, its own deployable under ADR 0004 (`agent/`, own identity, shares only
the published contracts), doing four things and nothing else:

1. Calls the query service's tools through an MCP toolset over streamable HTTP with a bearer
   token.
2. Composes answers in a fixed shape: verbatim blocks from tool results, each followed by its
   citation (document, version, `sourceKey`, hash); the assistant's own words in a separate,
   labelled part.
3. Runs the post-check: every verbatim block back through `verify_quote`; flags any
   `no-match`.
4. Emits one structured record per turn — which tools were called, which spans verified —
   through the same no-narrative logger discipline as everything else.

**The open question that decides the design:** whose identity reaches the query service.
Entitlements are per principal, so a tool call made under the agent's own service account
loses the user. Two candidate answers, to be settled by what Google supports today
_(verify)_: (a) Gemini Enterprise / Agent Engine passes an end-user authorization to tool
calls, in which case the query service verifies the user's token as designed; or (b) the agent
calls with its own service-account token and asserts the end-user principal in a separate
claim, in which case the query service must accept that assertion **only** from the agent's
service account — a delegated-trust extension that must be stated in the design note, tested
negatively, and recorded in the audit record as "on behalf of". (a) is preferred; (b) is
acceptable for the demonstrator and must be named as a control boundary if used.

## What this is and is not, for the intended-use statement

- It is an information-retrieval aid for trained staff. The validated boundary is the store,
  the hashes, the tools, and the post-check. The model sits outside it, like a browser.
- It does not make regulatory decisions, does not replace review, and is not patient-facing.
  Patient-facing use is a separate, later, higher bar.
- Residual risks are the ones already named for the query service: prompt injection through
  document content (bounded by `ContentNotice` and by the model, not eliminated), and prose
  omission (the assistant may leave something out; the post-check catches alteration, not
  absence — which is why "the answer is the tool result" matters more than "the assistant is
  careful").

## The demonstration (item 1c)

The line for the client: **FHIR gives every sentence in a label a stable address — product,
version, section — and everything here hangs off that address. PDFs do not have addresses.**

1. **One truth, three windows.** Section 4.4 of a synthetic label as the validated EMA ePI, as
   the BigQuery row that appeared seconds after the write, and as an assistant answer with its
   hash and approver. Same resource, same hash, no copies.
2. **Change one word, watch the world know.** Version 2 of the same label with one sentence
   changed. FHIR history holds both; the ledger shows two hashes; `get_section` gives
   different hashes per version; `verify_quote` with the old sentence matches version 1 and
   not version 2. Then the assistant is asked what changed, and every line of its answer is
   checkable.
3. **A question a regulator cannot ask a PDF.** Across all products, which list hepatic
   impairment in section 4.3 — answered from coded sections, with citations.

Enablers, all synthetic: a second version of the synthetic label differing by one sentence
(consistently in source text and narrative, so it passes the gate honestly); two or three more
synthetic products; a seeding script that publishes them through the real document path so
Provenance exists; and a written demo script.
