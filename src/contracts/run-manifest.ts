import { z } from "zod";

import {
  Count,
  HttpUrl,
  IsoDateTime,
  NonEmptyString,
  NormalizationVersion,
  PackageRef,
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
// evidence schema together"). Versions 1.0.0, 1.1.0, 2.0.0 and 3.0.0 are kept so ledger rows
// written before the ingestion block, before authority imports, before the manifest was signed
// ahead of persistence, and before it named the packages that validated the run remain readable
// through `AnyRunManifestSchema`.
//
// 3.0.0 is signed BEFORE the FHIR transaction, so a persist-mode manifest cannot say the run
// persisted: its status is `authorised`, and it names the exact transaction it authorises
// (`persistence.transactionSha256`, the SHA-256 of the transaction Bundle sent, kept as the run's
// `persist-transaction` evidence object). Only the ledger row, written after the transaction
// succeeded, says `persisted`; the response's hash and the Bundle version it wrote are in the
// run's `commit` evidence object and the ledger row. A signed manifest with no ledger row is a
// run that did not commit (the transaction never ran, or was refused, atomically) or one that
// committed but went unrecorded (the transaction succeeded and the commit object or the ledger
// write then failed, after bounded retries). The two are told apart by reading the target store:
// the second left a Bundle version whose transaction hashes to `transactionSha256`.
//
// The two statuses are two shapes, a discriminated union on `status`, so the published JSON
// Schema (and every model generated from it) enforces that an authorised run, and only one,
// names its transaction and is not a dry run.

// 4.0.0 (audit B07, S-4) names the standards that validated the run instead of literals: every
// FHIR package of fhir/standards.lock.json by id#version and SHA-256 (`standards.packages`, the
// Global ePI and EMA packages among them), and the validator sidecar's image digest
// (`runtime.validatorImageDigest`). Up to 3.0.0 a manifest named the packages by literal, with
// no hash, so a signed manifest could name a standard other than the one that ran.

export const RUN_MANIFEST_VERSION = "4.0.0";

// `rejected` and `failed` were never written: a refused or failed run leaves no manifest, only
// its log line. They are kept for the older versions and dropped from 3.0.0, and `persisted`
// moved to the ledger row.
const LegacyRunStatus = z
  .enum(["validated", "persisted", "rejected", "failed"])
  .meta({ id: "LegacyRunStatus" });

const legacyStandards = {
  fhir: z.literal("5.0.0"),
  globalEpiPackage: NonEmptyString,
  emaPackage: z.literal("EUePI#1.0.0"),
  qrdTemplate: NonEmptyString,
  mappingVersion: NonEmptyString,
};

// Up to 3.0.0: the standards by literal.
const LegacyStandardsSchema = z
  .strictObject(legacyStandards)
  .meta({ id: "LegacyManifestStandards" });

const ManifestPackageSchema = z
  .strictObject({ package: PackageRef, sha256: Sha256Hex })
  .meta({ id: "ManifestPackage" });

// 4.0.0: the same fields, the two named packages among `packages`, which carries every package the
// validator loads (both locks the worker ships, src/fhir/standards-lock.ts) with the SHA-256 its
// lock records. The two rules below are not expressible in JSON Schema, so the published schema
// states them in its description and Zone A's verifier enforces them
// (zone-a/src/zone_a/run_manifest_rules.py): an outside verifier refuses what the worker refuses.
export const STANDARDS_RULES =
  "globalEpiPackage and emaPackage are each the package of an entry of packages; no two entries of packages name the same package (id#version); one id may appear at several versions.";

const StandardsSchema = z
  .strictObject({
    ...legacyStandards,
    globalEpiPackage: PackageRef,
    packages: z.array(ManifestPackageSchema).min(1),
  })
  .superRefine((standards, context) => {
    const named = standards.packages.map(({ package: ref }) => ref);
    for (const field of ["globalEpiPackage", "emaPackage"] as const) {
      if (!named.includes(standards[field])) {
        context.addIssue({ code: "custom", message: `${field} is not among the pinned packages` });
      }
    }
    if (new Set(named).size !== named.length) {
      context.addIssue({ code: "custom", message: "a package is pinned more than once" });
    }
  })
  .meta({ id: "ManifestStandards", description: STANDARDS_RULES });

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
  .strictObject({ targetStore: NonEmptyString, transactionSha256: Sha256Hex })
  .meta({ id: "ManifestPersistence" });

// Up to 2.0.0 a manifest was signed after the transaction and hashed its response.
const LegacyPersistenceSchema = z
  .strictObject({ targetStore: NonEmptyString, transactionResponseHash: Sha256Hex })
  .meta({ id: "LegacyManifestPersistence" });

const legacyRuntime = {
  sourceCommit: NonEmptyString,
  imageDigest: NonEmptyString,
  workflowRevision: NonEmptyString,
};

// Up to 3.0.0: no record of the validator that checked the run.
const LegacyRuntimeSchema = z.strictObject(legacyRuntime).meta({ id: "LegacyManifestRuntime" });

// 4.0.0: the validator sidecar's image digest beside the worker's ("development" off Cloud Run,
// as for the others).
const RuntimeSchema = z
  .strictObject({ ...legacyRuntime, validatorImageDigest: NonEmptyString })
  .meta({ id: "ManifestRuntime" });

// The fields every version up to 3.0.0 shares; status, dryRun and persistence are each version's
// own.
const legacyBody = {
  runId: Uuid,
  startedAt: IsoDateTime,
  completedAt: IsoDateTime,
  standards: LegacyStandardsSchema,
  validation: ValidationSchema,
  transformation: TransformationSchema,
  runtime: LegacyRuntimeSchema,
};

// 4.0.0's: the standards and the runtime name what validated the run.
const manifestBody = { ...legacyBody, standards: StandardsSchema, runtime: RuntimeSchema };

const legacyManifestBody = {
  ...legacyBody,
  status: LegacyRunStatus,
  dryRun: z.boolean(),
  persistence: LegacyPersistenceSchema.optional(),
};

export const RunManifestV1Schema = z
  .strictObject({
    schemaVersion: z.literal("1.0.0"),
    source: z.strictObject({
      kind: z.enum(["fixture", "healthcare-api"]),
      resource: NonEmptyString,
      hash: Sha256Hex,
    }),
    ...legacyManifestBody,
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
    ...legacyManifestBody,
    ingestion: IngestionEvidenceV1Schema.optional(),
  })
  .superRefine(documentRunsCarryIngestion)
  .meta({ id: "RunManifestV11" });

// 2.0.0, frozen: signed after the transaction, over its response's hash.
export const RunManifestV2Schema = z
  .strictObject({
    schemaVersion: z.literal("2.0.0"),
    source: runSource,
    ...legacyManifestBody,
    ingestion: IngestionEvidenceSchema.optional(),
  })
  .superRefine(documentRunsCarryIngestion)
  .meta({ id: "RunManifestV2" });

// 3.0.0, frozen: signed before its transaction, as 4.0.0 is, with the standards by literal.
const v3ManifestBody = {
  schemaVersion: z.literal("3.0.0"),
  source: runSource,
  ...legacyBody,
  ingestion: IngestionEvidenceSchema.optional(),
};

export const RunManifestV3Schema = z
  .discriminatedUnion("status", [
    z
      .strictObject({ ...v3ManifestBody, status: z.literal("validated"), dryRun: z.literal(true) })
      .meta({ id: "ValidatedRunManifestV3" }),
    z
      .strictObject({
        ...v3ManifestBody,
        status: z.literal("authorised"),
        dryRun: z.literal(false),
        persistence: PersistenceSchema,
      })
      .meta({ id: "AuthorisedRunManifestV3" }),
  ])
  .superRefine(documentRunsCarryIngestion)
  .meta({ id: "RunManifestV3" });

const currentManifestBody = {
  schemaVersion: z.literal(RUN_MANIFEST_VERSION),
  source: runSource,
  ...manifestBody,
  ingestion: IngestionEvidenceSchema.optional(),
};

// A dry run: validated, nothing persisted, no transaction named.
export const ValidatedRunManifestSchema = z
  .strictObject({
    ...currentManifestBody,
    status: z.literal("validated"),
    dryRun: z.literal(true),
  })
  .meta({ id: "ValidatedRunManifest" });

// A persist-mode run, signed before its transaction: validated and authorised to write exactly
// the transaction it names. `completedAt` is when validation and the transformation completed,
// which is before the transaction; the commit's time is the ledger row's `completed_at`.
export const AuthorisedRunManifestSchema = z
  .strictObject({
    ...currentManifestBody,
    status: z.literal("authorised"),
    dryRun: z.literal(false),
    persistence: PersistenceSchema,
  })
  .meta({ id: "AuthorisedRunManifest" });

export const RunManifestSchema = z
  .discriminatedUnion("status", [ValidatedRunManifestSchema, AuthorisedRunManifestSchema])
  .superRefine(documentRunsCarryIngestion)
  .meta({ id: "RunManifest" });

export const AnyRunManifestSchema = z.union([
  RunManifestV1Schema,
  RunManifestV11Schema,
  RunManifestV2Schema,
  RunManifestV3Schema,
  RunManifestSchema,
]);

export type RunManifestV1 = z.infer<typeof RunManifestV1Schema>;
export type RunManifest = z.infer<typeof RunManifestSchema>;
export type ManifestPersistence = z.infer<typeof PersistenceSchema>;
export type IngestionEvidence = z.infer<typeof IngestionEvidenceSchema>;
