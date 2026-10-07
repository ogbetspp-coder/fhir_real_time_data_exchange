import { z } from "zod";

import type { SignedApprovalStatement } from "../contracts/approval.js";
import {
  APPROVER_ROLE_SYSTEM,
  PARTICIPANT_TYPE_ATTESTER,
  PARTICIPANT_TYPE_SYSTEM,
} from "../fhir/provenance.js";
import type { FhirResource } from "../fhir/types.js";
import { stableUuid } from "../lib/hash.js";
import { signedStatementBytes } from "./statement.js";

// Each stored version is linked to its approval after it is written (docs/design/approval.md,
// D5): a second Provenance whose target is the versioned `Bundle/<id>/_history/<vid>`, with the
// deterministic id `stableUuid(bundleId, versionId)`, carrying the signed statement in
// `Provenance.signature`. The query service reads it by id, never by search.
//
// The signature element carries the signed statement's canonical JSON as its `data`, so the
// statement's bytes and its signature travel together, byte for byte what the document's head
// entry holds: `targetFormat` application/json (the statement), `sigFormat` application/json (the
// approval-statement contract's envelope, whose `signatureBase64` is RSA-PSS over the statement).
// It names no `activity`: the repository's activity code system (scripts/fhir/generate-artifacts.ts)
// has no code for it, and the statement's own `meaning` says what the signature means.

// The approver's Google subject, as the attester agent names them.
export const APPROVER_SUBJECT_SYSTEM = "https://accounts.google.com";
export const SIGNED_STATEMENT_FORMAT = "application/json";

export function approvalLinkId(bundleId: string, versionId: string): string {
  return stableUuid(bundleId, versionId);
}

export function approvalLinkTarget(bundleId: string, versionId: string): string {
  return `Bundle/${bundleId}/_history/${versionId}`;
}

export function approvalLinkProvenance(
  bundleId: string,
  versionId: string,
  signed: SignedApprovalStatement,
): FhirResource {
  const { statement } = signed;
  const who = { identifier: { system: APPROVER_SUBJECT_SYSTEM, value: statement.approver.sub } };
  return {
    resourceType: "Provenance",
    id: approvalLinkId(bundleId, versionId),
    target: [{ reference: approvalLinkTarget(bundleId, versionId) }],
    recorded: statement.signedAt,
    agent: [
      {
        type: { coding: [{ system: PARTICIPANT_TYPE_SYSTEM, code: PARTICIPANT_TYPE_ATTESTER }] },
        role: [{ coding: [{ system: APPROVER_ROLE_SYSTEM, code: statement.approver.role }] }],
        who,
      },
    ],
    signature: [
      {
        when: statement.signedAt,
        who,
        targetFormat: SIGNED_STATEMENT_FORMAT,
        sigFormat: SIGNED_STATEMENT_FORMAT,
        data: Buffer.from(signedStatementBytes(signed), "utf8").toString("base64"),
      },
    ],
  };
}

const LinkSchema = z.object({
  resourceType: z.literal("Provenance"),
  id: z.string(),
  target: z.array(z.object({ reference: z.string().optional() })).length(1),
  signature: z
    .array(
      z.object({
        targetFormat: z.literal(SIGNED_STATEMENT_FORMAT),
        sigFormat: z.literal(SIGNED_STATEMENT_FORMAT),
        data: z.string().regex(/^[A-Za-z0-9+/]+={0,2}$/),
      }),
    )
    .length(1),
});

// The signed statement's bytes a stored link carries, held to the version it was read for: its id
// and its one target must be that version's. Undefined for anything else; the caller then verifies
// the bytes like any other statement and fails closed on whatever they are.
export function approvalLinkBytes(
  resource: unknown,
  bundleId: string,
  versionId: string,
): string | undefined {
  const parsed = LinkSchema.safeParse(resource);
  if (!parsed.success) return undefined;
  const link = parsed.data;
  if (link.id !== approvalLinkId(bundleId, versionId)) return undefined;
  if (link.target[0]?.reference !== approvalLinkTarget(bundleId, versionId)) return undefined;
  const data = link.signature[0]?.data ?? "";
  const bytes = Buffer.from(data, "base64");
  if (bytes.toString("base64") !== data) return undefined;
  return bytes.toString("utf8");
}
