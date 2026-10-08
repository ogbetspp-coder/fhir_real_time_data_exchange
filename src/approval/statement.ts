import { constants, createPublicKey, verify, type KeyObject } from "node:crypto";

import {
  MAX_SEQUENCE,
  SignedApprovalStatementSchema,
  type ApprovalDocument,
  type ApprovalEnvironment,
  type ApprovalStatement,
  type ApprovedSection,
  type SignedApprovalStatement,
} from "../contracts/approval.js";
import type { EmaMapping, SectionRule } from "../fhir/mapping.js";
import type { CompositionSection } from "../fhir/types.js";
import { canonicalJson, sha256, sha256Utf8, stableUuid } from "../lib/hash.js";

// The approval statement's pure library (docs/design/approval.md, D3 and D8): its hash, the
// document's head entries, and verification. No cloud import, no clock, no environment read
// (ADR 0004, principle 6): bytes, a public key and a signature in, a verdict out. The signer, the
// pipeline and the query service all verify through this file, and a third party can too.

// The one algorithm an approval is signed with: RSA-PSS with SHA-256 and a 32-byte salt, on a
// 3072-bit key. Cloud KMS names it RSA_SIGN_PSS_3072_SHA256 (its PSS salt is the digest's length).
export const APPROVAL_KEY_ALGORITHM = "RSA_SIGN_PSS_3072_SHA256";
export const APPROVAL_KEY_BITS = 3_072;
export const PSS_SALT_LENGTH = 32;

// Why a statement or a head is refused, as a closed code: never a message, never content.
export type ApprovalRefusal =
  | "malformed"
  | "not-canonical"
  | "untrusted-key"
  | "bad-signature"
  | "wrong-environment"
  | "other-document"
  | "other-submission"
  | "other-content"
  | "other-mapping"
  | "other-record"
  | "section-mismatch"
  | "no-head"
  | "malformed-head"
  | "not-head"
  | "ungated-source";

export class ApprovalRefusedError extends Error {
  public override readonly name = "ApprovalRefusedError";

  public constructor(public readonly reason: ApprovalRefusal) {
    super(`The approval was refused: ${reason}`);
  }
}

// What a statement is known by: the SHA-256 of its canonical JSON, which is also the digest the
// approval service signs.
export function statementSha256(statement: ApprovalStatement): string {
  return sha256(statement);
}

// A document's key in the heads bucket: the SHA-256 of its identity's canonical JSON.
export function documentKey(document: ApprovalDocument): string {
  return sha256(document);
}

// The heads of one document: `docs/<documentKey>/<sequence>`, the sequence zero-padded to twelve
// digits so a listing's order is numeric. Create-if-absent, never replaced (the amendment's heads).
export function headPrefix(document: ApprovalDocument): string {
  return `docs/${documentKey(document)}/`;
}

export function headObjectName(document: ApprovalDocument, sequence: number): string {
  if (!Number.isInteger(sequence) || sequence < 1 || sequence > MAX_SEQUENCE) {
    throw new RangeError("A head's sequence is an integer from 1 to 999999999999");
  }
  return `${headPrefix(document)}${String(sequence).padStart(12, "0")}`;
}

// The bytes a head entry holds and a versioned Provenance's signature carries: the signed
// statement's canonical JSON.
export function signedStatementBytes(signed: SignedApprovalStatement): string {
  return canonicalJson(signed);
}

// The public key of an approval key version, from its PEM (and of a Word drawing key version,
// whose algorithm is the same). Anything but a 3072-bit RSA key is refused: a statement verified
// against another kind of key proves nothing about this one.
export function approvalPublicKey(pem: string): KeyObject {
  const key = createPublicKey(pem);
  if (
    key.asymmetricKeyType !== "rsa" ||
    key.asymmetricKeyDetails?.modulusLength !== APPROVAL_KEY_BITS
  ) {
    throw new Error("An approval key is a 3072-bit RSA key");
  }
  return key;
}

export type VerifiedStatement = {
  signed: SignedApprovalStatement;
  statement: ApprovalStatement;
  statementSha256: string;
  // Exactly the bytes that were verified, canonical.
  bytes: string;
};

// Reads a signed statement from its bytes, without checking the signature: what the caller needs
// to find the key. The bytes must be the canonical JSON of what they parse to, so one statement has
// one byte form wherever it is stored.
export function parseSignedStatement(bytes: string): SignedApprovalStatement | ApprovalRefusal {
  let value: unknown;
  try {
    value = JSON.parse(bytes);
  } catch {
    return "malformed";
  }
  const parsed = SignedApprovalStatementSchema.safeParse(value);
  if (!parsed.success) return "malformed";
  if (canonicalJson(parsed.data) !== bytes) return "not-canonical";
  return parsed.data;
}

// Whether `key` signed `bytes`: RSA-PSS (SHA-256, salt 32) over them, which is how Cloud KMS signs
// their SHA-256 digest, the signature in its one base64 spelling. A signed approval statement and a
// Word drawing record (src/certified-word/drawing.ts) are both verified here.
export function verifyPss(bytes: string, signatureBase64: string, key: KeyObject): boolean {
  const signature = Buffer.from(signatureBase64, "base64");
  if (signature.toString("base64") !== signatureBase64) return false;
  return verify(
    "sha256",
    Buffer.from(bytes, "utf8"),
    { key, padding: constants.RSA_PKCS1_PSS_PADDING, saltLength: PSS_SALT_LENGTH },
    signature,
  );
}

// Whether `key` signed the statement: over the canonical JSON of the statement.
export function verifySignature(signed: SignedApprovalStatement, key: KeyObject): boolean {
  return verifyPss(canonicalJson(signed.statement), signed.signatureBase64, key);
}

// Parses and verifies a signed statement against the key its own `signer.keyVersion` names, as the
// caller's trust list resolves it: a key version the caller does not trust is refused, whatever
// the signature says.
export function verifySignedStatement(
  bytes: string,
  trustedKey: (keyVersion: string) => KeyObject | undefined,
): VerifiedStatement | ApprovalRefusal {
  const signed = parseSignedStatement(bytes);
  if (typeof signed === "string") return signed;
  const key = trustedKey(signed.statement.signer.keyVersion);
  if (key === undefined) return "untrusted-key";
  if (!verifySignature(signed, key)) return "bad-signature";
  return {
    signed,
    statement: signed.statement,
    statementSha256: statementSha256(signed.statement),
    bytes,
  };
}

// What a reader requires of a statement beyond its signature. Each field given must match.
export type StatementExpectation = {
  environment: ApprovalEnvironment;
  document?: ApprovalDocument;
  submissionId?: string;
  approvedContentSha256?: string;
  mappingVersion?: string;
  documentBundleSha256?: string;
  sections?: readonly ApprovedSection[];
};

export function checkStatement(
  statement: ApprovalStatement,
  expected: StatementExpectation,
): ApprovalRefusal | undefined {
  if (statement.environment !== expected.environment) return "wrong-environment";
  if (expected.document !== undefined && sha256(statement.document) !== sha256(expected.document)) {
    return "other-document";
  }
  if (expected.submissionId !== undefined && statement.submissionId !== expected.submissionId) {
    return "other-submission";
  }
  if (
    expected.approvedContentSha256 !== undefined &&
    statement.approvedContentSha256 !== expected.approvedContentSha256
  ) {
    return "other-content";
  }
  if (
    expected.mappingVersion !== undefined &&
    statement.mappingVersion !== expected.mappingVersion
  ) {
    return "other-mapping";
  }
  if (
    expected.documentBundleSha256 !== undefined &&
    statement.documentBundleSha256 !== expected.documentBundleSha256
  ) {
    return "other-record";
  }
  if (expected.sections !== undefined && sha256(statement.sections) !== sha256(expected.sections)) {
    return "section-mismatch";
  }
  return undefined;
}

const HEAD_NAME = /^docs\/[0-9a-f]{64}\/([0-9]{12})$/;

// The head of a document from the names a listing of its prefix returned: the highest sequence.
// Every name must be a head entry of that document, or the listing is refused as a whole: a
// malformed entry is never stepped over to the one below it.
export function chooseHead(
  document: ApprovalDocument,
  names: readonly string[],
): { name: string; sequence: number } | "no-head" | "malformed-head" {
  const prefix = headPrefix(document);
  let best: { name: string; sequence: number } | undefined;
  for (const name of names) {
    const match = HEAD_NAME.exec(name);
    if (match === null || !name.startsWith(prefix)) return "malformed-head";
    const sequence = Number(match[1]);
    if (sequence < 1) return "malformed-head";
    if (best === undefined || sequence > best.sequence) best = { name, sequence };
  }
  return best ?? "no-head";
}

// A head entry's bytes, verified, and held to the name it was read from: its statement's
// document hashes to the prefix and its sequence is the name's.
export function checkHeadEntry(
  head: { name: string; sequence: number },
  document: ApprovalDocument,
  verified: VerifiedStatement | ApprovalRefusal,
): VerifiedStatement | ApprovalRefusal {
  if (typeof verified === "string") {
    return verified === "bad-signature" || verified === "untrusted-key"
      ? verified
      : "malformed-head";
  }
  if (head.name !== headObjectName(document, head.sequence)) return "malformed-head";
  if (documentKey(verified.statement.document) !== documentKey(document)) return "malformed-head";
  if (verified.statement.sequence !== head.sequence) return "malformed-head";
  return verified;
}

// --- the whole published record ---------------------------------------------------------------------

// The SHA-256 of an EMA document Bundle as published: its canonical JSON without the two values the
// store assigns on every write, `meta.versionId` and `meta.lastUpdated` (and `meta` itself when
// nothing else is left in it). The signer hashes the crosswalk's output so; the query service hashes
// the stored Bundle so and requires the statement's value. A store that changed anything else in
// the Bundle makes the version `not-approved`: a false refusal, never a false approval.
export function publishedDocumentSha256(bundle: unknown): string {
  const copy = structuredClone(bundle) as Record<string, unknown>;
  const meta = copy.meta;
  if (meta !== null && typeof meta === "object" && !Array.isArray(meta)) {
    const rest = { ...(meta as Record<string, unknown>) };
    delete rest.versionId;
    delete rest.lastUpdated;
    if (Object.keys(rest).length === 0) delete copy.meta;
    else copy.meta = rest;
  }
  return sha256(copy);
}

// --- the sections a statement names ---------------------------------------------------------------

// The id the transform stamps on the EMA section of a rule (src/fhir/transform.ts).
function sectionKeys(mapping: EmaMapping): { order: string[]; byId: Map<string, string> } {
  const order: string[] = [];
  const byId = new Map<string, string>();
  const walk = (rule: SectionRule): void => {
    order.push(rule.sourceKey);
    byId.set(stableUuid("ema-qrd-section", rule.sourceKey), rule.sourceKey);
    for (const child of rule.children ?? []) walk(child);
  };
  walk(mapping.root);
  return { order, byId };
}

// Every published section with narrative, by its key and the SHA-256 of its `text.div`, in the
// mapping's order: what a statement names and what the query service re-hashes (D9). Undefined
// when a section with narrative is not one the mapping names, or two sections carry one key: such a
// record cannot be described by a statement, so it is never approved or answered from.
export function publishedSections(
  sections: readonly CompositionSection[],
  mapping: EmaMapping,
): ApprovedSection[] | undefined {
  const { order, byId } = sectionKeys(mapping);
  const found = new Map<string, string>();
  const pending = [...sections];
  for (let section = pending.pop(); section !== undefined; section = pending.pop()) {
    pending.push(...(section.section ?? []));
    const div = section.text?.div;
    if (div === undefined) continue;
    const sourceKey = byId.get(section.id ?? "");
    if (sourceKey === undefined || found.has(sourceKey)) return undefined;
    found.set(sourceKey, sha256Utf8(div));
  }
  return order.flatMap((sourceKey) => {
    const narrativeDivSha256 = found.get(sourceKey);
    return narrativeDivSha256 === undefined ? [] : [{ sourceKey, narrativeDivSha256 }];
  });
}

// --- reading a head through injected I/O ------------------------------------------------------------

// Where heads and keys come from, injected so this file stays pure: Cloud Storage and Cloud KMS in
// the services (src/gcp/approvals.ts), maps in tests. `list` names every object under a prefix;
// `read` is an object's bytes, or undefined when it is absent. `key` resolves a key version the
// reader trusts, or undefined for any other.
export type HeadSource = {
  list(prefix: string): Promise<string[]>;
  read(name: string): Promise<string | undefined>;
};

export type KeySource = (keyVersion: string) => Promise<KeyObject | undefined>;

// A statement's bytes verified against the key its own `signer.keyVersion` names, as `keys`
// resolves it.
export async function verifyWithKeys(
  bytes: string,
  keys: KeySource,
): Promise<VerifiedStatement | ApprovalRefusal> {
  const signed = parseSignedStatement(bytes);
  if (typeof signed === "string") return signed;
  const key = await keys(signed.statement.signer.keyVersion);
  return verifySignedStatement(bytes, (keyVersion) =>
    keyVersion === signed.statement.signer.keyVersion ? key : undefined,
  );
}

// The document's head: listed, the highest entry read, verified and held to its name. `no-head`
// when the document has none; any other refusal when what is there cannot be trusted, which a
// reader never steps over to the entry below (the amendment's "Reading a head").
export async function readHead(
  source: HeadSource,
  keys: KeySource,
  document: ApprovalDocument,
): Promise<VerifiedStatement | ApprovalRefusal> {
  const chosen = chooseHead(document, await source.list(headPrefix(document)));
  if (typeof chosen === "string") return chosen;
  const bytes = await source.read(chosen.name);
  if (bytes === undefined) return "malformed-head";
  return checkHeadEntry(chosen, document, await verifyWithKeys(bytes, keys));
}
