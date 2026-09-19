import { randomUUID } from "node:crypto";

import type { AppConfig } from "./config.js";
import { OfficialFhirValidatorClient } from "./fhir/official-validator.js";
import {
  hasValidationErrors,
  validateEmaPreflight,
  validateType2Preflight,
} from "./fhir/preflight.js";
import { transformType2ToEma } from "./fhir/transform.js";
import type { EmaMapping } from "./fhir/mapping.js";
import type { FhirBundle, FhirResource, OperationOutcome } from "./fhir/types.js";
import { GcpEvidenceStore, type RunManifest, type SignedManifest } from "./gcp/evidence.js";
import { HealthcareApiClient } from "./gcp/healthcare.js";
import { GcpLineagePublisher } from "./gcp/lineage.js";
import { sha256 } from "./lib/hash.js";
import { log } from "./lib/logger.js";

const GLOBAL_TYPE2_PROFILE =
  "http://hl7.org/fhir/uv/emedicinal-product-info/StructureDefinition/Bundle-uv-epi";

export type PipelineInput = {
  runId?: string;
  source: FhirBundle;
  sourceKind: "fixture" | "healthcare-api";
  sourceResource: string;
};

export type PipelineResult = {
  runId: string;
  status: RunManifest["status"];
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
};

function countErrors(outcomes: OperationOutcome[]): number {
  return outcomes.reduce(
    (total, outcome) =>
      total +
      outcome.issue.filter(({ severity }) => severity === "fatal" || severity === "error").length,
    0,
  );
}

export async function runPipeline(
  input: PipelineInput,
  mapping: EmaMapping,
  config: AppConfig,
): Promise<PipelineResult> {
  const runId = input.runId ?? randomUUID();
  const startedAt = new Date().toISOString();
  const stageStarted = Date.now();
  log("info", "ePI interoperability run started", {
    runId,
    stage: "pipeline",
    dryRun: config.DRY_RUN,
  });

  const sourcePreflight = validateType2Preflight(input.source);
  if (hasValidationErrors(sourcePreflight)) {
    log("warning", "Canonical Type 2 preflight rejected", {
      runId,
      stage: "source-preflight",
      errorCount: countErrors([sourcePreflight]),
    });
    throw new Error("Canonical Type 2 preflight failed");
  }

  const transformed = transformType2ToEma(input.source, mapping);
  const emaPreflight = validateEmaPreflight(transformed.list, transformed.documentBundle, mapping);
  if (hasValidationErrors(emaPreflight)) {
    log("warning", "EMA preflight rejected", {
      runId,
      stage: "ema-preflight",
      errorCount: countErrors([emaPreflight]),
    });
    throw new Error("EMA structural preflight failed");
  }

  const cloudOutcomes: OperationOutcome[] = [];
  const officialOutcomes: OperationOutcome[] = [];
  let transactionResponseHash: string | undefined;
  const artifactUris: string[] = [];

  if (!config.DRY_RUN) {
    const validatorUrl = config.FHIR_VALIDATOR_URL;
    if (validatorUrl === undefined) throw new Error("FHIR_VALIDATOR_URL is required");
    const officialValidator = new OfficialFhirValidatorClient(validatorUrl);
    officialOutcomes.push(await officialValidator.validate(input.source, [GLOBAL_TYPE2_PROFILE]));
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
    cloudOutcomes.push(await healthcare.validate(input.source, GLOBAL_TYPE2_PROFILE, runId));
    cloudOutcomes.push(await healthcare.validate(transformed.list, mapping.profiles.list, runId));
    cloudOutcomes.push(
      await healthcare.validate(transformed.documentBundle, mapping.profiles.bundle, runId),
    );
    const composition = transformed.documentBundle.entry[0]?.resource;
    if (composition === undefined) throw new Error("Transformed Composition is missing");
    for (const profile of mapping.profiles.composition) {
      cloudOutcomes.push(await healthcare.validate(composition, profile, runId));
    }
    if (cloudOutcomes.some(hasValidationErrors)) {
      throw new Error("Cloud Healthcare API profile validation failed");
    }

    const transactionResponse = await healthcare.persistPackage(
      transformed.list,
      transformed.documentBundle,
      runId,
    );
    transactionResponseHash = sha256(transactionResponse);
  }

  const completedAt = new Date().toISOString();
  const manifest: RunManifest = {
    schemaVersion: "1.0.0",
    runId,
    startedAt,
    completedAt,
    status: config.DRY_RUN ? "validated" : "persisted",
    dryRun: config.DRY_RUN,
    source: {
      kind: input.sourceKind,
      resource: input.sourceResource,
      hash: transformed.inputHash,
    },
    standards: {
      fhir: "5.0.0",
      globalEpiPackage:
        process.env.GLOBAL_EPI_PACKAGE ?? "hl7.fhir.uv.emedicinal-product-info#1.0.0",
      emaPackage: "EUePI#1.0.0",
      qrdTemplate: "10.4",
      mappingVersion: mapping.mappingVersion,
    },
    validation: {
      preflightErrors: countErrors([sourcePreflight, emaPreflight]),
      officialValidationExecuted: !config.DRY_RUN,
      officialProfileErrors: countErrors(officialOutcomes),
      cloudValidationExecuted: !config.DRY_RUN,
      cloudProfileErrors: countErrors(cloudOutcomes),
      profiles: [
        GLOBAL_TYPE2_PROFILE,
        mapping.profiles.list,
        mapping.profiles.bundle,
        ...mapping.profiles.composition,
      ],
    },
    transformation: {
      inputHash: transformed.inputHash,
      outputHash: transformed.outputHash,
      decisions: transformed.mappingDecisions.length,
    },
    ...(transactionResponseHash === undefined
      ? {}
      : {
          persistence: {
            targetStore: config.TARGET_FHIR_STORE_ID ?? "unknown",
            transactionResponseHash,
          },
        }),
    runtime: {
      sourceCommit: process.env.GIT_COMMIT ?? "development",
      imageDigest: process.env.IMAGE_DIGEST ?? "development",
      workflowRevision: process.env.WORKFLOW_REVISION ?? process.env.K_REVISION ?? "development",
    },
  };

  let evidence: SignedManifest = {
    manifest,
    manifestHash: sha256(manifest),
  };
  if (!config.DRY_RUN) {
    const store = new GcpEvidenceStore(config);
    artifactUris.push(await store.writeJson(runId, "source-type2", input.source));
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
    evidence = await store.signManifest(manifest);
    artifactUris.push(await store.writeJson(runId, "signed-manifest", evidence));
    await store.writeLedger(evidence);

    const project = config.GOOGLE_CLOUD_PROJECT ?? "unknown";
    const dataset = config.HEALTHCARE_DATASET_ID ?? "unknown";
    const sourceStore = config.SOURCE_FHIR_STORE_ID ?? "unknown";
    const targetStore = config.TARGET_FHIR_STORE_ID ?? "unknown";
    const lineage = new GcpLineagePublisher(config);
    const lineageResources = await lineage.publish({
      runId,
      startedAt,
      completedAt,
      sourceFqn:
        input.sourceKind === "healthcare-api"
          ? `healthcare:${project}.${config.GCP_LOCATION}.${dataset}.${sourceStore}.${input.sourceResource.replace("/", ".")}`
          : `custom:ema-flow.${input.sourceResource}`,
      targetFhirFqn: `healthcare:${project}.${config.GCP_LOCATION}.${dataset}.${targetStore}.Bundle.${transformed.documentBundle.id ?? "unknown"}`,
      targetBigQueryFqn: `bigquery:${project}.${config.FHIR_ANALYTICS_DATASET ?? "unknown"}.Bundle`,
      inputHash: transformed.inputHash,
      outputHash: transformed.outputHash,
    });
    artifactUris.push(await store.writeJson(runId, "lineage-resources", lineageResources));
  }

  log("info", "ePI interoperability run completed", {
    runId,
    stage: "pipeline",
    outcome: manifest.status,
    durationMs: Date.now() - stageStarted,
    mappingDecisions: transformed.mappingDecisions.length,
  });

  return {
    runId,
    status: manifest.status,
    emaList: transformed.list,
    emaBundle: transformed.documentBundle,
    mappingDecisions: transformed.mappingDecisions,
    outcomes: {
      sourcePreflight,
      emaPreflight,
      official: officialOutcomes,
      cloud: cloudOutcomes,
    },
    evidence,
    artifactUris,
  };
}
