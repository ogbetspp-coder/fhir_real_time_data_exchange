import { Hono } from "hono";

import { loadConfig, type AppConfig } from "./config.js";
import { RunRequestSchema, SubmissionRejectedError } from "./contracts/index.js";
import { loadEmaMapping } from "./fhir/mapping.js";
import { OfficialValidatorError } from "./fhir/official-validator.js";
import { TransformationError } from "./fhir/transform.js";
import type { FhirBundle } from "./fhir/types.js";
import { SMOKE_PRODUCT_ID } from "./fixtures/synthetic-products.js";
import { createSyntheticType2Bundle } from "./fixtures/synthetic.js";
import { HealthcareApiClient, HealthcareApiError } from "./gcp/healthcare.js";
import {
  GcsSubmissionReader,
  SubmissionReadError,
  type SubmissionReader,
} from "./gcp/submission-reader.js";
import { log } from "./lib/logger.js";
import { runPipeline, type PipelineInput } from "./pipeline.js";

// Which of this service's own gates threw, as a closed code. Keyed by the exact message literals
// thrown in src/pipeline.ts, src/gcp/ and src/fhir/official-validator.ts, so nothing derived from
// an upstream response body can reach it: a message this service did not write is
// `unclassified`. test/failure-reasons.test.ts holds every literal thrown there to an entry.
export const FAILURE_REASONS: Readonly<Record<string, string>> = {
  "runId must be a UUID": "bad-run-id",
  "Canonical preflight failed": "source-preflight-failed",
  "EMA structural preflight failed": "ema-preflight-failed",
  "Source identifier is in the reserved authority-import namespace": "reserved-namespace",
  "The EMA document Bundle and Composition require ids": "document-ids-missing",
  "Ingestion Provenance requires an id": "provenance-id-missing",
  "Only a passed fidelity report reaches run evidence": "fidelity-not-passed",
  "FHIR_VALIDATOR_URL is required": "validator-not-configured",
  "Transformed Composition is missing": "composition-missing",
  "Official HL7 FHIR profile validation failed": "official-validation-failed",
  "Official FHIR validator request timed out": "official-validator-timeout",
  "Official FHIR validator returned an invalid OperationOutcome":
    "official-validator-invalid-response",
  "Cloud Healthcare API profile validation failed": "cloud-validation-failed",
  "GOOGLE_CLOUD_PROJECT is required": "project-not-configured",
  "EVIDENCE_BUCKET is required": "evidence-bucket-not-configured",
  "Run evidence already exists for this run id": "run-id-reused",
  "Cloud Storage refused an evidence write": "evidence-write-refused",
  "The run committed but its record could not be written": "committed-unrecorded",
  "The persisted resources reference each other in a cycle": "reference-cycle",
  "KMS_MANIFEST_KEY is required": "kms-key-not-configured",
  "KMS_MANIFEST_KEY must name a crypto key version": "kms-key-not-a-version",
  "Cloud KMS returned no manifest signature": "kms-no-signature",
  "Cloud KMS did not verify the manifest digest's checksum": "kms-digest-checksum-failed",
  "Cloud KMS signed with a key version other than the configured one": "kms-wrong-key-version",
  "Cloud KMS returned a signature that fails its checksum": "kms-signature-checksum-failed",
  "TRANSFORMATION_LEDGER_DATASET is required": "ledger-not-configured",
  "Every persisted resource requires an id for idempotent persistence": "resource-id-missing",
  "A persisted reference names no resource in the transaction": "unresolved-reference",
  "Healthcare API project and dataset configuration are required": "healthcare-not-configured",
  "Application Default Credentials returned no access token": "no-access-token",
  "Healthcare API request timed out": "healthcare-timeout",
  "Healthcare API returned a response that is not a JSON object": "healthcare-invalid-response",
  "SOURCE_FHIR_STORE_ID is required": "source-store-not-configured",
  "A source resource id must be a single path segment": "source-id-not-a-segment",
  "TARGET_FHIR_STORE_ID is required": "target-store-not-configured",
  "Data Lineage API returned no process name": "lineage-no-process",
  "Data Lineage API returned no run name": "lineage-no-run",
  "SUBMISSION_BUCKET is required": "submission-bucket-not-configured",
  "Submission reader is unavailable": "submission-reader-unavailable",
};

// Codes that are the caller's to act on rather than a fault here, with the status they answer.
// A crosswalk refusal is the submission's content (a non-English source, an unmapped section, a
// missing identifier); a reused runId is a replay, which must not be retried as it is.
const FAILURE_STATUS: Readonly<Record<string, 409 | 422>> = {
  "crosswalk-refused": 422,
  "run-id-reused": 409,
};

export function pipelineFailure(failure: Error): { reason: string; status: 409 | 422 | 500 } {
  // An upstream refusal carries its own closed operation, so it is classified by type rather
  // than by message: the message embeds a status and a hash and could never match a literal.
  const reason =
    failure instanceof HealthcareApiError
      ? `healthcare-${failure.operation}-refused`
      : failure instanceof OfficialValidatorError
        ? "official-validator-refused"
        : failure instanceof TransformationError
          ? "crosswalk-refused"
          : (FAILURE_REASONS[failure.message] ?? "unclassified");
  return { reason, status: FAILURE_STATUS[reason] ?? 500 };
}

export type AppOverrides = {
  config?: AppConfig;
  submissionReader?: SubmissionReader;
};

// The request's runId, for the error handler's log line.
type AppEnvironment = { Variables: { runId?: string } };

export function createApp(overrides: AppOverrides = {}): Hono<AppEnvironment> {
  const app = new Hono<AppEnvironment>();
  const config = overrides.config ?? loadConfig();
  const mappingPromise = loadEmaMapping();
  const submissionReader =
    overrides.submissionReader ??
    (config.SUBMISSION_BUCKET === undefined ? undefined : new GcsSubmissionReader(config));

  app.get("/healthz", (context) =>
    context.json({
      status: "ok",
      service: "ema-flow",
      dryRun: config.DRY_RUN,
      documentSource: submissionReader !== undefined,
      timestamp: new Date().toISOString(),
    }),
  );

  app.post("/v1/runs", async (context) => {
    let payload: unknown;
    try {
      payload = await context.req.json();
    } catch {
      return context.json({ error: "invalid-json" }, 400);
    }
    const parsed = RunRequestSchema.safeParse(payload);
    if (!parsed.success) {
      return context.json(
        {
          error: "invalid-request",
          issues: parsed.error.issues.map(({ path, message }) => ({
            path: path.join("."),
            message,
          })),
        },
        400,
      );
    }

    const request = parsed.data;
    // The run-source allowlist (ADR 0002 consequences) is checked before any reader, fixture,
    // client, or store is touched: a disabled source does no work and leaves only this line.
    if (!config.ENABLED_RUN_SOURCES.includes(request.source)) {
      log("warning", "Run source is disabled", { stage: "http", source: request.source });
      return context.json({ error: "source-disabled" }, 422);
    }
    if (request.source === "document" && submissionReader === undefined) {
      return context.json({ error: "document-source-not-configured" }, 503);
    }

    // One correlation id for the whole request, so the reference read and the source fetch log
    // under the same runId as the pipeline they feed.
    const runId = request.runId ?? crypto.randomUUID();
    context.set("runId", runId);
    const mapping = await mappingPromise;
    let input: PipelineInput;

    if (request.source === "fixture") {
      // The smoke product, not the default one. A `fixture` run is what the deploy's smoke step
      // posts, and it persists a document like any other run; using a demonstration product here
      // meant every deploy wrote a fixture-sourced version over a label the demonstration is
      // about. This product exists only for this path (src/fixtures/synthetic-products.ts).
      input = {
        runId,
        sourceKind: "fixture",
        source: createSyntheticType2Bundle(mapping, { product: SMOKE_PRODUCT_ID }),
        sourceResource: `fixture:${SMOKE_PRODUCT_ID}`,
      };
    } else if (request.source === "healthcare-api") {
      const healthcare = new HealthcareApiClient(config);
      input = {
        runId,
        sourceKind: "healthcare-api",
        source: await healthcare.readSourceResource<FhirBundle>("Bundle", request.bundleId, runId),
        sourceResource: `Bundle/${request.bundleId}`,
      };
    } else {
      if (submissionReader === undefined) throw new Error("Submission reader is unavailable");
      input = {
        runId,
        sourceKind: "document",
        ...(await submissionReader.read(request.submissionRef, runId)),
        // The pinned hash identifies the submission without putting an object path, which a
        // caller chooses, into the manifest and from there into the ledger.
        sourceResource: `document:${request.submissionRef.sha256}`,
      };
    }

    const result = await runPipeline(input, mapping, config);

    return context.json({
      runId: result.runId,
      status: result.status,
      manifestHash: result.evidence.manifestHash,
      signature: result.evidence.signature,
      targetBundleId: result.emaBundle.id,
      // The version this run wrote, which the workflow's BigQuery check looks for.
      ...(result.persistedBundle === undefined
        ? {}
        : {
            targetBundleVersionId: result.persistedBundle.versionId,
            targetBundleLastUpdated: result.persistedBundle.lastUpdated,
          }),
      mappingDecisions: result.mappingDecisions.length,
      validation: result.evidence.manifest.validation,
      artifacts: result.artifactUris,
      ...(result.evidence.manifest.ingestion === undefined
        ? {}
        : { submissionId: result.evidence.manifest.ingestion.submissionId }),
    });
  });

  // Error messages can embed upstream response bodies (which may quote narrative), so the HTTP
  // surface returns only the error class; details stay in the evidence, never in a response.
  app.onError((error, context) => {
    const runId = context.get("runId");
    if (error instanceof SubmissionReadError) {
      log("error", "Submission reference could not be read", {
        ...(runId === undefined ? {} : { runId }),
        stage: "http",
        errorType: error.name,
        reason: error.reason,
        part: error.part,
      });
      // Both fields are closed enumerations, so the caller learns why without learning anything
      // about the document.
      return context.json(
        { error: "submission-unreadable", reason: error.reason, part: error.part },
        422,
      );
    }
    if (error instanceof SubmissionRejectedError) {
      log("error", "Request failed", {
        ...(runId === undefined ? {} : { runId }),
        stage: "http",
        errorType: error.name,
        errorCount: error.issues.length,
      });
      return context.json({ error: "submission-rejected", errorType: error.name }, 422);
    }
    const { reason, status } = pipelineFailure(error);
    log("error", "Request failed", {
      ...(runId === undefined ? {} : { runId }),
      stage: "http",
      errorType: error.name,
      reason,
    });
    return context.json({ error: "pipeline-failed", errorType: error.name, reason }, status);
  });

  return app;
}
