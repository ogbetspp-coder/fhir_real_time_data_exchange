import { z } from "zod";

import {
  Count,
  HttpUrl,
  IsoDateTime,
  NonEmptyString,
  NormalizationVersion,
  Sha256Hex,
  Uuid,
} from "./common.js";
import {
  ApprovalMeaning,
  ApprovalMethod,
  ApproverRole,
  FidelityStatus,
} from "./ingestion-provenance.js";

// Evidence schema for the signed run manifest (AGENTS.md: "update the mapping manifest and
// evidence schema together"). Version 1.0.0 is kept so ledger rows written before the
// ingestion block existed remain readable through `AnyRunManifestSchema`.

export const RUN_MANIFEST_VERSION = "1.1.0";

export const RunStatus = z
  .enum(["validated", "persisted", "rejected", "failed"])
  .meta({ id: "RunStatus" });

const StandardsSchema = z
  .strictObject({
    fhir: z.literal("5.0.0"),
    globalEpiPackage: NonEmptyString,
    emaPackage: z.literal("EUePI#1.0.0"),
    qrdTemplate: NonEmptyString,
    mappingVersion: NonEmptyString,
  })
  .meta({ id: "ManifestStandards" });

const ValidationSchema = z
  .strictObject({
    preflightErrors: Count,
    officialValidationExecuted: z.boolean(),
    officialProfileErrors: Count,
    cloudValidationExecuted: z.boolean(),
    cloudProfileErrors: Count,
    profiles: z.array(HttpUrl),
  })
  .meta({ id: "ManifestValidation" });

const TransformationSchema = z
  .strictObject({ inputHash: Sha256Hex, outputHash: Sha256Hex, decisions: Count })
  .meta({ id: "ManifestTransformation" });

const PersistenceSchema = z
  .strictObject({ targetStore: NonEmptyString, transactionResponseHash: Sha256Hex })
  .meta({ id: "ManifestPersistence" });

const RuntimeSchema = z
  .strictObject({
    sourceCommit: NonEmptyString,
    imageDigest: NonEmptyString,
    workflowRevision: NonEmptyString,
  })
  .meta({ id: "ManifestRuntime" });

const manifestBody = {
  runId: Uuid,
  startedAt: IsoDateTime,
  completedAt: IsoDateTime,
  status: RunStatus,
  dryRun: z.boolean(),
  standards: StandardsSchema,
  validation: ValidationSchema,
  transformation: TransformationSchema,
  persistence: PersistenceSchema.optional(),
  runtime: RuntimeSchema,
};

export const RunManifestV1Schema = z
  .strictObject({
    schemaVersion: z.literal("1.0.0"),
    source: z.strictObject({
      kind: z.enum(["fixture", "healthcare-api"]),
      resource: NonEmptyString,
      hash: Sha256Hex,
    }),
    ...manifestBody,
  })
  .meta({ id: "RunManifestV1" });

export const IngestionEvidenceSchema = z
  .strictObject({
    submissionId: Uuid,
    contractVersion: z.literal("1.0.0"),
    sourceDocumentSha256: Sha256Hex,
    extractionRunId: Uuid,
    parser: NonEmptyString,
    modelId: NonEmptyString.optional(),
    promptTemplateVersion: NonEmptyString.optional(),
    fidelity: z.strictObject({
      status: FidelityStatus.extract(["passed"]),
      normalizationVersion: NormalizationVersion,
      sectionsChecked: Count,
      sectionsMatched: Count,
      reportSha256: Sha256Hex,
      narrativeBindingSha256: Sha256Hex,
      coverage: z.strictObject({
        pageCodePoints: Count,
        bodyCodePoints: Count,
        coveredCodePoints: Count,
        uncoveredGaps: Count,
      }),
    }),
    approval: z.strictObject({
      approverId: NonEmptyString,
      approverRole: ApproverRole,
      approvedAt: IsoDateTime,
      method: ApprovalMethod,
      meaning: ApprovalMeaning,
      approvedContentSha256: Sha256Hex,
      recordRef: NonEmptyString.optional(),
    }),
    provenanceResourceId: Uuid,
  })
  .meta({
    id: "IngestionEvidence",
    description: "Hashes, counts, enumerations, and identifiers only; never narrative.",
  });

export const RunManifestSchema = z
  .strictObject({
    schemaVersion: z.literal(RUN_MANIFEST_VERSION),
    source: z.strictObject({
      kind: z.enum(["fixture", "healthcare-api", "document"]),
      resource: NonEmptyString,
      hash: Sha256Hex,
    }),
    ...manifestBody,
    ingestion: IngestionEvidenceSchema.optional(),
  })
  .superRefine((manifest, context) => {
    if (manifest.source.kind === "document" && manifest.ingestion === undefined) {
      context.addIssue({ code: "custom", message: "document runs require an ingestion block" });
    }
    if (manifest.source.kind !== "document" && manifest.ingestion !== undefined) {
      context.addIssue({ code: "custom", message: "only document runs carry an ingestion block" });
    }
  })
  .meta({ id: "RunManifest" });

export const AnyRunManifestSchema = z.discriminatedUnion("schemaVersion", [
  RunManifestV1Schema,
  RunManifestSchema,
]);

export type RunManifestV1 = z.infer<typeof RunManifestV1Schema>;
export type RunManifest = z.infer<typeof RunManifestSchema>;
export type IngestionEvidence = z.infer<typeof IngestionEvidenceSchema>;
