import { z } from "zod";

import {
  AddressableFhirId,
  CanonicalUri,
  Count,
  ImageDigest,
  IsoDateTime,
  NormalizationVersion,
  PositiveInt,
  PrincipalId,
  Sha256Hex,
  SourceKey,
  Uuid,
} from "./common.js";
import { ApproverRole, MediaType } from "./ingestion-provenance.js";

// The approval a named person makes, and the review they were shown when they made it
// (docs/design/approval.md, D3 and D6). Both are new contracts, 1.0.0, published beside the
// others so a third party can check an approval without trusting this repository's code.
//
// approval-statement 1.0.0 is the statement the signer signs and its envelope: the canonical JSON
// (RFC 8785, src/lib/hash.ts) of `statement`, signed with RSA-PSS over its SHA-256 (salt 32) by the
// environment's `approval-signing-hsm` key version that `statement.signer.keyVersion` names. Phase 1
// builds the `approve` kind only (docs/design/approval.md, "Two phases"); `reject`, `withdraw` and
// an authority import's `request` are added by the change that builds them, as a major (ADR 0002:
// an enum the gate branches on). The design's amendment of 2026-10-06 records why `request` is not
// in 1.0.0.
//
// review-record 1.0.0 is what the approver is shown: the record the signer builds from the
// submission and the document's head, which the signer renders into the one HTML file the approver
// opens (src/approval/review.ts). The statement's `reviewSha256` is the SHA-256 of that file's
// bytes, so it covers exactly what was shown.

export const APPROVAL_STATEMENT_VERSION = "1.0.0";
export const REVIEW_RECORD_VERSION = "1.0.0";

// The version a statement says it is written under, inside the signed bytes.
export const STATEMENT_VERSION = "approval-statement/1";
export const REVIEW_VERSION = "review/1";

export const ApprovalEnvironment = z
  .enum(["dev", "validation", "prod"])
  .meta({ id: "ApprovalEnvironment" });

export const ApprovalKind = z.enum(["approve"]).meta({ id: "ApprovalKind" });

// What the signature means (D1). The code is what the statement carries; the sentence is what the
// review shows and the query service states.
export const ApprovalStatementMeaning = z
  .enum(["record-represents-approved-label"])
  .meta({ id: "ApprovalStatementMeaning" });

export const APPROVAL_MEANING_TEXT =
  "I reviewed this structured record against the approved source label and attest that its narrative and structure represent that label.";

// The mapping the record is published under, as lineage names it (src/fhir/mapping.ts,
// mappingReference): `cap-smpc-en#1.3.0`.
const MappingVersion = z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*#[0-9]+\.[0-9]+\.[0-9]+$/);

// A Cloud KMS crypto key version, as the statement names the one that signed it.
const KmsKeyVersion = z
  .string()
  .regex(
    /^projects\/[a-z0-9-]{1,63}\/locations\/[a-z0-9-]{1,63}\/keyRings\/[A-Za-z0-9_-]{1,63}\/cryptoKeys\/[A-Za-z0-9_-]{1,63}\/cryptoKeyVersions\/[0-9]{1,9}$/,
  );

// The heads are named by a twelve-digit sequence (docs/design/approval.md, the amendment's heads).
export const MAX_SEQUENCE = 999_999_999_999;

// A person's verified e-mail address, for people to read: never an id (D2).
const EmailAddress = z
  .string()
  .max(254)
  .regex(/^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/);

// A person's display name, for people to read: bounded, no markup, no line break.
const DisplayName = z
  .string()
  .min(1)
  .max(200)
  .regex(/^[^<>\n\r\t]+$/);

const BundleIdentifierValue = z.string().regex(/^[A-Za-z0-9._:-]{1,128}$/);

// Which document a statement approves: the canonical Bundle.identifier the submission carries, the
// EMA document Bundle id the run persists under (stableUuid of the identifier value), and the
// language it is published in. Content hashes, never a store's versionId (D4).
export const ApprovalDocumentSchema = z
  .strictObject({
    identifier: z.strictObject({
      system: CanonicalUri.optional(),
      value: BundleIdentifierValue,
    }),
    emaBundleId: AddressableFhirId,
    language: z.string().regex(/^[a-z]{2,3}(?:-[A-Z]{2})?$/),
  })
  .meta({ id: "ApprovalDocument" });

// One published section with narrative: the key the mapping gives it and the SHA-256 of its
// `text.div`, exactly as the query service re-hashes it on every answer (D9).
export const ApprovedSectionSchema = z
  .strictObject({ sourceKey: SourceKey, narrativeDivSha256: Sha256Hex })
  .meta({ id: "ApprovedSection" });

export const ApprovalStatementSchema = z
  .strictObject({
    statementVersion: z.literal(STATEMENT_VERSION),
    kind: ApprovalKind,
    // One environment's approval never publishes in another (D4).
    environment: ApprovalEnvironment,
    document: ApprovalDocumentSchema,
    // The signer's order for this document, 1, 2, 3 …, never a clock's (D8).
    sequence: PositiveInt.max(MAX_SEQUENCE),
    // The document's head before this one (the SHA-256 of its statement), or null for the first.
    previousStatementSha256: Sha256Hex.nullable(),
    submissionId: Uuid,
    // As the ingress gate recomputes it: { schemaVersion, graphType, bundle, provenance }.
    approvedContentSha256: Sha256Hex,
    mappingVersion: MappingVersion,
    // Every published section with narrative, in the mapping's order.
    sections: z.array(ApprovedSectionSchema).min(1).max(200),
    // SHA-256 of the review file's bytes, exactly as the approver was shown it (D6).
    reviewSha256: Sha256Hex,
    approver: z.strictObject({
      // The Google subject of the verified identity token: the approver's id (D2).
      sub: PrincipalId,
      role: ApproverRole,
      // SHA-256 of the canonical JSON of the approver map the role was read from.
      approverMapSha256: Sha256Hex,
    }),
    // From the same verified token, for people; never an id (D2).
    manifestation: z.strictObject({ name: DisplayName, email: EmailAddress }),
    meaning: ApprovalStatementMeaning,
    // The signer's clock.
    signedAt: IsoDateTime,
    signer: z.strictObject({ imageDigest: ImageDigest, keyVersion: KmsKeyVersion }),
  })
  .meta({
    id: "ApprovalStatement",
    description:
      "What a named person approved: the document, the exact content and sections by hash, the review they were shown by hash, who they are as Google asserted it, and the meaning of the signature. Signed by the approval service, not by the person.",
  });

export const SignedApprovalStatementSchema = z
  .strictObject({
    statement: ApprovalStatementSchema,
    // RSA-PSS (SHA-256, salt 32) over the SHA-256 of the canonical JSON of `statement`, by the key
    // version `statement.signer.keyVersion` names. Standard base64.
    signatureBase64: z
      .string()
      .min(4)
      .max(2_048)
      .regex(/^[A-Za-z0-9+/]+={0,2}$/),
  })
  .meta({
    id: "SignedApprovalStatement",
    description:
      "An ApprovalStatement and the approval service's signature over its canonical JSON. This exact object, as canonical JSON, is a document's head entry and the data of its versioned Provenance's signature.",
  });

// --- the review ------------------------------------------------------------------------------

export const ReviewSectionChange = z
  .enum(["added", "changed", "unchanged"])
  .meta({ id: "ReviewSectionChange" });

export const ReviewRecordSchema = z
  .strictObject({
    reviewVersion: z.literal(REVIEW_VERSION),
    environment: ApprovalEnvironment,
    submissionId: Uuid,
    approvedContentSha256: Sha256Hex,
    document: ApprovalDocumentSchema,
    mappingVersion: MappingVersion,
    // The source document the record was drawn from, by hash.
    source: z.strictObject({
      sha256: Sha256Hex,
      mediaType: MediaType,
    }),
    // The fidelity check's result, as the gate re-executed it.
    fidelity: z.strictObject({
      status: z.literal("passed"),
      reportSha256: Sha256Hex,
      normalizationVersion: NormalizationVersion,
      sectionsChecked: Count,
      sectionsMatched: Count,
    }),
    meaning: ApprovalStatementMeaning,
    // The document's current head approval the changes are shown against, or null for a first.
    baseline: z
      .strictObject({
        sequence: PositiveInt.max(MAX_SEQUENCE),
        statementSha256: Sha256Hex,
        submissionId: Uuid,
      })
      .nullable(),
    // Every published section with narrative, in the mapping's order, with its heading and its
    // narrative exactly as it will be published, and whether it differs from the baseline's.
    sections: z
      .array(
        z.strictObject({
          sourceKey: SourceKey,
          title: z.string().min(1).max(300),
          div: z
            .string()
            .min(1)
            .max(8 * 1_024 * 1_024),
          narrativeDivSha256: Sha256Hex,
          change: ReviewSectionChange,
        }),
      )
      .min(1)
      .max(200),
    // Sections the baseline approved that this record no longer carries.
    removed: z.array(ApprovedSectionSchema).max(200),
  })
  .meta({
    id: "ReviewRecord",
    description:
      "What an approver is shown before they approve: the document, the source and fidelity result by hash, and every published section verbatim with what changed since the current approval. Rendered once into the review file whose bytes the statement's reviewSha256 covers.",
  });

export type ApprovalEnvironment = z.infer<typeof ApprovalEnvironment>;
export type ApprovalDocument = z.infer<typeof ApprovalDocumentSchema>;
export type ApprovedSection = z.infer<typeof ApprovedSectionSchema>;
export type ApprovalStatement = z.infer<typeof ApprovalStatementSchema>;
export type SignedApprovalStatement = z.infer<typeof SignedApprovalStatementSchema>;
export type ReviewRecord = z.infer<typeof ReviewRecordSchema>;
