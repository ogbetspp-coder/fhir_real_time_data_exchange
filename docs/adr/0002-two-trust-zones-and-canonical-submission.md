# ADR 0002: Two trust zones and the CanonicalSubmission contract

- Status: Accepted for prototype
- Date: 2026-09-19

## Context

Most product information still exists as authored documents. Converting it into the canonical
Type 2 graph (ADR 0001) is the entry point for every downstream projection, and it cannot be
fully deterministic: locating section boundaries, metadata, and codes in a document requires
judgement that today is best delivered by AI-assisted extraction with human review.

The non-negotiable rules of this repository forbid generating, summarising, or inferring
regulated narrative and require fail-closed, deterministic, evidenced transformations. An LLM
structuring step satisfies none of these on its own. The two concerns must therefore be
separated by a boundary that is enforced mechanically rather than by instruction.

## Decision

The system is split into two trust zones with one versioned contract between them.

**Zone A — Structuring** (separate service; probabilistic, verifiable). Converts an existing,
approved label document into a Type 2 document Bundle. It may decide section boundaries,
metadata, and codes. It may never author, alter, reorder, or omit narrative words. Every code
it assigns must cite a terminology lookup. Its output is a proposal until a human approves it.

**Zone B — Publishing** (this repository; deterministic). Accepts only an approved
`CanonicalSubmission`, re-verifies the invariants below, and then runs the unchanged
transform, validation, persistence, and evidence pipeline. `src/fhir/transform.ts`,
`src/fhir/preflight.ts`, the mapping manifest, and the generated artifacts are not modified by
this decision; their determinism and hashes are frozen.

**The contract** (`src/contracts/`, Zod as the single source of truth; JSON Schema generated
into `contracts/generated/` and enforced by `npm run contracts:check`):

- `CanonicalSubmission` = `schemaVersion` + the Type 2 Bundle + `IngestionProvenance` +
  `Approval`, plus hashes of the Bundle and of the approved content.
- `IngestionProvenance` records the source document (byte hash, media type, size, optional
  source-system identity, optional reference to the extracted text), the extraction tooling
  (run id, service version, parser, optional model, prompt template, terminology service), one
  provenance entry per narrative section (source spans and hashes), every structuring decision
  classified as `extracted-verbatim`, `code-mapped`, `defaulted-by-rule`, `human-edited`, or
  `rejected`, and the fidelity summary.
- `Approval` records an opaque approver id (never an e-mail address), role, timestamp, method
  (`api-attestation` or `manual-record`), an enumerated meaning
  (`reviewed-fidelity-and-structure`), and `approvedContentSha256`, the hash of
  `{ schemaVersion, bundle, provenance }` exactly as approved.

**Invariants Zone B enforces at ingress, before any transformation** (fail closed):

1. `bundleSha256` equals the canonical hash of the received Bundle.
2. `approval.approvedContentSha256` equals the recomputed hash of the received content.
3. The fidelity report parses against its own contract schema, is `passed`, recomputes its
   `reportHash`, was computed over the referenced extracted text (`extractedTextSha256`), and
   its narrative binding hash equals the one recomputed from the Bundle alone. Zone B then
   **always** re-executes the full fidelity check against the extracted text and requires the
   identical `reportHash` (ADR 0003): a report is a claim, the re-execution is the proof, and a
   submission whose extracted text is unavailable is rejected.
4. Sections carrying the canonical source code system and provenance entries are in bijection,
   each section's `narrativeDivSha256` matches its `text.div`, and the Composition's section
   tree is well formed.
5. Conditional decision fields are present (`ruleId` for defaults, `terminologyRef` for codes,
   `editorId` and `reason` for edits and rejections), and a terminology service is declared
   whenever any decision is `code-mapped`.
6. No free text exists anywhere in the Bundle outside the verified narratives: any other
   `text.div`, and any string longer than 300 characters or containing `<`, rejects. Product
   graph fields are names, codes, identifiers, and URLs; regulated prose must travel as a
   verified section. Field-level spans for structured strings are a known extension, not yet
   implemented.

**Transport.** Submissions are passed by reference (`{ uri, sha256 }`), never inline: real
bundles are multi-megabyte, orchestration payload limits are small, and request bodies transit
logs. Extracted source text travels separately by reference and is never logged. Status: the
contract and the ingress gate exist; the reference resolver, the `/v1/runs` `document` route,
and the Workflows branch are the next phase, so today the document path is reachable only from
tests and `src/fixtures/synthetic-submission.ts`. The `fixture` and `healthcare-api` sources are
pre-existing trusted inputs guarded by IAM, not by this gate; deployments where Zone A is the
only producer should disable them.

**Versioning.** Every contract root carries a `schemaVersion` literal and a `$id` that embeds
it. Objects are strict (unknown keys reject) so content cannot be smuggled in unnamed fields.
Patch changes alter descriptions only; minor changes add optional fields and require Zone B to
deploy before Zone A emits them; anything else is a new major `$id`. Enum additions on fields
that Zone B branches on are major. `zod` is pinned exactly to keep generated schemas stable.

**Offsets.** Span offsets are Unicode code points; span hashes are SHA-256 of the UTF-8 bytes of
the raw slice. See ADR 0003.

## Consequences

- AI output can never reach the FHIR store or the evidence bucket without a hash-bound human
  approval and a passing mechanical fidelity check. UR-09 to UR-16 in
  `docs/validation/README.md` trace these controls to tests.
- `RunManifest` moves to `schemaVersion` 1.1.0 with an optional `ingestion` block containing
  hashes, counts, enumerations, and identifiers only. Version 1.0.0 remains readable.
- A Zone A implementation in another language must generate its models from
  `contracts/generated/` and prove fidelity-check equivalence against the golden vectors.
- Real electronic signature, identity-provider binding of `approverId`, segregation of duties,
  and signature-to-record binding beyond hash reference are future control boundaries. Nothing
  here claims Annex 11 or 21 CFR Part 11 compliance.
- Strict objects make every minor contract change a Zone B-first deployment.
