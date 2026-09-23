# Approval: a named person signs, and only the text they approved answers

_Roadmap item 2. Design, 2026-09-22. Nothing in this note is built. It was revised once after an
independent adversarial review (validation, security and engineering lenses) and is reviewed
again before any of it runs._

## What this is for

The north star's proof layer has three parts: a fidelity check that decides mechanically whether
two texts are the same, a content hash on every narrative, and **an approval that binds a named
person to a hash**. The first two are delivered. The third is a placeholder: the contract has an
`Approval`, but whoever writes a submission also writes the approver's name into it, and nothing
checks that the person named ever saw it.

The claim this design must make true is narrow: **the text the query service serves traces to
the approval of that text, by a named person, and to no other approval.**

### Delivered when

1. A version that no valid approval covers is refused by the query service (`not-approved`).
2. The same content, approved and published, answers. (Approval happens before publication, so
   a signed publish is always a new store version: "the same content" means the same hashes.)
3. The answer's audit record, and the evidence behind it, name the approver as a person: name,
   time and meaning of the signature, not only an opaque id.
4. A version that a later approval of the same document supersedes is not served as the current
   text, and an approval can be withdrawn.

## What already exists

- `Approval` (`src/contracts/ingestion-provenance.ts`) records `approverId` (an opaque principal
  id, never an e-mail address), `approverRole`, `approvedAt`, `method` (`api-attestation` |
  `manual-record`, described in the contract itself as "attestation placeholders"), `meaning`
  (`reviewed-fidelity-and-structure`) and `approvedContentSha256`, the hash of
  `{ schemaVersion, bundle, provenance }` exactly as approved.
- The ingress gate recomputes that hash and refuses content that does not match it
  (`src/contracts/canonical-submission.ts`). Approval is bound to the _submitted_ content; it is
  not bound to a person, and nothing binds it to what is later served.
- The pipeline writes a `Provenance` whose target is the unversioned `Bundle/<id>`
  (`src/fhir/provenance.ts`), in the same transaction as the Bundle, and signs its run manifest
  with the worker's HSM key.
- The query service answers from the newest stored version and attaches the newest Provenance,
  whatever version was asked for. A separate fix in progress (branch `query-proof-fixes`) stops
  it attaching an approval to a version that is not current. This design replaces that rule.

## Decisions

### D1. One approval, at the boundary that already exists, made real

The human decision stays where ADR 0002 put it: at the Zone A → Zone B boundary, where a
proposal (Zone A's structure, later the engine's output) becomes a record. No second approval is
added after publishing. The approval covers the narrative, which the fidelity check then proves
is published unchanged, and the structure as submitted. The EMA headings, QRD codes and list
metadata come from the mapping, not from the submission; the mapping is validated separately, so
the statement names the mapping version it will be published under (D3).

What the signature means, stated on every record (21 CFR 11.50 and Annex 11 §14 ask that a
signature carry its meaning; this design claims neither): _"I reviewed this structured record
against the approved source label and attest that its narrative and structure represent that
label."_ It is **not** the regulatory approval of the label.

### D2. The person is asserted by Google, never by a form

The approver is the subject of a Google-signed identity token that the signer verifies itself,
from the raw token, with a fresh issue time. The role comes from a server-side approver map, whose
version hash goes into the statement. The display name and e-mail from the same verified token
are recorded **with** the approval, as its manifestation for a human reader, never as its id.
Subjects are normalised (IAP's `accounts.google.com:<id>` and an ID token's bare `<id>` are the
same person) and tested against the entitlement map's keys.

### D3. The service signs a statement that binds the person to the document and its text

A person cannot hold a Cloud KMS key, so the signature is the approval service's, over a
statement that names the person and pins exactly what they approved:

```text
ApprovalStatement {
  statementVersion        "approval-statement/1"
  kind                    "approve" | "reject" | "withdraw"
  environment             "dev" | "prod"                   // a dev approval never publishes in prod
  document                { Type 2 Bundle.identifier system + value, EMA bundle id, language }
  sequence                1, 2, 3 … per document            // order is the signer's, not a clock's
  previousStatementSha256 the document's head before this one, or null
  submissionId
  approvedContentSha256   as today
  mappingVersion          e.g. cap-smpc-en#1.1.0
  sections                [{ sourceKey, narrativeDivSha256 }] // what the query service re-checks
  reviewSha256            hash of the exact review the approver was shown (D6)
  approver                { sub, role, approverMapSha256 }
  manifestation           { name, email }                   // for people; never an id
  meaning                 the D1 sentence's code
  signedAt                the signer's clock
  signer                  { imageDigest, keyVersion }
}
```

It is signed as `canonicalJson(statement)` with RSA-PSS (salt 32) on a new HSM key,
`approval-signing-hsm`, used by nothing else, held by a new service account used by nothing else
(ADR 0004). The signature proves _"the approval service, having verified that Google identity
`sub` made this request, recorded this"_. It does not prove the person held a key, and the
evidence says so.

Verification is a pure library with no cloud imports (principle 6): canonical bytes, public key,
signature → valid or not. The public key versions are published beside the evidence, so a third
party can verify an approval without access to Google Cloud.

### D4. Approvals are per environment; content hashes, not store ids

The statement never names a Healthcare API `versionId`, which is local to one store. It does name
its environment, and each environment trusts only its own key versions. Publishing into
production (roadmap item 7) therefore means approving there, or an explicit production
countersignature; a `dev` approval never publishes in production.

### D5. Each stored version is linked to its approval after it is written

FHIR R5 `Bundle` is a Resource, not a DomainResource: it has no `extension`, and a `meta.tag` on
it would be copied from the submission and so could be planted by it. So the Bundle stays as it
is. After the transaction commits, the pipeline writes a second `Provenance` whose target is the
**versioned** reference `Bundle/<id>/_history/<vid>`, with the deterministic id
`stableUuid(bundleId, versionId)`, carrying the statement's canonical bytes and signature in
`Provenance.signature`. The query service reads it directly by id: no search, no newest-first.
If the second write fails, the version has no approval and is refused. Fail closed.

### D6. What the approver sees is built, fixed and hashed before they see it

Nobody can meaningfully approve a hash. The review shows, per section, the heading, the text as
it will be published, and what changed since the document's current head approval; the full text
is always reachable, not only the diff. It also shows the fidelity result, the source document's
identity and the mapping version.

- The review is a pure function of the submission and the previous head, built by a tested
  library (the diff is regulated logic: a diff that hid a change would get a signature over text
  nobody saw).
- It is rendered once, as an immutable file in the evidence bucket under `reviews/`. Narrative
  never enters BigQuery, Workflows execution history or a chat message (ADR 0002: no ledger row
  carries prose; a Chat message is limited to 32 000 bytes anyway).
- Its hash travels in the approver's click and into the statement. The signer rebuilds the
  review from the submission and refuses if the hash differs. What was shown is what is signed.

### D7. Segregation of duties, defined

The approver may not be any of: an `editorId` on a `human-edited` or `rejected` structuring
decision (those ids become Google subjects for this purpose); the principal that wrote the
submission object; the principal that started the review. At least two people are therefore
required, one to prepare and one to approve. Today one person builds, deploys and would approve;
the design does not pretend otherwise, and `dev` records this as a known gap.

### D8. One head per document; the head is the current text

The signer keeps a head object per document under `approvals/heads/`, updated only by
compare-and-swap (Cloud Storage `ifGenerationMatch`). It refuses to sign unless the statement's
`previousStatementSha256` is the current head: two reviews racing for the same document cannot
both win, and the loser re-reviews against the new baseline. The pipeline refuses to publish a
statement that is not the head, so replaying an old signed submission cannot make old text
current. A signed `withdraw` statement moves the head to "no current approved text" without
publishing anything; a signed `reject` records who refused what.

The current approved text of a document is the version whose linked statement is the head. An
explicitly requested older version is served only if its statement is valid, and the answer says
it is superseded and by which statement.

### D9. The query service verifies, on every answer

For a version it serves, the query service: reads the linked Provenance by id (D5); verifies the
signature with the pure library against the environment's public keys (fetched once, cached);
checks the statement is the head, or marks the answer superseded; and **recomputes each served
section's `narrativeDivSha256` from the Composition and compares it with the statement's
`sections`**. Only then does it answer. A version with no linked approval, an invalid signature,
or a section that does not match is `not-approved`.

This bounds reads: one extra read per answered document, so `find_product`'s scan budget is
recalculated rather than silently exhausted, and its answer lists only documents with a current
approval.

## The flow

1. Zone A (or, today, the seeding script) writes a submission by reference to the submissions
   bucket, with no approval in it: a submission is no longer born approved.
2. A review service (no signing key) runs the gate's checks except approval, builds the review
   (D6), writes it to `evidence/reviews/` and records `{submissionId, reviewSha256, status}` in
   the ledger. No narrative in the ledger or in Workflows.
3. The approver opens the review and approves, rejects or withdraws.
4. The signer verifies the approver's token itself, checks role, segregation of duties and the
   review hash, compare-and-swaps the head, signs the statement and writes it to
   `evidence/approvals/`.
5. An Eventarc trigger on `approvals/` starts the publish run, pinned to the statement by hash.
   No Workflows callback: completing a callback needs `roles/workflows.invoker`, which can only
   be granted project-wide and would also let the signer start or cancel any execution.
6. The pipeline verifies the statement (signature, head, content hash, environment), publishes,
   then writes the versioned Provenance (D5).
7. The query service verifies on every answer (D9).

## Open decision: which Google component asserts the approver

| Option                                             | Who asserts the person                                                                                                                                                                               | Custom code                 | Re-authentication at signing        | Strength                                                                          |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------- | ----------------------------------- | --------------------------------------------------------------------------------- |
| **A. AppSheet**                                    | AppSheet, which signs the user in but calls out as itself                                                                                                                                            | None                        | No                                  | Weakest: the signer takes AppSheet's word, by e-mail                              |
| **B. Google Chat app built as a Workspace add-on** | Google: the add-on event carries `userIdToken`, a Google-signed ID token with the user's `sub` (requested with the `userinfo.email` scope), beside `systemIdToken` proving the call came from Google | A small add-on and receiver | No                                  | Good, if the receiver forwards the raw tokens and the signer verifies them itself |
| **C. A review page behind Identity-Aware Proxy**   | IAP: a Google-signed JWT on every request                                                                                                                                                            | A small page on Cloud Run   | Yes, IAP can require recent sign-in | Strongest, and the only one that can re-authenticate                              |

A classic Chat app is not option B: its body names the user, but only Chat's own token is signed,
and that identifies Chat, not the person.

**Recommendation: a one-day spike, then decide. Lean to B for the everyday review and record the
re-authentication gap, or C if the client's validation lead requires re-authentication at each
signing (21 CFR 11.200 expects an identifying component at each signing).** The spike records,
against the real products: an add-on click reaching a receiver whose endpoint admits only the
add-ons service agent, both tokens verified by a separate signer, their audience and issue time;
and whether IAP's re-authentication setting behaves as documented. C is a custom interface, and
the only case that needs an exception to principle 7.

## Contract changes

| Contract                                | Change                                                                                                     | Version                                        |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| `CanonicalSubmission`                   | `approval` removed from the submission; the placeholder methods cease to exist, with no environment switch | major (ADR 0002: a field the gate branches on) |
| New `ApprovalStatement`, `ReviewRecord` | As in D3 and D6, generated into `contracts/generated/`                                                     | new, 1.0.0                                     |
| `QueryErrorCode`                        | adds `not-approved`                                                                                        | query-tools major                              |
| `get_provenance`, `get_section`         | carry the statement hash, the approver's manifestation, `superseded`, `supersededBy`                       | same major                                     |
| `QueryAuditRecord`                      | adds `approverSub`, `statementSha256`                                                                      | same major                                     |

## Infrastructure and controls

- **Key.** `approval-signing-hsm` (ASYMMETRIC_SIGN, RSA-PSS, HSM), under `prevent_destroy` and
  the key-guard deny policy. Key-guard exempts the deployer today; for this key, any IAM change
  pages, and verifiers accept only statements whose `signer.imageDigest` is on an allowlist, with
  Binary Authorization on the signer, so a deployer who grants itself the key or ships a signer
  that signs anything is visible and refused.
- **Identities.** A signer account with `signerVerifier` on that key only and write on
  `approvals/` only; a review service account with write on `reviews/` only; the worker and the
  query service with `publicKeyViewer` only. The worker's bucket-wide `objectCreator` gets a
  condition excluding `approvals/` and `reviews/` (ADR 0004: the approval store is not written by
  the publisher). All of them join the effective-IAM export.
- **Retention.** Statements must outlive what they approve: the evidence retention lock (a
  production-gate item) covers `approvals/` and `reviews/`, and who may shorten retention is
  written down.
- **Approver map.** A Terraform variable until the entitlement store (roadmap item 6) holds it;
  its hash is in every statement, and a change to it is a change record.
- **Run sources.** Wherever real content lives, `enabled_run_sources = ["document"]`, so no
  unsigned route publishes. In `dev`, the `fixture` smoke product stays unsigned and is the
  standing proof that the query service refuses unapproved content.

## Build order, each step with its evidence

1. **Spike** (open decision). Evidence: a recorded, verified identity from the chosen surface, or
   a written finding that it cannot be had.
2. **Statement, review and verifier libraries** (pure) with negative tests: another key, other
   content, another document, a replayed old head, a stale review hash, the submitter as
   approver, an unmapped subject, a wrong environment. Evidence: the tests.
3. **Signer and review services**, head compare-and-swap. Evidence: a signed statement and its
   review file in the evidence bucket; a racing second approval refused.
4. **Pipeline verifies and links** (D5). Evidence: an unsigned submission refused; the same
   content signed, published, with its versioned Provenance.
5. **Query service verifies** (D9). Evidence: `not-approved` for the smoke product; an approved
   version answering with the approver's name in its evidence; a superseded version marked; a
   tampered section refused.
6. **Migration.** The four seeded demo approvals are placeholders and become `not-approved` at
   step 5, and the query-tools major changes what the live Gemini connector calls. Re-seed through
   the signer and roll the connector forward in the same change, before 2026-10-20, when the
   Gemini Enterprise trial ends.
7. **Validation work.** Intended use, an architecture section, traceability rows in
   `docs/validation/README.md` for each negative test, effective-IAM export for the new accounts.

## What this does not claim

It does not claim 21 CFR Part 11 or Annex 11 compliance: that needs the organisation's
procedures, training, identity lifecycle and record retention as well. It produces the technical
evidence such a claim would rest on. It does not approve label content; it attests that a record
represents an approved label.
