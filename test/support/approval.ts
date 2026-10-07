import { constants, generateKeyPairSync, sign, type KeyObject } from "node:crypto";

import {
  ApprovalStatementSchema,
  STATEMENT_VERSION,
  verifyDocumentSubmission,
  type ApprovalStatement,
  type DocumentGateResult,
  type SignedApprovalStatement,
} from "../../src/contracts/index.js";
import { parseApproverMap, type ApproverMap } from "../../src/approval/approve.js";
import { recordFacts, type RecordFacts } from "../../src/approval/review.js";
import {
  headObjectName,
  signedStatementBytes,
  type HeadSource,
  type KeySource,
} from "../../src/approval/statement.js";
import type { EmaMapping } from "../../src/fhir/mapping.js";
import { transformType2ToEma, type EmaPackage } from "../../src/fhir/transform.js";
import {
  createSyntheticSubmission,
  type SyntheticSubmissionOptions,
} from "../../src/fixtures/synthetic-submission.js";
import { canonicalJson } from "../../src/lib/hash.js";
import { SYNTHETIC } from "./submission.js";

// Locally generated keys and statements, standing in for Cloud KMS: a statement signed here with
// RSA-PSS (SHA-256, salt 32) is what KMS's RSA_SIGN_PSS_3072_SHA256 makes of the same digest.

export const KEY_RING = "projects/p/locations/europe-west4/keyRings/ema-flow-dev-evidence";
export const KEY_VERSION = `${KEY_RING}/cryptoKeys/approval-signing-hsm/cryptoKeyVersions/1`;
export const OTHER_KEY_VERSION = `${KEY_RING}/cryptoKeys/approval-signing-hsm/cryptoKeyVersions/2`;
export const IMAGE_DIGEST = `sha256:${"a".repeat(64)}`;
export const SUBJECT = "109876543210987654321";
export const EMAIL = "approver@khs.dev";

let pair: { publicKey: KeyObject; privateKey: KeyObject } | undefined;
let other: { publicKey: KeyObject; privateKey: KeyObject } | undefined;

export function approvalKeys(): { publicKey: KeyObject; privateKey: KeyObject } {
  pair ??= generateKeyPairSync("rsa", { modulusLength: 3_072 });
  return pair;
}

export function otherKeys(): { publicKey: KeyObject; privateKey: KeyObject } {
  other ??= generateKeyPairSync("rsa", { modulusLength: 3_072 });
  return other;
}

export function signStatement(
  statement: ApprovalStatement,
  privateKey: KeyObject = approvalKeys().privateKey,
): SignedApprovalStatement {
  const signature = sign("sha256", Buffer.from(canonicalJson(statement), "utf8"), {
    key: privateKey,
    padding: constants.RSA_PKCS1_PSS_PADDING,
    saltLength: 32,
  });
  return { statement, signatureBase64: signature.toString("base64") };
}

// The key source a reader in `dev` would have: this key version and no other.
export const trustedKeys: KeySource = (keyVersion) =>
  Promise.resolve(keyVersion === KEY_VERSION ? approvalKeys().publicKey : undefined);

export type ApprovableRecord = {
  gate: DocumentGateResult;
  transformed: EmaPackage;
  facts: RecordFacts;
  parts: ReturnType<typeof createSyntheticSubmission>;
};

export function approvableRecord(
  mapping: EmaMapping,
  options: SyntheticSubmissionOptions = {},
): ApprovableRecord {
  const parts = createSyntheticSubmission(mapping, options);
  const gate = verifyDocumentSubmission(
    {
      submission: parts.submission,
      fidelityReport: parts.fidelityReport,
      sourceText: parts.sourceText,
    },
    mapping.sourceCodeSystem,
    SYNTHETIC,
  );
  const transformed = transformType2ToEma(gate.bundle, mapping);
  return { gate, transformed, facts: recordFacts(gate, transformed, mapping), parts };
}

export function approvers(): ApproverMap {
  return parseApproverMap(
    JSON.stringify({ [SUBJECT]: { role: "content-reviewer", email: EMAIL } }),
  );
}

// A statement for a record, as the signer would make it, with any field overridden.
export function statementFor(
  facts: RecordFacts,
  overrides: Partial<ApprovalStatement> = {},
): ApprovalStatement {
  return ApprovalStatementSchema.parse({
    statementVersion: STATEMENT_VERSION,
    kind: "approve",
    environment: "dev",
    document: facts.document,
    sequence: 1,
    previousStatementSha256: null,
    submissionId: facts.submissionId,
    approvedContentSha256: facts.approvedContentSha256,
    mappingVersion: facts.mappingVersion,
    sections: facts.sections,
    reviewSha256: "b".repeat(64),
    approver: { sub: SUBJECT, role: "content-reviewer", approverMapSha256: approvers().sha256 },
    manifestation: { name: "Synthetic Approver", email: EMAIL },
    meaning: "record-represents-approved-label",
    signedAt: "2026-10-06T12:00:00.000Z",
    signer: { imageDigest: IMAGE_DIGEST, keyVersion: KEY_VERSION },
    ...overrides,
  });
}

// An in-memory bucket: create-if-absent, never replaced, as the real heads bucket's retention makes
// it.
export class MemoryHeads implements HeadSource {
  public readonly objects = new Map<string, string>();

  public list(prefix: string): Promise<string[]> {
    return Promise.resolve([...this.objects.keys()].filter((name) => name.startsWith(prefix)));
  }

  public read(name: string): Promise<string | undefined> {
    return Promise.resolve(this.objects.get(name));
  }

  public readonly types = new Map<string, string>();

  public create(name: string, bytes: string, contentType = ""): Promise<"created" | "exists"> {
    if (this.objects.has(name)) return Promise.resolve("exists");
    this.objects.set(name, bytes);
    this.types.set(name, contentType);
    return Promise.resolve("created");
  }

  public append(signed: SignedApprovalStatement): void {
    const name = headObjectName(signed.statement.document, signed.statement.sequence);
    if (this.objects.has(name)) throw new Error("head exists");
    this.objects.set(name, signedStatementBytes(signed));
  }
}
