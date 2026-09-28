import { randomUUID } from "node:crypto";

import type { AppConfig } from "./config.js";
import {
  AUTHORITY_IMPORT_PREFIX,
  CANONICAL_SUBMISSION_VERSION,
  RUN_MANIFEST_VERSION,
  RunManifestSchema,
  SubmissionRejectedError,
  Uuid,
  verifyDocumentSubmission,
  type DocumentGateResult,
  type DocumentSubmissionInput,
  type IngestionEvidence,
  type ManifestPersistence,
} from "./contracts/index.js";
import { OfficialFhirValidatorClient } from "./fhir/official-validator.js";
import {
  hasValidationErrors,
  validateEmaPreflight,
  validateCanonicalPreflight,
} from "./fhir/preflight.js";
import { toProvenanceResource } from "./fhir/provenance.js";
import { sourceIdentifierValue, transformType2ToEma } from "./fhir/transform.js";
import { mappingReference, type EmaMapping } from "./fhir/mapping.js";
import type { FhirBundle, FhirResource, OperationOutcome } from "./fhir/types.js";
import {
  EMA_EPI_PACKAGE_ID,
  GLOBAL_EPI_PACKAGE_ID,
  GLOBAL_TYPE2_BUNDLE_PROFILE,
  QRD_TEMPLATE_VERSION,
} from "./fhir/standards.js";
import { pinnedPackage, pinnedPackages } from "./fhir/standards-lock.js";
import { GcpEvidenceStore, type RunManifest, type SignedManifest } from "./gcp/evidence.js";
import {
  HealthcareApiClient,
  buildPersistTransaction,
  persistedVersion,
  type PersistedVersion,
} from "./gcp/healthcare.js";
import { GcpLineagePublisher } from "./gcp/lineage.js";
import { defaultFetcher, type AuthorityFetcher } from "./authority/fetch.js";
import { verifyAuthorityImport, type AuthorityGateResult } from "./authority/gate.js";
import { sha256Bytes } from "./authority/import.js";
import { sha256 } from "./lib/hash.js";
import { log } from "./lib/logger.js";

const GLOBAL_TYPE2_PROFILE = GLOBAL_TYPE2_BUNDLE_PROFILE;

type BaseInput = {
  runId?: string;
  sourceResource: string;
};

export type PipelineInput =
  | (BaseInput & {
      sourceKind: "fixture" | "healthcare-api";
      source: FhirBundle;
    })
  // The three document parts stay `unknown` all the way to the gate: nothing upstream of
  // `verifyDocumentSubmission` may assume a shape it has not proved.
  | (BaseInput & { sourceKind: "document" } & DocumentSubmissionInput);

export type PipelineResult = {
  runId: string;
  // `validated` for a dry run; `persisted` once the transaction committed and the ledger recorded
  // it. The signed manifest itself says `authorised`: it was signed before the transaction.
  status: "validated" | "persisted";
  emaList: FhirResource;
  emaBundle: FhirBundle;
  mappingDecisions: ReturnType<typeof transformType2ToEma>["mappingDecisions"];
  outcomes: {
    sourcePreflight: OperationOutcome;
    emaPreflight: OperationOutcome;
    official: OperationOutcome[];
    cloud: OperationOutcome[];
  };
  evidence: SignedManifest;
  artifactUris: string[];
  // The version of the EMA document Bundle the transaction wrote, as its response named it;
  // absent for a dry run, or when the response did not say.
  persistedBundle?: PersistedVersion;
};

function countErrors(outcomes: OperationOutcome[]): number {
  return outcomes.reduce(
    (total, outcome) =>
      total +
      outcome.issue.filter(({ severity }) => severity === "fatal" || severity === "error").length,
    0,
  );
}

// Hashes, counts, enumerations, and identifiers only; never narrative (ADR 0002).
function ingestionEvidence(
  gate: DocumentGateResult,
  provenanceResourceId: string,
  allowSyntheticSources: boolean,
  authority: AuthorityRun | undefined,
): IngestionEvidence {
  const { provenance, approval, submissionId, graphType } = gate.submission;
  const { parser, model, promptTemplate, extractionRunId } = provenance.extraction;
  const source = provenance.sourceDocument;
  // The signed manifest states the report's own status, never an assumed one; the gate refuses
  // any other, and this holds even if that check were lost.
  const { status } = gate.report;
  if (status !== "passed") throw new Error("Only a passed fidelity report reaches run evidence");

  return {
    submissionId,
    contractVersion: CANONICAL_SUBMISSION_VERSION,
    sourceKind: source.kind,
    graphType,
    allowSyntheticSources,
    sourceDocumentSha256: source.kind === "drawn" ? source.sha256 : source.document.sha256,
    extractionRunId,
    parser: `${parser.name}@${parser.version}`,
    ...(model === undefined ? {} : { modelId: model.id }),
    ...(promptTemplate === undefined ? {} : { promptTemplateVersion: promptTemplate.version }),
    fidelity: {
      status,
      normalizationVersion: gate.report.normalizationVersion,
      sectionsChecked: gate.report.summary.total,
      sectionsMatched: gate.report.summary.verified,
      reportSha256: gate.report.reportHash,
      narrativeBindingSha256: gate.report.narrativeBindingSha256,
      coverage: { ...gate.report.coverage },
    },
    approval,
    ...(authority === undefined
      ? {}
      : {
          authority: {
            importerVersion: authority.importerVersion,
            fetched: authority.fetched.map(({ url, bytes, fetchedAt }) => ({
              url,
              sha256: sha256Bytes(bytes),
              byteLength: bytes.length,
              fetchedAt,
            })),
          },
        }),
    provenanceResourceId,
  };
}

type DocumentInput = Extract<PipelineInput, { sourceKind: "document" }>;

// What the gate learnt about an authority import: the fetched files and the importer that ran.
type AuthorityRun = Omit<AuthorityGateResult, "gate">;

// Whether a submission, before it is parsed, says it is an authority's publication: only that
// decides which gate it goes through; the gate itself parses and checks everything.
function claimsAuthoritySource(submission: unknown): boolean {
  const kind = (
    submission as { provenance?: { sourceDocument?: { kind?: unknown } } } | null | undefined
  )?.provenance?.sourceDocument?.kind;
  return kind === "authority-publication";
}

async function documentGate(
  input: DocumentInput,
  mapping: EmaMapping,
  config: AppConfig,
  runId: string,
  dependencies: PipelineDependencies,
): Promise<{ gate: DocumentGateResult; authority?: AuthorityRun }> {
  const options = { allowSyntheticSources: config.ALLOW_SYNTHETIC_SOURCES };
  try {
    if (claimsAuthoritySource(input.submission)) {
      const fetcher =
        dependencies.authorityFetcher ?? defaultFetcher(mapping, config.ALLOW_SYNTHETIC_SOURCES);
      const { gate, ...authority } = await verifyAuthorityImport(
        input,
        mapping,
        { ...options, dryRun: config.DRY_RUN },
        fetcher,
      );
      return { gate, authority };
    }
    return { gate: verifyDocumentSubmission(input, mapping.sourceCodeSystem, options) };
  } catch (error) {
    if (error instanceof SubmissionRejectedError) {
      log("warning", "Canonical submission rejected", {
        runId,
        stage: "document-gate",
        errorCount: error.issues.length,
      });
    }
    throw error;
  }
}

// What a run may be given instead of its production default; tests use it.
export type PipelineDependencies = { authorityFetcher?: AuthorityFetcher };

export async function runPipeline(
  input: PipelineInput,
  mapping: EmaMapping,
  config: AppConfig,
  dependencies: PipelineDependencies = {},
): Promise<PipelineResult> {
  if (input.runId !== undefined && !Uuid.safeParse(input.runId).success) {
    throw new Error("runId must be a UUID");
  }
  // The run-source allowlist (ADR 0002 consequences), here as well as at the HTTP surface, so no
  // caller of the pipeline (scripts/dev/run-pipeline.ts, a test, a later entry point) can run a
  // source the deployment disabled: a no-synthetic deployment never signs fixture content.
  if (!config.ENABLED_RUN_SOURCES.includes(input.sourceKind)) {
    throw new Error("Run source is disabled");
  }
  const runId = input.runId ?? randomUUID();
  const startedAt = new Date().toISOString();
  const stageStarted = Date.now();
  log("info", "ePI interoperability run started", {
    runId,
    stage: "pipeline",
    dryRun: config.DRY_RUN,
  });

  let gate: DocumentGateResult | undefined;
  let authority: AuthorityRun | undefined;
  let source: FhirBundle;
  if (input.sourceKind === "document") {
    ({ gate, authority } = await documentGate(input, mapping, config, runId, dependencies));
    source = gate.bundle;
  } else {
    source = input.source;
  }

  const sourcePreflight = validateCanonicalPreflight(source, gate?.submission.graphType ?? "type2");
  if (hasValidationErrors(sourcePreflight)) {
    log("warning", "Canonical preflight rejected", {
      runId,
      stage: "source-preflight",
      errorCount: countErrors([sourcePreflight]),
    });
    throw new Error("Canonical preflight failed");
  }

  // The authority-import namespace is written only by the importer: only a submission the gate
  // recomputed from the authority's files may carry it, and every other route, the ungated
  // fixture and healthcare-api sources included, is refused (docs/design/authority-import-contract.md,
  // D7).
  if (
    authority === undefined &&
    sourceIdentifierValue(source).startsWith(AUTHORITY_IMPORT_PREFIX)
  ) {
    throw new Error("Source identifier is in the reserved authority-import namespace");
  }

  // The standards the manifest names, read from the lock the image ships before anything is
  // written: a missing or malformed lock fails the run here, not after its side effects.
  const packages = pinnedPackages();
  const standards: RunManifest["standards"] = {
    fhir: "5.0.0",
    globalEpiPackage: pinnedPackage(packages, GLOBAL_EPI_PACKAGE_ID),
    emaPackage: pinnedPackage(packages, EMA_EPI_PACKAGE_ID) as "EUePI#1.0.0",
    qrdTemplate: QRD_TEMPLATE_VERSION,
    mappingVersion: mapping.mappingVersion,
    packages,
  };

  const transformed = transformType2ToEma(source, mapping, QRD_TEMPLATE_VERSION);
  const emaPreflight = validateEmaPreflight(transformed.list, transformed.documentBundle, mapping);
  if (hasValidationErrors(emaPreflight)) {
    log("warning", "EMA preflight rejected", {
      runId,
      stage: "ema-preflight",
      errorCount: countErrors([emaPreflight]),
    });
    throw new Error("EMA structural preflight failed");
  }

  const emaBundleId = transformed.documentBundle.id;
  const emaCompositionId = transformed.documentBundle.entry[0]?.resource.id;
  if (emaBundleId === undefined || emaCompositionId === undefined) {
    throw new Error("The EMA document Bundle and Composition require ids");
  }
  const provenanceResource =
    gate === undefined
      ? undefined
      : toProvenanceResource(gate.submission, gate.report, {
          bundleId: emaBundleId,
          compositionId: emaCompositionId,
          ...(authority?.fetched[0] === undefined
            ? {}
            : { fetchedAt: authority.fetched[0].fetchedAt }),
        });

  let ingestion: IngestionEvidence | undefined;
  if (gate !== undefined) {
    const provenanceResourceId = provenanceResource?.id;
    if (provenanceResourceId === undefined) {
      throw new Error("Ingestion Provenance requires an id");
    }
    ingestion = ingestionEvidence(
      gate,
      provenanceResourceId,
      config.ALLOW_SYNTHETIC_SOURCES,
      authority,
    );
  }

  const profiles = [
    GLOBAL_TYPE2_PROFILE,
    mapping.profiles.list,
    mapping.profiles.bundle,
    ...mapping.profiles.composition,
  ];
  // A dry run's manifest is `validated`; a persist-mode run's is `authorised`, signed before its
  // transaction and naming it. Only the ledger row, written after the commit, says `persisted`.
  const composeManifest = (
    completedAt: string,
    validation: RunManifest["validation"],
    outcome:
      | { status: "validated"; dryRun: true }
      | { status: "authorised"; dryRun: false; persistence: ManifestPersistence },
  ): RunManifest => ({
    schemaVersion: RUN_MANIFEST_VERSION,
    runId,
    startedAt,
    completedAt,
    ...outcome,
    source: {
      kind: input.sourceKind,
      resource: input.sourceResource,
      hash: transformed.inputHash,
    },
    standards,
    validation,
    transformation: {
      inputHash: transformed.inputHash,
      outputHash: transformed.outputHash,
      decisions: transformed.mappingDecisions.length,
    },
    runtime: {
      sourceCommit: process.env.GIT_COMMIT ?? "development",
      imageDigest: process.env.IMAGE_DIGEST ?? "development",
      validatorImageDigest: process.env.VALIDATOR_IMAGE_DIGEST ?? "development",
      workflowRevision: process.env.WORKFLOW_REVISION ?? process.env.K_REVISION ?? "development",
    },
    ...(ingestion === undefined ? {} : { ingestion }),
  });

  const cloudOutcomes: OperationOutcome[] = [];
  const officialOutcomes: OperationOutcome[] = [];
  const artifactUris: string[] = [];
  const validation = (): RunManifest["validation"] => ({
    preflightErrors: countErrors([sourcePreflight, emaPreflight]),
    officialValidationExecuted: !config.DRY_RUN,
    officialProfileErrors: countErrors(officialOutcomes),
    cloudValidationExecuted: !config.DRY_RUN,
    cloudProfileErrors: countErrors(cloudOutcomes),
    profiles,
  });

  const finish = (
    status: PipelineResult["status"],
    signed: SignedManifest,
    persistedBundle: PersistedVersion | undefined,
  ): PipelineResult => {
    log("info", "ePI interoperability run completed", {
      runId,
      stage: "pipeline",
      outcome: status,
      durationMs: Date.now() - stageStarted,
      mappingDecisions: transformed.mappingDecisions.length,
    });

    return {
      runId,
      status,
      emaList: transformed.list,
      emaBundle: transformed.documentBundle,
      mappingDecisions: transformed.mappingDecisions,
      outcomes: {
        sourcePreflight,
        emaPreflight,
        official: officialOutcomes,
        cloud: cloudOutcomes,
      },
      evidence: signed,
      artifactUris,
      ...(persistedBundle === undefined ? {} : { persistedBundle }),
    };
  };

  if (config.DRY_RUN) {
    const manifest = composeManifest(new Date().toISOString(), validation(), {
      status: "validated",
      dryRun: true,
    });
    RunManifestSchema.parse(manifest);
    return finish("validated", { manifest, manifestHash: sha256(manifest) }, undefined);
  }

  const validatorUrl = config.FHIR_VALIDATOR_URL;
  if (validatorUrl === undefined) throw new Error("FHIR_VALIDATOR_URL is required");
  const officialValidator = new OfficialFhirValidatorClient(validatorUrl);
  officialOutcomes.push(await officialValidator.validate(source, [GLOBAL_TYPE2_PROFILE]));
  officialOutcomes.push(
    await officialValidator.validate(transformed.list, [mapping.profiles.list]),
  );
  officialOutcomes.push(
    await officialValidator.validate(transformed.documentBundle, [mapping.profiles.bundle]),
  );
  const targetComposition = transformed.documentBundle.entry[0]?.resource;
  if (targetComposition === undefined) throw new Error("Transformed Composition is missing");
  officialOutcomes.push(
    await officialValidator.validate(targetComposition, mapping.profiles.composition),
  );
  if (officialOutcomes.some(hasValidationErrors)) {
    throw new Error("Official HL7 FHIR profile validation failed");
  }

  const healthcare = new HealthcareApiClient(config);
  cloudOutcomes.push(await healthcare.validate(source, GLOBAL_TYPE2_PROFILE, runId));
  cloudOutcomes.push(await healthcare.validate(transformed.list, mapping.profiles.list, runId));
  cloudOutcomes.push(
    await healthcare.validate(transformed.documentBundle, mapping.profiles.bundle, runId),
  );
  for (const profile of mapping.profiles.composition) {
    cloudOutcomes.push(await healthcare.validate(targetComposition, profile, runId));
  }
  if (cloudOutcomes.some(hasValidationErrors)) {
    throw new Error("Cloud Healthcare API profile validation failed");
  }

  // The commit order (docs/architecture.md): evidence, then the signed manifest, then the FHIR
  // transaction, then the commit object and the ledger row that record it. The manifest says
  // `authorised`, names the exact transaction by hash, and is signed before anything reaches the
  // FHIR store, so no version is ever live without a signed manifest over its transaction. A
  // failure before the transaction, or the transaction's own refusal, leaves nothing live. The
  // transaction commits when the store accepts it; a failure after that (the commit object or the
  // ledger insert, each retried within a bound) leaves a committed run with no record, answered
  // as `committed-unrecorded` and logged with the run's manifest hash. So a signed manifest with
  // no ledger row is one of the two, and the target store tells them apart: only the second left
  // a Bundle version, written by the `persist-transaction` object's transaction. Nothing is written
  // before the manifest is proved against its schema.
  const transaction = buildPersistTransaction(
    transformed.list,
    transformed.documentBundle,
    runId,
    provenanceResource === undefined ? [] : [provenanceResource],
  );
  const transactionSha256 = sha256(transaction);
  const manifest = composeManifest(new Date().toISOString(), validation(), {
    status: "authorised",
    dryRun: false,
    persistence: { targetStore: config.TARGET_FHIR_STORE_ID ?? "unknown", transactionSha256 },
  });
  RunManifestSchema.parse(manifest);

  // Every write is conditional on its object not existing, so the first one claims the runId: a
  // runId already used is refused here, before the FHIR store is touched.
  const store = new GcpEvidenceStore(config);
  artifactUris.push(await store.writeJson(runId, "source-type2", source));
  artifactUris.push(await store.writeJson(runId, "ema-list", transformed.list));
  artifactUris.push(
    await store.writeJson(runId, "ema-document-bundle", transformed.documentBundle),
  );
  artifactUris.push(
    await store.writeJson(runId, "mapping-decisions", transformed.mappingDecisions),
  );
  artifactUris.push(
    await store.writeJson(runId, "validation-outcomes", {
      sourcePreflight,
      emaPreflight,
      officialOutcomes,
      cloudOutcomes,
    }),
  );
  if (gate !== undefined && provenanceResource !== undefined) {
    artifactUris.push(await store.writeJson(runId, "canonical-submission", gate.submission));
    artifactUris.push(
      await store.writeJson(runId, "ingestion-provenance", gate.submission.provenance),
    );
    artifactUris.push(await store.writeJson(runId, "fidelity-report", gate.report));
    artifactUris.push(await store.writeJson(runId, "provenance-resource", provenanceResource));
  }
  artifactUris.push(await store.writeJson(runId, "persist-transaction", transaction));
  const evidence = await store.signManifest(manifest);
  artifactUris.push(await store.writeJson(runId, "signed-manifest", evidence));

  const transactionResponse = await healthcare.executeTransaction(transaction, runId);
  const committedAt = new Date().toISOString();
  const persistedBundle = persistedVersion(transactionResponse, "Bundle", emaBundleId);
  if (persistedBundle === undefined) {
    log("warning", "Transaction response names no version of the document Bundle", {
      runId,
      stage: "persist",
    });
  }
  try {
    // Hashes and identifiers only: what the transaction answered, bound to what was signed.
    artifactUris.push(
      await store.writeJson(
        runId,
        "commit",
        {
          runId,
          manifestHash: evidence.manifestHash,
          transactionSha256,
          transactionResponseSha256: sha256(transactionResponse),
          committedAt,
          targetBundle: {
            id: emaBundleId,
            versionId: persistedBundle?.versionId ?? null,
            lastUpdated: persistedBundle?.lastUpdated ?? null,
          },
        },
        { afterCommit: true },
      ),
    );
    await store.writeLedger(evidence, {
      committedAt,
      bundleVersionId: persistedBundle?.versionId ?? null,
    });
  } catch (error) {
    // The transaction is live. What reconciles it: this line, the signed manifest and the
    // `persist-transaction` object, and the Bundle version in the target store.
    log("error", "A committed run could not be recorded", {
      runId,
      stage: "commit",
      manifestHash: evidence.manifestHash,
      errorType: error instanceof Error ? error.name : typeof error,
    });
    throw new Error("The run committed but its record could not be written", { cause: error });
  }

  // Lineage is published after the commit and is best effort: a Data Lineage failure no longer
  // turns a committed run into a 500 that invites a retry.
  try {
    const project = config.GOOGLE_CLOUD_PROJECT ?? "unknown";
    const dataset = config.HEALTHCARE_DATASET_ID ?? "unknown";
    const sourceStore = config.SOURCE_FHIR_STORE_ID ?? "unknown";
    const targetStore = config.TARGET_FHIR_STORE_ID ?? "unknown";
    const lineage = new GcpLineagePublisher(config);
    const importSource =
      gate?.submission.provenance.sourceDocument.kind === "authority-publication"
        ? gate.submission.provenance.sourceDocument
        : undefined;
    // An import's lineage names the authority's document it came from
    // (docs/design/authority-import-contract.md, D12).
    const sourceFqn =
      input.sourceKind === "healthcare-api"
        ? `healthcare:${project}.${config.GCP_LOCATION}.${dataset}.${sourceStore}.${input.sourceResource.replace("/", ".")}`
        : gate === undefined
          ? `custom:ema-flow.${input.sourceResource}`
          : importSource !== undefined
            ? `custom:authority-import.${importSource.authority === "EMA" ? "ema" : "synthetic"}.${importSource.document.id}`
            : `custom:zone-a.${gate.submission.submissionId}`;
    const lineageResources = await lineage.publish({
      runId,
      startedAt,
      completedAt: manifest.completedAt,
      mapping: mappingReference(mapping),
      sourceFqn,
      targetFhirFqn: `healthcare:${project}.${config.GCP_LOCATION}.${dataset}.${targetStore}.Bundle.${emaBundleId}`,
      targetBigQueryFqn: `bigquery:${project}.${config.FHIR_ANALYTICS_DATASET ?? "unknown"}.Bundle`,
      inputHash: transformed.inputHash,
      outputHash: transformed.outputHash,
    });
    artifactUris.push(await store.writeJson(runId, "lineage-resources", lineageResources));
  } catch (error) {
    log("warning", "Lineage was not published for a committed run", {
      runId,
      stage: "lineage",
      errorType: error instanceof Error ? error.name : typeof error,
    });
  }

  return finish("persisted", evidence, persistedBundle);
}
