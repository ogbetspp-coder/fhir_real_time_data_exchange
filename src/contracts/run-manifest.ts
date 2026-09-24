import { z } from "zod";

import {
  Count,
  HttpUrl,
  IsoDateTime,
  NonEmptyString,
  NormalizationVersion,
  PrincipalId,
  RecordRef,
  Sha256Hex,
  Token,
  Uuid,
} from "./common.js";
import { CANONICAL_SUBMISSION_VERSION, GraphType } from "./canonical-submission.js";
import {
  ApprovalMeaning,
  ApprovalSchema,
  AttestationMethod,
  ApproverRole,
  FidelityStatus,
} from "./ingestion-provenance.js";

// Evidence schema for the signed run manifest (AGENTS.md: "update the mapping manifest and
// evidence schema together"). Versions 1.0.0 and 1.1.0 are kept so ledger rows written before
// the ingestion block, and before authority imports, remain readable through
// `AnyRunManifestSchema`.

export const RUN_MANIFEST_VERSION = "2.0.0";

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

const IngestionFidelitySchema = z.strictObject({
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
});

// The ingestion block of a 1.1.0 manifest, frozen so those ledger rows stay readable.
export const IngestionEvidenceV1Schema = z
  .strictObject({
    submissionId: Uuid,
    contractVersion: z.literal("1.0.0"),
    sourceDocumentSha256: Sha256Hex,
    extractionRunId: Uuid,
    parser: Token,
    modelId: Token.optional(),
    promptTemplateVersion: Token.optional(),
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
      approverId: PrincipalId,
      approverRole: ApproverRole,
      approvedAt: IsoDateTime,
      method: AttestationMethod,
      meaning: ApprovalMeaning,
      approvedContentSha256: Sha256Hex,
      recordRef: RecordRef.optional(),
    }),
    provenanceResourceId: Uuid,
  })
  .meta({ id: "IngestionEvidenceV1" });

// What Zone B fetched from the authority for an import (docs/design/authority-import-contract.md,
// D1, D12).
export const AuthorityFetchSchema = z
  .strictObject({
    importerVersion: Token,
    fetched: z
      .array(
        z.strictObject({
          url: HttpUrl,
          sha256: Sha256Hex,
          byteLength: Count,
          fetchedAt: IsoDateTime,
          evidenceUri: NonEmptyString.optional(),
        }),
      )
      .min(2),
  })
  .meta({ id: "AuthorityFetch" });

export const IngestionEvidenceSchema = z
  .strictObject({
    submissionId: Uuid,
    contractVersion: z.literal(CANONICAL_SUBMISSION_VERSION),
    sourceKind: z.enum(["drawn", "authority-publication"]),
    graphType: GraphType,
    // Whether the deployment accepted synthetic content when this run passed its gate (D7).
    allowSyntheticSources: z.boolean(),
    sourceDocumentSha256: Sha256Hex,
    extractionRunId: Uuid,
    parser: Token,
    modelId: Token.optional(),
    promptTemplateVersion: Token.optional(),
    fidelity: IngestionFidelitySchema,
    approval: ApprovalSchema,
    authority: AuthorityFetchSchema.optional(),
    provenanceResourceId: Uuid,
  })
  .superRefine((evidence, context) => {
    if ((evidence.sourceKind === "authority-publication") !== (evidence.authority !== undefined)) {
      context.addIssue({
        code: "custom",
        message: "an authority import, and only one, records what Zone B fetched",
      });
    }
  })
  .meta({
    id: "IngestionEvidence",
    description: "Hashes, counts, enumerations, and identifiers only; never narrative.",
  });

const runSource = z.strictObject({
  kind: z.enum(["fixture", "healthcare-api", "document"]),
  resource: NonEmptyString,
  hash: Sha256Hex,
});

function documentRunsCarryIngestion(
  manifest: { source: { kind: string }; ingestion?: unknown },
  context: z.RefinementCtx,
): void {
  if (manifest.source.kind === "document" && manifest.ingestion === undefined) {
    context.addIssue({ code: "custom", message: "document runs require an ingestion block" });
  }
  if (manifest.source.kind !== "document" && manifest.ingestion !== undefined) {
    context.addIssue({ code: "custom", message: "only document runs carry an ingestion block" });
  }
}

export const RunManifestV11Schema = z
  .strictObject({
    schemaVersion: z.literal("1.1.0"),
    source: runSource,
    ...manifestBody,
    ingestion: IngestionEvidenceV1Schema.optional(),
  })
  .superRefine(documentRunsCarryIngestion)
  .meta({ id: "RunManifestV11" });

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
  .superRefine(documentRunsCarryIngestion)
  .meta({ id: "RunManifest" });

export const AnyRunManifestSchema = z.discriminatedUnion("schemaVersion", [
  RunManifestV1Schema,
  RunManifestV11Schema,
  RunManifestSchema,
]);

export type RunManifestV1 = z.infer<typeof RunManifestV1Schema>;
export type RunManifest = z.infer<typeof RunManifestSchema>;
export type IngestionEvidence = z.infer<typeof IngestionEvidenceSchema>;
