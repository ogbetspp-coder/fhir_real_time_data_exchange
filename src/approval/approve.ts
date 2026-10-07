import { z } from "zod";

import {
  ApprovalStatementSchema,
  MAX_SEQUENCE,
  STATEMENT_VERSION,
  type ApprovalEnvironment,
  type ApprovalStatement,
  type ReviewRecord,
} from "../contracts/approval.js";
import { PrincipalId } from "../contracts/common.js";
import { ApproverRole } from "../contracts/ingestion-provenance.js";
import { sha256 } from "../lib/hash.js";
import type { VerifiedStatement } from "./statement.js";

// The signer's decision, as a pure function (docs/design/approval.md, D2, D3 and D8): who may
// approve, against which head, and the statement that results. The signer's service code fetches
// and stores; everything it decides is decided here, so every refusal has a test.

// --- the approver map ----------------------------------------------------------------------------

// IAP writes a Google subject as `accounts.google.com:<id>`, an ID token as the bare `<id>`: the
// same person (D2). Every subject, in a token or in the map, is compared in the bare form.
const IAP_SUBJECT_PREFIX = "accounts.google.com:";

export function normaliseSubject(subject: string): string {
  return subject.startsWith(IAP_SUBJECT_PREFIX)
    ? subject.slice(IAP_SUBJECT_PREFIX.length)
    : subject;
}

const ApproverEntrySchema = z.strictObject({
  role: ApproverRole,
  // The address the approver's verified token must carry, and the member the review download is
  // granted to (infra/signer.tf). Lower case, as Google writes it.
  email: ApprovalStatementSchema.shape.manifestation.shape.email.regex(/^[^A-Z]+$/),
});

export type ApproverEntry = z.infer<typeof ApproverEntrySchema>;

export type ApproverMap = {
  entries: ReadonlyMap<string, ApproverEntry>;
  // The SHA-256 of the map's canonical JSON, subjects normalised: what every statement names.
  sha256: string;
};

// The server-side approver map (D2), a Terraform variable until the entitlement store holds it:
// `{ "<Google subject>": { "role": "content-reviewer", "email": "…" } }`. A key that is not a
// subject, two keys for one person, or an unknown field fails startup: a map that cannot be read
// exactly approves no one.
export function parseApproverMap(json: string): ApproverMap {
  const raw = z.record(z.string(), ApproverEntrySchema).parse(JSON.parse(json));
  const entries = new Map<string, ApproverEntry>();
  for (const [key, entry] of Object.entries(raw)) {
    const subject = PrincipalId.parse(normaliseSubject(key));
    if (entries.has(subject)) throw new Error("The approver map names one subject twice");
    entries.set(subject, entry);
  }
  const canonical = Object.fromEntries([...entries].map(([subject, entry]) => [subject, entry]));
  return { entries, sha256: sha256(canonical) };
}

// --- the decision ---------------------------------------------------------------------------------

// A person as Google asserted them: the verified identity token's claims (src/signer/identity.ts).
export type VerifiedApprover = { sub: string; email: string; name: string };

export type ApprovalRequest = {
  environment: ApprovalEnvironment;
  // What the approver's click names (D6): the kind and the hash of the review they opened.
  kind: string;
  reviewSha256: string;
  approver: VerifiedApprover;
  approvers: ApproverMap;
  // The review the signer rebuilt from the submission and the current head, and its file's hash.
  review: ReviewRecord;
  rebuiltReviewSha256: string;
  // The document's current head, verified, or undefined when it has none.
  head: VerifiedStatement | undefined;
  signedAt: string;
  signer: { imageDigest: string; keyVersion: string };
};

export type SigningRefusal =
  | "kind-not-built"
  | "unmapped-approver"
  | "approver-email-mismatch"
  | "stale-review"
  | "sequence-exhausted";

// The statement to sign, or why not. The review was rebuilt against the head given, so a review
// built against an older head no longer hashes the same and is `stale-review`: two reviews racing
// for one document cannot both be signed against one baseline, and the loser re-reviews (D8).
export function decideApproval(request: ApprovalRequest): ApprovalStatement | SigningRefusal {
  // Phase 1 builds `approve` only (docs/design/approval.md, "Two phases").
  if (request.kind !== "approve") return "kind-not-built";
  const subject = normaliseSubject(request.approver.sub);
  const entry = request.approvers.entries.get(subject);
  if (entry === undefined) return "unmapped-approver";
  if (request.approver.email.toLowerCase() !== entry.email) return "approver-email-mismatch";
  if (request.reviewSha256 !== request.rebuiltReviewSha256) return "stale-review";

  const { review, head } = request;
  const sequence = head === undefined ? 1 : head.statement.sequence + 1;
  if (sequence > MAX_SEQUENCE) return "sequence-exhausted";

  return ApprovalStatementSchema.parse({
    statementVersion: STATEMENT_VERSION,
    kind: "approve",
    environment: request.environment,
    document: review.document,
    sequence,
    previousStatementSha256: head === undefined ? null : head.statementSha256,
    submissionId: review.submissionId,
    approvedContentSha256: review.approvedContentSha256,
    mappingVersion: review.mappingVersion,
    sections: review.sections.map(({ sourceKey, narrativeDivSha256 }) => ({
      sourceKey,
      narrativeDivSha256,
    })),
    reviewSha256: request.rebuiltReviewSha256,
    approver: { sub: subject, role: entry.role, approverMapSha256: request.approvers.sha256 },
    manifestation: { name: request.approver.name, email: entry.email },
    meaning: review.meaning,
    signedAt: request.signedAt,
    signer: request.signer,
  });
}
