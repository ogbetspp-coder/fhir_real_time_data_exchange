import { z } from "zod";

import {
  Count,
  GitCommit,
  HttpUrl,
  ImageDigest,
  IsoDateTime,
  NonEmptyString,
  NormalizationVersion,
  PackageRef,
  Sha256Hex,
  Token,
  Uuid,
} from "./common.js";
import { CANONICAL_SUBMISSION_VERSION, GraphType } from "./canonical-submission.js";
import { ApprovalSchema, FidelityStatus } from "./ingestion-provenance.js";
import {
  RunManifestV11Schema,
  RunManifestV1Schema,
  RunManifestV2Schema,
  RunManifestV3Schema,
  RunManifestV4Schema,
  RunManifestV5Schema,
} from "./run-manifest-frozen.js";

// Evidence schema for the signed run manifest (AGENTS.md: "update the mapping manifest and
// evidence schema together"). Versions 1.0.0, 1.1.0, 2.0.0, 3.0.0, 4.0.0 and 5.0.0 stay readable
// through `AnyRunManifestSchema`, each frozen in ./run-manifest-frozen.ts from copies of its own
// parts, so that no change here or to a live primitive changes what an old version accepts.
//
// Since 3.0.0 a manifest is signed BEFORE the FHIR transaction, so a persist-mode manifest cannot
// say the run persisted: its status is `authorised`, and it names the exact transaction it
// authorises (`persistence.transactionSha256`, the SHA-256 of the transaction Bundle sent, kept as
// the run's `persist-transaction` evidence object). Only the ledger row, written after the
// transaction succeeded, says `persisted`; the response's hash and the Bundle version it wrote are
// in the run's `commit` evidence object and the ledger row. A signed manifest with no ledger row
// is a run that did not commit (the transaction never ran, or was refused, atomically) or one that
// committed but went unrecorded (the transaction succeeded and the commit object or the ledger
// write then failed, after bounded retries). The two are told apart by reading the target store:
// the second left a Bundle version whose transaction hashes to `transactionSha256`.
//
// The two statuses are two shapes, a discriminated union on `status`, so the published JSON
// Schema (and every model generated from it) enforces that an authorised run, and only one,
// names its transaction and is not a dry run.
//
// 4.0.0 (audit B07, S-4) named the standards that validated the run instead of literals: every
// FHIR package the validator loads by id#version and SHA-256 (`standards.packages`, the Global ePI
// and EMA packages among them), and the validator sidecar's image digest
// (`runtime.validatorImageDigest`).
//
// 5.0.0 (audit C-9, C-8): what names the code, the images and the store is in its own grammar,
// where 4.0.0 took any text up to 1,024 characters. `runtime.sourceCommit` is a full git commit
// id, `runtime.imageDigest` and `runtime.validatorImageDigest` are image digests (each, or
// `development` off Cloud Run), `runtime.workflowRevision`, `standards.qrdTemplate`,
// `standards.mappingVersion` and `persistence.targetStore` are tokens; the worker reads them
// through its configuration (src/config.ts), which refuses a malformed one at startup. And the
// published schema states the two rules between fields that JSON Schema can: a document run, and
// only one, carries an ingestion block; an authority import, and only one, records what Zone B
// fetched (src/contracts/json-schema.ts, REFINEMENTS). A major: 4.0.0 accepted values 5.0.0
// refuses.
//
// 5.1.0 (ADR 0006 P4, D1): the ingestion block's `sourceKind` may be `certified-word`, and its
// `contractVersion` follows `CanonicalSubmission` 2.1.0, as it follows the submission's version
// in every release. A minor, as the submission's is: the new source kind refuses nothing that 5.0.0
// accepted of a 2.1.0 run, and a 5.0.0 manifest stays readable as 5.0.0.

export const RUN_MANIFEST_VERSION = "5.1.0";

const ManifestPackageSchema = z
  .strictObject({ package: PackageRef, sha256: Sha256Hex })
  .meta({ id: "ManifestPackage" });

// The two named packages are among `packages`, which carries every package the validator loads
// (both locks the worker ships, src/fhir/standards-lock.ts) with the SHA-256 its lock records.
// The two rules below are not expressible in JSON Schema, so the published schema states them in
// its description and Zone A's verifier enforces them (zone-a/src/zone_a/run_manifest_rules.py):
// an outside verifier refuses what the worker refuses.
export const STANDARDS_RULES =
  "globalEpiPackage and emaPackage are each the package of an entry of packages; no two entries of packages name the same package (id#version); one id may appear at several versions.";

const StandardsSchema = z
  .strictObject({
    fhir: z.literal("5.0.0"),
    globalEpiPackage: PackageRef,
    emaPackage: z.literal("EUePI#1.0.0"),
    qrdTemplate: Token,
    mappingVersion: Token,
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
  .strictObject({ targetStore: Token, transactionSha256: Sha256Hex })
  .meta({ id: "ManifestPersistence" });

// What the worker records where it runs off Cloud Run, which sets none of the runtime values.
export const DEVELOPMENT_RUNTIME = "development";
const Development = z.literal(DEVELOPMENT_RUNTIME);

const RuntimeSchema = z
  .strictObject({
    sourceCommit: z.union([GitCommit, Development]),
    imageDigest: z.union([ImageDigest, Development]),
    validatorImageDigest: z.union([ImageDigest, Development]),
    workflowRevision: Token,
  })
  .meta({
    id: "ManifestRuntime",
    description:
      "The code and the images that produced the run: a full git commit id and image digests, or `development` off Cloud Run.",
  });

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

// The current submission contract's run: a manifest major follows a `CanonicalSubmission` one.
export const IngestionEvidenceSchema = z
  .strictObject({
    submissionId: Uuid,
    contractVersion: z.literal(CANONICAL_SUBMISSION_VERSION),
    sourceKind: z.enum(["drawn", "authority-publication", "certified-word"]),
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

const manifestBody = {
  schemaVersion: z.literal(RUN_MANIFEST_VERSION),
  source: runSource,
  runId: Uuid,
  startedAt: IsoDateTime,
  completedAt: IsoDateTime,
  standards: StandardsSchema,
  validation: ValidationSchema,
  transformation: TransformationSchema,
  runtime: RuntimeSchema,
  ingestion: IngestionEvidenceSchema.optional(),
};

// A dry run: validated, nothing persisted, no transaction named.
export const ValidatedRunManifestSchema = z
  .strictObject({
    ...manifestBody,
    status: z.literal("validated"),
    dryRun: z.literal(true),
  })
  .meta({ id: "ValidatedRunManifest" });

// A persist-mode run, signed before its transaction: validated and authorised to write exactly
// the transaction it names. `completedAt` is when validation and the transformation completed,
// which is before the transaction; the commit's time is the ledger row's `completed_at`.
export const AuthorisedRunManifestSchema = z
  .strictObject({
    ...manifestBody,
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
  RunManifestV4Schema,
  RunManifestV5Schema,
  RunManifestSchema,
]);

export {
  RunManifestV11Schema,
  RunManifestV1Schema,
  RunManifestV2Schema,
  RunManifestV3Schema,
  RunManifestV4Schema,
  RunManifestV5Schema,
  type RunManifestV1,
} from "./run-manifest-frozen.js";
export type RunManifest = z.infer<typeof RunManifestSchema>;
export type ManifestPersistence = z.infer<typeof PersistenceSchema>;
export type ManifestRuntime = z.infer<typeof RuntimeSchema>;
export type IngestionEvidence = z.infer<typeof IngestionEvidenceSchema>;
