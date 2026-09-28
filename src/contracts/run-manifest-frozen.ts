import { z } from "zod";

// The run manifest's released versions before the current one, frozen (audit C-9). Each is built
// from copies made here of every primitive, enum and block it uses, and imports nothing from the
// live contracts: a change to a live primitive (`HttpUrl`, `Token`), to the approval union or to
// `CANONICAL_SUBMISSION_VERSION` would otherwise change what an old version accepts, and a ledger
// row written under it could stop parsing, or a row it never allowed could start to.
// test/contracts/run-manifest-frozen.test.ts holds this file to importing zod alone, and reads
// real manifests each version's own code emitted (test/fixtures/run-manifest/).
//
// The copies are the grammars as they stand at 5.0.0, and every run that ever completed was
// validated under them: they reached their present form on 2026-09-19 (1a70910, where `HttpUrl`
// stopped being `z.url()`) and 2026-09-28 (#145, which respelt `\d` as `[0-9]`, the same language),
// and no run had completed before 2026-09-20 (docs/validation/README.md, "Official validation
// gate"). These schemas are read, never published: each version is named by an id, as before, and
// none of their parts carries one, so no copy shares an id with the live part it was copied from.

const Sha256Hex = z.string().regex(/^[0-9a-f]{64}$/);
const Uuid = z.uuid();
const IsoDateTime = z.iso.datetime({ offset: true });
const URL_TAIL = "[A-Za-z0-9._~:/?#@!$&'()*+,;=%|-]";
const HttpUrl = z
  .string()
  .regex(new RegExp(`^https?://[A-Za-z0-9.-]{1,253}(?::[0-9]{1,5})?(?:[/?#]${URL_TAIL}*)?$`))
  .max(256);
const PrincipalId = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/);
const RecordRef = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._:/#?=&%+-]{0,511}$/);
const Token = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$/);
const NonEmptyString = z.string().min(1).max(1024);
const Count = z.number().int().nonnegative();
const PackageRef = z
  .string()
  .regex(/^[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)*#[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$/)
  .max(256);
const NormalizationVersion = z.string().regex(/^fidelity-norm\/[0-9]+\.[0-9]+\.[0-9]+$/);
const AuthorityId = z.string().regex(/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);

const FidelityPassed = z.literal("passed");
const ApproverRole = z.enum(["content-reviewer", "qa-reviewer"]);
const AttestationMethod = z.enum(["api-attestation", "manual-record"]);
const ApprovalMeaning = z.enum(["reviewed-fidelity-and-structure"]);
const Authority = z.enum(["EMA", "synthetic"]);
const GraphType = z.enum(["type1", "type2"]);

// `rejected` and `failed` were never written: a refused or failed run leaves no manifest, only
// its log line. They are kept for the versions that listed them.
const LegacyRunStatus = z.enum(["validated", "persisted", "rejected", "failed"]);

const legacyStandards = {
  fhir: z.literal("5.0.0"),
  globalEpiPackage: NonEmptyString,
  emaPackage: z.literal("EUePI#1.0.0"),
  qrdTemplate: NonEmptyString,
  mappingVersion: NonEmptyString,
};

// Up to 3.0.0: the standards by literal.
const LegacyStandardsSchema = z.strictObject(legacyStandards);

// 4.0.0: every package the validator loaded, the two named packages among them.
const StandardsV4Schema = z
  .strictObject({
    ...legacyStandards,
    globalEpiPackage: PackageRef,
    packages: z.array(z.strictObject({ package: PackageRef, sha256: Sha256Hex })).min(1),
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
  });

const ValidationSchema = z.strictObject({
  preflightErrors: Count,
  officialValidationExecuted: z.boolean(),
  officialProfileErrors: Count,
  cloudValidationExecuted: z.boolean(),
  cloudProfileErrors: Count,
  profiles: z.array(HttpUrl),
});

const TransformationSchema = z.strictObject({
  inputHash: Sha256Hex,
  outputHash: Sha256Hex,
  decisions: Count,
});

// 3.0.0 and 4.0.0: signed before the transaction it names.
const PersistenceSchema = z.strictObject({
  targetStore: NonEmptyString,
  transactionSha256: Sha256Hex,
});

// Up to 2.0.0: signed after the transaction, over its response's hash.
const LegacyPersistenceSchema = z.strictObject({
  targetStore: NonEmptyString,
  transactionResponseHash: Sha256Hex,
});

const legacyRuntime = {
  sourceCommit: NonEmptyString,
  imageDigest: NonEmptyString,
  workflowRevision: NonEmptyString,
};

const LegacyRuntimeSchema = z.strictObject(legacyRuntime);
const RuntimeV4Schema = z.strictObject({ ...legacyRuntime, validatorImageDigest: NonEmptyString });

const legacyBody = {
  runId: Uuid,
  startedAt: IsoDateTime,
  completedAt: IsoDateTime,
  standards: LegacyStandardsSchema,
  validation: ValidationSchema,
  transformation: TransformationSchema,
  runtime: LegacyRuntimeSchema,
};

const legacyManifestBody = {
  ...legacyBody,
  status: LegacyRunStatus,
  dryRun: z.boolean(),
  persistence: LegacyPersistenceSchema.optional(),
};

const coverage = z.strictObject({
  pageCodePoints: Count,
  bodyCodePoints: Count,
  coveredCodePoints: Count,
  uncoveredGaps: Count,
});

const ingestionFidelity = z.strictObject({
  status: FidelityPassed,
  normalizationVersion: NormalizationVersion,
  sectionsChecked: Count,
  sectionsMatched: Count,
  reportSha256: Sha256Hex,
  narrativeBindingSha256: Sha256Hex,
  coverage,
});

const attestedApproval = {
  approverId: PrincipalId,
  approverRole: ApproverRole,
  approvedAt: IsoDateTime,
  method: AttestationMethod,
  meaning: ApprovalMeaning,
  approvedContentSha256: Sha256Hex,
  recordRef: RecordRef.optional(),
};

// The ingestion block of a 1.1.0 manifest: `CanonicalSubmission` 1.0.0's approval.
const IngestionEvidenceV1Schema = z.strictObject({
  submissionId: Uuid,
  contractVersion: z.literal("1.0.0"),
  sourceDocumentSha256: Sha256Hex,
  extractionRunId: Uuid,
  parser: Token,
  modelId: Token.optional(),
  promptTemplateVersion: Token.optional(),
  fidelity: ingestionFidelity,
  approval: z.strictObject(attestedApproval),
  provenanceResourceId: Uuid,
});

// `CanonicalSubmission` 2.0.0's approval union.
const ApprovalV2Schema = z.discriminatedUnion("method", [
  z.strictObject(attestedApproval),
  z.strictObject({
    method: z.literal("authority-publication"),
    meaning: z.literal("authority-publication-imported"),
    authority: Authority,
    authorityStatus: z.enum(["pilot"]),
    publication: z.strictObject({
      epiId: Token,
      documentId: AuthorityId,
      indexId: AuthorityId,
      versionNumber: Token,
      procedureNumber: Token,
      authorityTimestamp: IsoDateTime,
    }),
    requestedBy: PrincipalId,
    requestedAt: IsoDateTime,
    approvedContentSha256: Sha256Hex,
  }),
]);

// The ingestion block of 2.0.0, 3.0.0 and 4.0.0: a `CanonicalSubmission` 2.0.0 run.
const IngestionEvidenceV2Schema = z
  .strictObject({
    submissionId: Uuid,
    contractVersion: z.literal("2.0.0"),
    sourceKind: z.enum(["drawn", "authority-publication"]),
    graphType: GraphType,
    allowSyntheticSources: z.boolean(),
    sourceDocumentSha256: Sha256Hex,
    extractionRunId: Uuid,
    parser: Token,
    modelId: Token.optional(),
    promptTemplateVersion: Token.optional(),
    fidelity: ingestionFidelity,
    approval: ApprovalV2Schema,
    authority: z
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
      .optional(),
    provenanceResourceId: Uuid,
  })
  .superRefine((evidence, context) => {
    if ((evidence.sourceKind === "authority-publication") !== (evidence.authority !== undefined)) {
      context.addIssue({
        code: "custom",
        message: "an authority import, and only one, records what Zone B fetched",
      });
    }
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

export const RunManifestV11Schema = z
  .strictObject({
    schemaVersion: z.literal("1.1.0"),
    source: runSource,
    ...legacyManifestBody,
    ingestion: IngestionEvidenceV1Schema.optional(),
  })
  .superRefine(documentRunsCarryIngestion)
  .meta({ id: "RunManifestV11" });

// 2.0.0: signed after the transaction, over its response's hash.
export const RunManifestV2Schema = z
  .strictObject({
    schemaVersion: z.literal("2.0.0"),
    source: runSource,
    ...legacyManifestBody,
    ingestion: IngestionEvidenceV2Schema.optional(),
  })
  .superRefine(documentRunsCarryIngestion)
  .meta({ id: "RunManifestV2" });

// 3.0.0 and 4.0.0: signed before the transaction, a discriminated union on `status`.
function signedBeforeTransaction<Shape extends z.core.$ZodLooseShape>(shape: Shape) {
  return z.discriminatedUnion("status", [
    z.strictObject({ ...shape, status: z.literal("validated"), dryRun: z.literal(true) }),
    z.strictObject({
      ...shape,
      status: z.literal("authorised"),
      dryRun: z.literal(false),
      persistence: PersistenceSchema,
    }),
  ]);
}

// 3.0.0: the standards by literal.
export const RunManifestV3Schema = signedBeforeTransaction({
  schemaVersion: z.literal("3.0.0"),
  source: runSource,
  ...legacyBody,
  ingestion: IngestionEvidenceV2Schema.optional(),
})
  .superRefine(documentRunsCarryIngestion)
  .meta({ id: "RunManifestV3" });

// 4.0.0: the packages that validated the run and the validator's image, as free text.
export const RunManifestV4Schema = signedBeforeTransaction({
  schemaVersion: z.literal("4.0.0"),
  source: runSource,
  ...legacyBody,
  standards: StandardsV4Schema,
  runtime: RuntimeV4Schema,
  ingestion: IngestionEvidenceV2Schema.optional(),
})
  .superRefine(documentRunsCarryIngestion)
  .meta({ id: "RunManifestV4" });

export type RunManifestV1 = z.infer<typeof RunManifestV1Schema>;
