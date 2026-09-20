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

export const SourceDocumentSchema = z
  .strictObject({
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

export const StructuringDecisionSchema = z
  .strictObject({
    target: TargetPath,
    sourceKey: SourceKey.optional(),
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

export const ApprovalMethod = z.enum(["api-attestation", "manual-record"]).meta({
  id: "ApprovalMethod",
  description: "Attestation placeholders. Electronic signature is a future control boundary.",
});

export const ApprovalMeaning = z
  .enum(["reviewed-fidelity-and-structure"])
  .meta({ id: "ApprovalMeaning" });

export const ApprovalSchema = z
  .strictObject({
    approverId: PrincipalId,
    approverRole: ApproverRole,
    approvedAt: IsoDateTime,
    method: ApprovalMethod,
    meaning: ApprovalMeaning,
    approvedContentSha256: Sha256Hex,
    recordRef: RecordRef.optional(),
  })
  .meta({ id: "Approval" });

export type SourceDocument = z.infer<typeof SourceDocumentSchema>;
export type ExtractionTooling = z.infer<typeof ExtractionToolingSchema>;
export type SourceSpan = z.infer<typeof SourceSpanSchema>;
export type SectionProvenance = z.infer<typeof SectionProvenanceSchema>;
export type StructuringDecision = z.infer<typeof StructuringDecisionSchema>;
export type FidelitySummary = z.infer<typeof FidelitySummarySchema>;
export type IngestionProvenance = z.infer<typeof IngestionProvenanceSchema>;
export type Approval = z.infer<typeof ApprovalSchema>;
