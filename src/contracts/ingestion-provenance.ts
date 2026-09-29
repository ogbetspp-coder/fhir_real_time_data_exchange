import { z } from "zod";

import {
  Count,
  HttpUrl,
  IsoDateTime,
  NormalizationVersion,
  PositiveInt,
  PrincipalId,
  RecordRef,
  Sha256Hex,
  SourceKey,
  StorageUri,
  TargetPath,
  Token,
  Uuid,
} from "./common.js";

// Everything in this file is hashes, counts, enumerations, identifiers, and offsets. No field
// may carry narrative or other regulated text: these records travel into manifests, the
// BigQuery ledger, and FHIR Provenance (ADR 0002, UR-16).

// The published version of `IngestionProvenance` on its own (contracts/generated/index.json).
// 2.0.0 (audit C-6): the provenance of `CanonicalSubmission` 2.0.0, whose `sourceDocument` is a
// union on a required `kind`. That change, and two tightenings before it, were published under
// 1.0.0's `$id`; `contracts/versions.lock.json` now refuses a changed schema under a version it
// has recorded.
export const INGESTION_PROVENANCE_VERSION = "2.0.0";

export const MediaType = z
  .enum([
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ])
  .meta({ id: "MediaType" });

export const SourceSystemRefSchema = z
  .strictObject({
    name: Token,
    documentId: Token,
    versionId: Token.optional(),
  })
  .meta({
    id: "SourceSystemRef",
    description: "Identity of the document in its system of record.",
  });

export const ExtractedTextRefSchema = z
  .strictObject({
    uri: StorageUri,
    sha256: Sha256Hex,
    extractorVersion: Token,
  })
  .meta({
    id: "ExtractedTextRef",
    description:
      "Reference to the extractor's page text (a SourceDocumentText object). It contains narrative and is never inlined.",
  });

// A document a reader draws (PDF, Word). No extractor of one is qualified under the current
// normalisation version (`NORMALIZATION_VERSION`, docs/fidelity-normalization.md §7): only a
// synthetic extractor may produce one, and only where the deployment accepts synthetic sources.
export const DrawnSourceDocumentSchema = z
  .strictObject({
    kind: z.literal("drawn"),
    sha256: Sha256Hex,
    byteLength: PositiveInt,
    mediaType: MediaType,
    filename: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._ -]{0,254}$/),
    pageCount: PositiveInt.optional(),
    sourceSystem: SourceSystemRefSchema.optional(),
    storageUri: StorageUri.optional(),
    // Required: Zone B always re-executes the fidelity check against this text (ADR 0003).
    extractedText: ExtractedTextRefSchema,
  })
  .meta({ id: "DrawnSourceDocument" });

// The authorities whose publications may be imported, and the synthetic one the tests and the
// demo use (docs/design/authority-import-contract.md, D7).
export const Authority = z.enum(["EMA", "synthetic"]).meta({ id: "Authority" });

// The authority's own ids for a document and its List, as its API serves them.
export const AuthorityId = z
  .string()
  .regex(/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/)
  .meta({ id: "AuthorityId", description: "A lower-case GUID." });

// A section of the authority's Composition, by its position in the section tree.
export const SectionPath = z
  .string()
  .regex(/^Composition(?:\.section\[[0-9]{1,4}\]){1,16}$/)
  .meta({ id: "SectionPath" });

// A reference to a picture the document names outside its own bytes, in the grammars the
// importer knows (docs/design/authority-import-contract.md, D6).
export const PictureReference = z
  .string()
  .regex(/^~\/_entity\/annotation\/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/)
  .meta({ id: "PictureReference" });

export const PinnedFileSchema = z
  .strictObject({ id: AuthorityId, sha256: Sha256Hex, byteLength: PositiveInt })
  .meta({ id: "PinnedFile", description: "Bytes as served, after HTTP content decoding." });

export const ImportRequestSchema = z
  .strictObject({
    authority: Authority,
    documentId: AuthorityId,
    indexId: AuthorityId,
    language: z.literal("en"),
  })
  .meta({
    id: "ImportRequest",
    description:
      "What a person asked to import: an input to the import, part of the approved content.",
  });

export const PictureSchema = z
  .discriminatedUnion("kind", [
    z.strictObject({
      kind: z.literal("fetched"),
      reference: PictureReference,
      url: HttpUrl,
      sha256: Sha256Hex,
      byteLength: PositiveInt,
    }),
    z.strictObject({ kind: z.literal("not-drawn"), reference: PictureReference, evidence: Token }),
  ])
  .meta({ id: "Picture" });

export const AuthoritySourceDocumentSchema = z
  .strictObject({
    kind: z.literal("authority-publication"),
    mediaType: z.literal("application/fhir+json"),
    authority: Authority,
    request: ImportRequestSchema,
    document: PinnedFileSchema,
    index: z.strictObject({
      id: AuthorityId,
      sha256: Sha256Hex,
      byteLength: PositiveInt,
      epiId: Token,
      versionNumber: Token,
      metaVersionId: Token,
      status: z.literal("current"),
    }),
    pictures: z.array(PictureSchema).max(500),
    sectionPages: z
      .array(z.strictObject({ page: PositiveInt, path: SectionPath, code: Token }))
      .min(1)
      .max(2_000),
    extractedText: ExtractedTextRefSchema,
  })
  .meta({
    id: "AuthoritySourceDocument",
    description:
      "An authority's published ePI, which Zone B fetches itself and re-imports (docs/design/authority-import-contract.md).",
  });

export const SourceDocumentSchema = z
  .discriminatedUnion("kind", [DrawnSourceDocumentSchema, AuthoritySourceDocumentSchema])
  .meta({ id: "SourceDocument" });

export const ToolVersionSchema = z
  .strictObject({ name: Token, version: Token })
  .meta({ id: "ToolVersion" });

export const ModelRefSchema = z
  .strictObject({ provider: Token, id: Token })
  .meta({ id: "ModelRef" });

export const PromptTemplateRefSchema = z
  .strictObject({ id: Token, version: Token, sha256: Sha256Hex })
  .meta({ id: "PromptTemplateRef" });

export const TerminologyServiceRefSchema = z
  .strictObject({ name: Token, version: Token, snapshotSha256: Sha256Hex })
  .meta({ id: "TerminologyServiceRef" });

export const ExtractionToolingSchema = z
  .strictObject({
    extractionRunId: Uuid,
    serviceVersion: Token,
    parser: ToolVersionSchema,
    model: ModelRefSchema.optional(),
    promptTemplate: PromptTemplateRefSchema.optional(),
    terminologyService: TerminologyServiceRefSchema.optional(),
  })
  .meta({ id: "ExtractionTooling" });

export const SourceSpanSchema = z
  .strictObject({
    page: PositiveInt,
    startOffset: Count,
    endOffset: PositiveInt,
    textSha256: Sha256Hex,
  })
  .refine((span) => span.startOffset < span.endOffset, {
    message: "startOffset must be less than endOffset",
    path: ["endOffset"],
  })
  .meta({
    id: "SourceSpan",
    description:
      "Half-open code-point range [startOffset, endOffset) on a page and the SHA-256 of the raw slice's UTF-8 bytes.",
  });

export const SectionProvenanceSchema = z
  .strictObject({
    sourceKey: SourceKey,
    spans: z.array(SourceSpanSchema).min(1),
    narrativeDivSha256: Sha256Hex,
    normalizedTextSha256: Sha256Hex,
  })
  .meta({ id: "SectionProvenance" });

export const DecisionAction = z
  .enum(["extracted-verbatim", "code-mapped", "defaulted-by-rule", "human-edited", "rejected"])
  .meta({ id: "DecisionAction" });

export const DecisionReason = z
  .enum([
    "boundary-correction",
    "code-correction",
    "metadata-correction",
    "not-applicable",
    "unreadable-source",
    "out-of-scope",
    "duplicate",
  ])
  .meta({ id: "DecisionReason" });

export const TerminologyRefSchema = z
  .strictObject({
    system: HttpUrl,
    code: Token,
    version: Token.optional(),
    lookupId: Token,
  })
  .meta({
    id: "TerminologyRef",
    description: "Receipt of the terminology lookup that produced a code.",
  });

// The field of the authority's document or List a value was read from.
export const SourcePath = z
  .string()
  .regex(
    /^(?:List|Bundle|Composition)(?:\.[A-Za-z][A-Za-z0-9]*(?:\[(?:[0-9]{1,4}|[A-Za-z][A-Za-z0-9]*)\])?)*$/,
  )
  .max(256)
  .meta({ id: "SourcePath" });

export const StructuringDecisionSchema = z
  .strictObject({
    target: TargetPath,
    sourceKey: SourceKey.optional(),
    sourceField: SourcePath.optional(),
    action: DecisionAction,
    ruleId: Token.optional(),
    terminologyRef: TerminologyRefSchema.optional(),
    editorId: Token.optional(),
    reason: DecisionReason.optional(),
  })
  .meta({ id: "StructuringDecision" });

export const FidelityStatus = z.enum(["passed", "failed"]).meta({ id: "FidelityStatus" });

export const FidelitySummarySchema = z
  .strictObject({
    normalizationVersion: NormalizationVersion,
    status: FidelityStatus,
    sectionsChecked: Count,
    sectionsMatched: Count,
    narrativeBindingSha256: Sha256Hex,
    reportSha256: Sha256Hex,
    reportUri: StorageUri.optional(),
  })
  .meta({ id: "FidelitySummary" });

export const IngestionProvenanceSchema = z
  .strictObject({
    sourceDocument: SourceDocumentSchema,
    extraction: ExtractionToolingSchema,
    sections: z.array(SectionProvenanceSchema).min(1),
    decisions: z.array(StructuringDecisionSchema).min(1),
    fidelity: FidelitySummarySchema,
  })
  .meta({ id: "IngestionProvenance" });

export const ApproverRole = z
  .enum(["content-reviewer", "qa-reviewer"])
  .meta({ id: "ApproverRole" });

export const AttestationMethod = z.enum(["api-attestation", "manual-record"]).meta({
  id: "AttestationMethod",
  description: "Attestation placeholders. Electronic signature is a future control boundary.",
});

export const ApprovalMeaning = z
  .enum(["reviewed-fidelity-and-structure"])
  .meta({ id: "ApprovalMeaning" });

export const AttestedApprovalSchema = z
  .strictObject({
    approverId: PrincipalId,
    approverRole: ApproverRole,
    approvedAt: IsoDateTime,
    method: AttestationMethod,
    meaning: ApprovalMeaning,
    approvedContentSha256: Sha256Hex,
    recordRef: RecordRef.optional(),
  })
  .meta({ id: "AttestedApproval" });

// The EMA says of its ePI service that its documents are "for pilot purposes only".
export const AuthorityStatus = z.enum(["pilot"]).meta({ id: "AuthorityStatus" });

export const AuthorityApprovalSchema = z
  .strictObject({
    method: z.literal("authority-publication"),
    meaning: z.literal("authority-publication-imported"),
    authority: Authority,
    authorityStatus: AuthorityStatus,
    publication: z.strictObject({
      epiId: Token,
      documentId: AuthorityId,
      indexId: AuthorityId,
      versionNumber: Token,
      procedureNumber: Token,
      // When the authority assembled the document, as it wrote it; not a publication date.
      authorityTimestamp: IsoDateTime,
    }),
    // Who asked for the import: a placeholder, like approverId, until roadmap item 2.
    requestedBy: PrincipalId,
    requestedAt: IsoDateTime,
    approvedContentSha256: Sha256Hex,
  })
  .meta({
    id: "AuthorityApproval",
    description:
      "The approval is the authority's publication; the person named requested the import (docs/design/authority-import-contract.md, D2, D8).",
  });

export const ApprovalSchema = z
  .discriminatedUnion("method", [AttestedApprovalSchema, AuthorityApprovalSchema])
  .meta({ id: "Approval" });

export type SourceDocument = z.infer<typeof SourceDocumentSchema>;
export type DrawnSourceDocument = z.infer<typeof DrawnSourceDocumentSchema>;
export type AuthoritySourceDocument = z.infer<typeof AuthoritySourceDocumentSchema>;
export type ImportRequest = z.infer<typeof ImportRequestSchema>;
export type AttestedApproval = z.infer<typeof AttestedApprovalSchema>;
export type AuthorityApproval = z.infer<typeof AuthorityApprovalSchema>;
export type ExtractionTooling = z.infer<typeof ExtractionToolingSchema>;
export type SourceSpan = z.infer<typeof SourceSpanSchema>;
export type SectionProvenance = z.infer<typeof SectionProvenanceSchema>;
export type StructuringDecision = z.infer<typeof StructuringDecisionSchema>;
export type FidelitySummary = z.infer<typeof FidelitySummarySchema>;
export type IngestionProvenance = z.infer<typeof IngestionProvenanceSchema>;
export type Approval = z.infer<typeof ApprovalSchema>;
