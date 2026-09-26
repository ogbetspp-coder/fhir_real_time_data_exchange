# Approval: a named person signs, and only the text they approved answers

_Roadmap item 2. Design, 2026-09-22. Nothing in this note is built. It was revised once after an
independent adversarial review (validation, security and engineering lenses), amended on
2026-09-25 for an authority import's request statement after that amendment's own reviews (roadmap
3a's PR 3c design), and is reviewed again before any of it runs._

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
  `{ schemaVersion, graphType, bundle, provenance }` exactly as approved.
- Since `CanonicalSubmission` 2.0.0 (roadmap 3a, `docs/design/authority-import-contract.md`, D2
  and D8) `Approval` is a union on `method`: the attestation above, or `authority-publication`,
  an authority import's. Its meaning is `authority-publication-imported`: the approval is the
  authority's publication (ePI id, document, List, version number, procedure number, the
  Bundle's timestamp, `authorityStatus: pilot`), and the human decision is the request to import
  it. `requestedBy` names who made that request and `requestedAt` when. Like `approverId`, it is
  a placeholder that the submission's writer fills in and nothing verifies, until this design
  binds it to a verified identity; the FHIR Provenance records it as the `enterer`, not as an
  attester. An import runs only dry until roadmap 3a PR 5, so no import is yet served.
- The ingress gate recomputes that hash and refuses content that does not match it
  (`src/contracts/canonical-submission.ts`). Approval is bound to the _submitted_ content; it is
  not bound to a person, and nothing binds it to what is later served.
- The pipeline writes a `Provenance` whose targets are the record's identifier and the
  unversioned `Composition/<id>` and `Bundle/<id>` (`src/fhir/provenance.ts`), in the same
  transaction as the Bundle, and signs its run manifest with the worker's HSM key.
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
  environment             "dev" | "validation" | "prod"    // one environment's approval never publishes in another
  document                { Type 2 Bundle.identifier system + value, EMA bundle id, language }
  sequence                1, 2, 3 … per document            // order is the signer's, not a clock's
  previousStatementSha256 the document's head before this one, or null
  submissionId
  approvedContentSha256   as today
  mappingVersion          e.g. cap-smpc-en#1.2.0
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

The signer keeps a head per document, appended in a bucket of its own (amended 2026-09-25, proposed for item 2's review: under
retention an object cannot be updated, so a head is the highest of append-only, create-if-absent
objects; see the amendment). It refuses to sign unless the statement's
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

This bounds reads (amended, proposed for item 2's review: the linked Provenance, the head's listing and entry, and for a request
the product chain's), so `find_product`'s scan budget is
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
   review hash, signs the statement, writes it to `evidence/approvals/`, and appends the head
   (amendment, proposed for item 2's review: in its own bucket, create-if-absent).
5. An Eventarc trigger on `approvals/` starts the publish run, pinned to the statement by hash.
   No Workflows callback: completing a callback needs `roles/workflows.invoker`, which can only
   be granted project-wide and would also let the signer start or cancel any execution.
6. The pipeline verifies the statement (signature, head, content hash, environment), publishes,
   then writes the versioned Provenance (D5).
7. The query service verifies on every answer (D9).

## Which Google component asserts the approver

**Decided 2026-09-22 by the owner: B, a Google Chat app built as a Workspace add-on.** The
re-authentication gap is recorded, not closed; C stays the answer if a client's validation lead
requires re-authentication at each signing.

| Option                                             | Who asserts the person                                                                                                                                                                               | Custom code                 | Re-authentication at signing        | Strength                                                                          |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------- | ----------------------------------- | --------------------------------------------------------------------------------- |
| **A. AppSheet**                                    | AppSheet, which signs the user in but calls out as itself                                                                                                                                            | None                        | No                                  | Weakest: the signer takes AppSheet's word, by e-mail                              |
| **B. Google Chat app built as a Workspace add-on** | Google: the add-on event carries `userIdToken`, a Google-signed ID token with the user's `sub` (requested with the `userinfo.email` scope), beside `systemIdToken` proving the call came from Google | A small add-on and receiver | No                                  | Good, if the receiver forwards the raw tokens and the signer verifies them itself |
| **C. A review page behind Identity-Aware Proxy**   | IAP: a Google-signed JWT on every request                                                                                                                                                            | A small page on Cloud Run   | Yes, IAP can require recent sign-in | Strongest, and the only one that can re-authenticate                              |

A classic Chat app is not option B: its body names the user, but only Chat's own token is signed,
and that identifies Chat, not the person.

The spike (build step 1) records, against the real products: an add-on click reaching a receiver
whose endpoint admits only the add-ons service agent, and both tokens verified by the signer
itself, with their audience and issue time. 21 CFR 11.200 expects an identifying component at
each signing; B does not re-authenticate, which is the recorded gap. C is a custom interface, and
the only case that would need an exception to principle 7.

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

## Two phases: what is built while the product is being tested, and what waits

The owner's direction (2026-09-22): the product is still being built and tested, so build the
simplest thing that makes the claim true, and hold the hardening a client's validation would
require for the production gate.

**Phase 1, built now: everything the claim depends on.** Without any one of these, an answer
could name an approval that does not cover its text, which is the failure the product exists to
prevent.

- The statement (D3) with document identity, mapping version, section hashes, the review hash,
  the approver's verified subject and manifestation, environment, sequence and previous head.
  `approve` only; `reject` and `withdraw` wait.
- The approver asserted by the Chat add-on's Google-signed token, verified by the signer (D2).
- One signer on Cloud Run with its own identity and its own HSM key. The review file is rendered
  by the signer itself, not a separate review service; it is still built by a pure function, stored
  once under `reviews/`, and its hash still travels in the click and the statement (D6).
- The head, append-only in its own bucket (D8 as amended, proposed for item 2's review), so a replay or a race cannot make old
  text current.
- The versioned Provenance written after commit (D5), and the query service's verification and
  re-hash on every answer (D9).
- The pipeline started by hand, or by the signer calling the existing workflow, in `dev`; no
  Eventarc trigger yet.
- The migration of the four seeded demo documents and the connector, before 2026-10-20.

**Phase 2, the production gate: hardening, not the claim.** Each is a line on the production
gate, so production cannot go live without it.

- Segregation of duties (D7), for approvals; for an authority import's requests, whether a second
  content-reviewer countersigns (the amendment below). In `dev` one person prepares and approves;
  the evidence says so.
- `reject` and `withdraw` statements.
- The image-digest allowlist, Binary Authorization on the signer, and paging on IAM changes to
  the key (Infrastructure).
- (The worker's write condition moves to phase 1, amendment, proposed for item 2's review.) A separate review service with its
  own identity for Type 2 reviews; for an authority import's requests it is phase 1's fallback
  behind Identity-Aware Proxy, if the spike needs it (amendment, proposed for item 2's review).
- Eventarc in place of a direct call; re-authentication at signing if a client requires it
  (option C).
- The validation work: intended use, architecture section, traceability rows, effective-IAM
  export for the new accounts.

## Build order for phase 1, each step with its evidence

1. **Spike** on option B. Evidence: a recorded add-on click reaching a Cloud Run receiver with
   both Google-signed tokens verified, and their audience and issue time.
2. **Statement, review and verifier libraries** (pure) with negative tests: another key, other
   content, another document, a replayed old head, a stale review hash, an unmapped subject, a
   wrong environment. Evidence: the tests.
3. **Signer**, with the append-only head (amended, proposed for item 2's review). Evidence: a signed statement and its review file
   in the evidence bucket; a racing second approval refused.
4. **Pipeline verifies and links** (D5). Evidence: an unsigned submission refused; the same
   content signed, published, with its versioned Provenance.
5. **Query service verifies** (D9). Evidence: `not-approved` for the smoke product; an approved
   version answering with the approver's name in its evidence; a superseded version marked; a
   tampered section refused.
6. **Migration.** The four seeded demo approvals are placeholders and become `not-approved` at
   step 5, and the query-tools major changes what the live Gemini connector calls. Re-seed through
   the signer and roll the connector forward in the same change, before 2026-10-20, when the
   Gemini Enterprise trial ends.

## What this does not claim

It does not claim 21 CFR Part 11 or Annex 11 compliance: that needs the organisation's
procedures, training, identity lifecycle and record retention as well. It produces the technical
evidence such a claim would rest on. It does not approve label content; it attests that a record
represents an approved label.

## Amendment (2026-09-25, an authority import's request statement)

`docs/design/authority-import-withheld.md` and `docs/design/authority-import-renderer.md` (roadmap
3a, PR 3c; owner decisions of 2026-09-25) make a person judge two things about an authority import
that no check proves: that each contact the renderer gate could not prove harmless is legible, and
which sections are withheld on a confirmed defect. PR 5 persists an authority import only once that
person is attested, so phase 1 of this design gains the following. It changes nothing a Type 2
approval decides; the proposed heads mechanics, if item 2's review adopts them, apply to every
statement kind.

**What binds now, and what item 2's own review settles.** This amendment is written for roadmap
3a's PR 3c; item 2 is designed here, not built, and "is reviewed again before any of it runs". So
only these eight requirements, and the unmarked bullets below that elaborate them, bind now, and
item 2 must meet them before PR 5 (where a served review's bytes are trusted to a deployer rather
than re-hashed, that trust root is stated, as requirement 6 states its limits):

1. A `request` statement, derived from the source kind, with its meaning codes; the requester is an
   attested person in the `content-reviewer` role; the request is one content-reviewer's
   judgement (whether production adds a countersignature is the owner's decision).
2. What was shown is what is signed: a self-contained review, built by the signer from the
   submission, a recomputation of the authority's own bytes (by the signer or by a component whose
   result it verifies, never the draft) and the attested renderer record, after it has
   verified, for this environment, the record's attestation, its captures index and each capture's
   hash, shows every acknowledged contact and every withheld section's confirmed and rejected defects,
   each with its captures, and the product and List; the signer refuses a draft the gate would refuse; nothing between the
   person and the signer can change what their action names (the review's hash, the kind and the
   submission); and the build's tests refuse a tampered capture, index or attestation, and one of
   another environment.
3. Who opened the review is recorded by a Google component, or the design says it is not.
4. The statement pins the carried sections and the withheld set; the query service fails closed on
   any disagreement, on a withheld safety or non-leaf section, and reads the defect kinds from the
   signed statement.
5. One List per product at a time: the statement signs `{ authority, epiId }` and the List, taken
   from the recomputed import, not the draft; every document served as current for a product comes
   from that product's one List, checked at signing (a serialised check: of racing requests only one
   is checked against a given product state, and a loser is checked again against the new state, so
   two whose Lists differ cannot both pass), at publish and on every answer; supersession across Lists waits for its own reviewed
   design.
6. No request's head, and no product-level record, can be replaced, hidden or rolled back while it is
   retained, with the limits stated (before retention is locked at the production gate, and after
   it expires); a reader fails closed on anything malformed; and a write sequence interrupted at any
   point fails closed or is rolled forward, never leaving a state that breaks requirements 4 to 6.
7. Where the publication's fields go, and the versions, as stated below.
8. Phase 1's properties hold for requests: the person is asserted by a Google-signed token that the
   signer verifies itself (D2); the statement is signed by an HSM key of its environment (D3, D4)
   over the submission's `approvedContentSha256` and the review's hash; the pipeline persists an
   authority import only under a request statement whose signature verifies against its
   environment's keys, which is the head and whose `approvedContentSha256` matches, and otherwise
   refuses, writing nothing to the FHIR store (the refusal is recorded as any refusal is; the
   flow's step 6); each stored version is linked to its statement (D5); the head is the current text
   (D8); and the query service verifies all of it on every answer (D9). Item 2's review may change
   how these are met, not whether.

The bullets below elaborate these requirements. Those marked _proposed_ (the heads bucket, the
product chain's entries, write order and roll-forward, how a head is read, beyond retention, reviews
in the versioned bucket, the grants, the build order), and the flow's storage, link and
Identity-Aware Proxy details, are the approach proposed to meet them; the rest bind as stated. Item 2's own design review settles them, with these questions from roadmap
3a's review rounds open: the document head must be read only after the product chain is read and
rolled forward, and a lost race re-reads both (never reusing earlier positions), a head create that
finds different bytes failing closed; a committed product entry whose head was never written must be
visible to D9 (as the head, or rolled forward on a schedule); the click's parameters (review hash,
kind, submission) are not signed by the add-on's tokens, so the receiver is a trust root to put
under the signer's image allowlist (which then moves to phase 1), or the signer is the endpoint, with each token consumed once and
a stated freshness window, to meet requirement 2; D9 needs a lookup from the head's statement to its stored version (a
second deterministic Provenance id from the statement's hash, or a bounded walk); Type 2 approvals
have no product chain and write the head before `approvals/`; a withdraw's place in the product
chain; roll-forward completing `approvals/` and the publish, verifying the entry, and treating an
equal "exists" as success; D9's read count; a deny policy scoped by a tag on the heads bucket and
covering `objects.move`; the bytes each hash covers; the reviews bucket's own retention, and that a served review's bytes hash to the signed value (or the trust root is stated); and the
signer's egress to the authority and the wider surface of the process holding the key.

- **A statement kind, `request`,** with its own meaning codes: for an import with renderer evidence,
  "I was shown the renderer gate's captures of this publication's record; each listed contact is
  legible; each listed defect is confirmed or rejected as stated; I request this import"; for a
  synthetic import without renderer evidence, "I request this synthetic import". The signer derives
  the kind from the submission's source: `request` for an authority import (a document in the
  `authority-import:` namespace), `approve` for any other; D9 and the pipeline require exactly that
  pairing, and `get_provenance` and the audit record carry the kind and its meaning, so a requester
  is never shown as the text's approver. For an authority import the request is the statement D5
  links and D9 verifies; there is no second `approve`, since the content's approval is the authority's
  publication (ADR 0005 decision 4). `document` is generalised to the canonical `Bundle.identifier`
  (an import's is `authority-import:ema:<id>`).
- **The flow.** (1) The requester drafts the decisions: the producer writes the submission, whose
  request (approved content) lists every contact of the record (the lookup requires exactly those),
  and each withheld section's confirmed and rejected defects.
  (2) The signer runs the lookup's evidence checks (the renderer note's R5) against its own image's
  verified store (a commit differing from the worker's refuses at publish; the record and captures the request names,
  the environment, the gate version and constants, no failure or refusal in a carried section, each
  withheld section's eligibility, the contact cap, and that the acknowledged contacts and the
  withheld sections' confirmed and rejected defects exactly partition the record's), verifies the
  record's attestation, its captures index and each capture's hash, checks the List (below), and
  builds the review (D6) as a pure function of the submission, the recomputed import and the attested record: the record and captures named by
  `renderEvidence`; every acknowledged contact with its captures and every identity each drawing
  stands for; and each withheld section's confirmed and rejected defects with their captures. It
  stores the review as one self-contained immutable file, `reviews/<reviewSha256>` (a PDF, or HTML
  with every capture inline as a `data:` PNG and no markup of the authority's inside), written
  create-if-absent; on "exists" it hashes the stored bytes before the card is sent. (3) The Chat card
  carries only a link to that file and its hash. The link is Cloud Storage's authenticated browser
  download (`storage.cloud.google.com/<bucket>/<object>`, a Google surface), which the approver map's
  members may use through `roles/storage.objectViewer` on the evidence bucket, conditioned on the
  `reviews/` prefix (a condition on an object-name prefix needs uniform bucket-level access, which
  the bucket has; the members' emails come from the approver map, kept with each `sub` in the same
  Terraform variable). Cloud Storage's data-access log redacts the principal of such a download made
  outside the Google Cloud console, so it shows that the review was fetched, not by whom. Step 1's
  spike records what the log holds for a card-link download and for one from the console's object
  page (which may also need `storage.objects.list`, denied by the prefix condition). If neither names
  the person, a separate review service on Cloud Run (not the add-on receiver, whose caller identity
  IAP would replace) serves the review behind Identity-Aware Proxy (a Google component): the approver
  map's members get `roles/iap.httpsResourceAccessor` instead of `objectViewer`, the IAP service agent
  gets `roles/run.invoker`, the service reads `reviews/`, and on each fetch it writes, from the
  verified IAP token, `reviews/fetches/<reviewSha256>/<sub>` create-if-absent; the signer then refuses
  to sign unless that object exists for the signing `sub`. Step 1's spike and step 3 carry this path. (4) The person opens it and clicks; to
  change a drafted decision they decline (do not sign), and a new draft is made. What was shown is
  what is signed.
- **The review has no diff.** D6 shows the diff from the document's current head; a request's content
  is the authority's publication, not an edit, so its review shows the decisions and identities, not
  a diff, and says so.
- **One List per product at a time; supersession is not designed here.** The EMA issues a new List
  version with every document under a new Bundle id, and it keeps serving older Lists and documents
  as current (observed 2026-09-26: Brukinsa's EPI/23/1009 has two current Lists, and the older SmPC is
  still served). So no check the signer can make at signing shows which document replaces which, nor
  tells the tablets SmPC from the capsules one, which share a List and differ only in their text. Until
  a separate, reviewed design settles supersession (a precondition of PR 5 for any product whose List
  changes), the rule is closed: every current import of a product comes from the same List. The
  statement signs `{ authority, epiId }` and the List's GUID, `versionNumber` and hash. A
  product-level record, immutable under requirement 6, names the product's List and its current
  imports; the signer refuses a request whose List differs from it (`list-differs`), newer or older;
  racing requests are checked one at a time against the product's state (requirement 5); and a document whose head is a request is a
  current import whether or not it has published. The pipeline and D9 require the served statement's
  List to equal the product-level record's. How that record is stored, and a `withdraw`'s place in
  it, are proposed below.
  So a newer List's documents are imported only after the older imports are withdrawn (phase 2), and
  an older List's never displace a newer one's; a re-import after a normalisation change is refused
  too if the List's bytes have changed since, until `withdraw` exists. An import's text therefore stays
  current even if the authority has replaced it, with its version and pilot status in every answer:
  stale, not wrong, and stated. For Imatinib Teva nothing is blocked: only the tablets SmPC is
  importable (the capsules' 5.1 cannot be withheld), and both SmPCs share the List.
- **A request without renderer evidence.** A synthetic import that withholds nothing carries no
  `renderEvidence` and is never persisted in production (the contract's D7). A synthetic import that
  withholds a section carries renderer evidence (the renderer note's R5) and is a request like any
  other.
- _Proposed._ **Heads under retention, in their own bucket.** The evidence bucket keeps object versions, and a
  retained live version can still be made noncurrent, which would hide the newest head from a
  listing; and retention forbids replacing an object, so D8's head cannot be one object updated by
  compare-and-swap. So heads live in a bucket of their own, `approval-heads`, with a retention policy
  (locked at the production gate) and no versioning: nothing in it can be replaced, hidden or
  deleted before it ages out (the default 2 555 days; the guarantee ends there, stated). Heads are
  append-only: after signing, the signer creates `docs/<sha256(document)>/<sequence>` holding the
  signed statement, create-if-absent (`ifGenerationMatch=0`), the sequence zero-padded to twelve
  digits so a listing's order is numeric; the current head is the highest sequence, read by a
  listing, which Cloud Storage makes strongly consistent; two racing statements cannot both create
  the same sequence, and the loser re-reviews. The product chains (above) live beside them under
  `products/`. The signer's rights there are create and read only; the query service and the
  pipeline read and list the bucket (a bucket-wide list, since a prefix condition cannot grant
  listing), and D9's per-answer reads become the linked Provenance, one listing and the head, so
  `find_product`'s scan budget is recalculated. A test hides nothing because nothing can be hidden: a
  delete or an overwrite is refused. This corrects D8, the flow, phase 1 and build step 3 for every
  statement kind. The worker's bucket-wide `objectCreator` on the evidence bucket is conditioned to
  exclude `approvals/` and `reviews/` in phase 1, not phase 2.
- _Proposed._ **The product chain: its entries, order and checks.** For a request, the statement also signs
  `productSequence` and `previousProductEntrySha256`, and the product chain's entry is the signed
  statement itself, the commit point. The order is: (1) the product entry,
  `products/<key>/<productSequence>`, create-if-absent; (2) the document head, the same bytes; (3)
  the statement under `approvals/`; (4) the publish run. A request that loses the product entry to
  a racing request for another document of the same product is signed again by the signer at the
  next position, with the same review (the click covers the review's hash, not the position); one
  for the same document re-reviews. Before accepting a new request for a product, the signer rolls
  forward any product entry whose document head is missing. The current document chains of a
  product are those its product chain names, and nothing else. The chain lives at
  `products/<sha256(authority, epiId)>/<sequence>`, every request (and every `withdraw`, once built)
  extends it, and two racing requests for one product collide on its next sequence, so only one
  wins. Keys are the SHA-256 of the canonical
  JSON (RFC 8785) of `{ authority, epiId }` and of the document identifier.
- **Where the key and the List come from.** Before signing, the gate's own
  recomputation of the import runs, by the signer or by an unprivileged component whose result the
  signer verifies (it fetches the authority's bytes and recomputes, D1 of the contract design; which
  is proposed, with the signer's egress) and takes `{ authority, epiId }` and the List's GUID, `versionNumber` and hash from it,
  never from the draft; the review shows the product and the List. The pipeline requires the
  statement's product and List to equal the recomputed source record's.
- _Proposed._ **Reading a head.** A reader (the signer, the pipeline, D9) lists the chain, takes the highest
  entry, and requires: its name matches `^\d{12}$` and equals the statement's sequence; the
  statement's document (or product) hashes to the prefix; its previous hash equals the entry below
  it; and, for a request, the head's bytes equal `products/<key>/<productSequence>`. A malformed or
  unreadable highest entry fails closed (`not-approved`), never falling back to the one below. D9
  answers from the stored version linked to the head, not the newest stored version. Its reads per
  answered document are the linked Provenance, the head's listing and entry and, for a request, the
  product chain's listing and entry; `find_product`'s scan budget is recalculated from that.
- _Proposed._ **Beyond retention.** An IAM deny on `storage.objects.delete` for the heads bucket (the key-guard
  pattern) and no lifecycle rule keep a head in place after its retention expires; before the policy
  is locked at the production gate, an administrator could remove it, stated.
- _Proposed._ **Reviews in the versioned evidence bucket.** A principal who may delete there could make
  `reviews/<hash>` noncurrent and create different bytes under the name; the IAP review service
  hashes the bytes before serving them, and for a direct download the deployers with delete rights on
  the bucket are the trust root, stated.
- **Its `sections`** list every carried section with narrative, by its `narrativeDivSha256`, and
  every withheld section, with `status: withheld`, the notice's hash and its confirmed defect kinds.
  D9 requires the served version's withheld set to equal the statement's exactly, every statement
  section to be present, and every served section with text to be listed: a missing statement
  section, an unlisted served section or a hash that does not match is `not-approved`, and only then
  is a withheld set, a `partial` or any other status that disagrees `record-inconsistent`. The query
  service fails closed on a withheld section that is a safety section (4.2 to 4.9, or under one) or is
  not a leaf, so no store write can plant a withholding behind a valid approval, and it reads a
  withheld section's defect kinds from the signed statement, for every version.
- **`reject` never moves the head,** for a request as for an approval; phase 1 does not build it,
  and declining to sign is how a draft is changed.
- **The head (D8) applies to requests,** keyed by the canonical `Bundle.identifier`, which the
  authority's document id determines; a new id is a new chain, subject to the List rule (above).
  The authority keeps serving a document it has replaced, so a signed request still publishes it;
  the List rule keeps it from displacing a newer List's import.
- **The role.** The signer requires `content-reviewer` for every request, including a synthetic
  import's without renderer evidence.
- **Segregation (D7) for a request.** A request is one content-reviewer's judgement in every
  environment: D7's bar on the principal that wrote the submission does not apply, since the
  requester drafts the decisions they sign, and the renderer record it rests on is regenerated
  deterministically on `main` (the renderer note's R1), so its proposer has no discretion over it.
  Whether production requires a second content-reviewer to countersign a request is a production-gate
  decision for the owner, stated, not assumed.
- **Where the publication's approval goes.** Item 2 removes `approval` from the submission. For an
  authority import the publication's fields (ePI id, document, List, version number, procedure
  number, the Bundle's timestamp) and `authorityStatus: pilot` move into the source record
  (`provenance.sourceDocument`, which gains the procedure number and timestamp), and `requestedBy`
  and `requestedAt` give way to the statement's approver and `signedAt`; the request statement signs
  over them through `approvedContentSha256`. ADR 0005 decision 4 and the contract design's D8 will
  point here (the withheld note's forward pointers), in the change that makes the move.
- **Versions.** `ApprovalStatement` and `ReviewRecord` are new, so their 1.0.0 includes the `request`
  kind and its meaning codes, D3's shape extended with `{ authority, epiId }`, the List's GUID,
  `versionNumber` and hash, whatever fields the settled mechanics add (proposed:
  `productSequence` and `previousProductEntrySha256`), `sections` entries with `status: withheld`, the notice's hash and defect kinds, and the import
  review's shape. Item 2's majors of `CanonicalSubmission` and the query tools and roadmap 3a's (the
  renderer and withheld notes' 3.0.0) are one major each if they land together, and consecutive
  otherwise; whichever lands second takes the next number. The contract table above gains the
  `request` kind, `ReviewRecord`'s import review, `record-inconsistent` and `get_section`'s
  `section-withheld`.
- _Proposed._ **Identities.** The signer's grants become: `signerVerifier` on its key; create-only on
  `approvals/`; create, and `objectViewer` (read and list), on the heads bucket; create-if-absent and read on `reviews/`, except
  `reviews/fetches/`, which it only reads; read on the submissions it signs over; read on the render
  build's attestation and captures buckets. The IAP review service, if built, reads `reviews/` and
  creates `reviews/fetches/` only. The query service and the pipeline read and list the heads
  bucket. The approver map's members gain `storage.objectViewer` on the evidence bucket, conditioned
  on `reviews/` (or, under Identity-Aware Proxy, the web app user role, above).
- _Proposed._ **Build order.** Step 1's spike adds a review opened from a card link through the authenticated
  browser download, rendered inline with its captures visible, and what the data-access log records
  of it; steps 2, 3 and 5 of phase 1 add request cases (the review library; a request signed over an
  authority import's review; a draft the gate would refuse, a tampered capture, index or attestation,
  and one of another environment, refused; a request from a List other than a current import's
  refused; append-only heads under retention, two racing statements refused; the query service
  verifying a request and its withheld set), before PR 5.
