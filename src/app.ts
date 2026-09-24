import { Hono } from "hono";

import { loadConfig, type AppConfig } from "./config.js";
import { RunRequestSchema, SubmissionRejectedError } from "./contracts/index.js";
import { loadEmaMapping } from "./fhir/mapping.js";
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

export type AppOverrides = {
  config?: AppConfig;
  submissionReader?: SubmissionReader;
};

export function createApp(overrides: AppOverrides = {}): Hono {
  const app = new Hono();
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
    // Which of this service's own gates threw, as a closed code. The map is keyed by the exact
    // message literals thrown in src/pipeline.ts, src/gcp/healthcare.ts and src/gcp/evidence.ts,
    // so nothing derived from an upstream response body can reach it: a message this service
    // did not write is `unclassified`. "pipeline-failed" alone told an operator only that
    // something threw, which in a system whose product is traceable evidence is not enough.
    function pipelineFailureReason(failure: Error): string {
      // An upstream refusal carries its own closed operation, so it is classified by type rather
      // than by message: the message embeds a status and a hash and could never match a literal.
      // The first run to reach persistence answered `unclassified` for exactly this reason, and
      // finding out which upstream had refused meant reading Cloud Audit Logs by hand.
      if (failure instanceof HealthcareApiError) {
        return `healthcare-${failure.operation}-refused`;
      }
      const reasons: Record<string, string> = {
        "runId must be a UUID": "bad-run-id",
        "Canonical preflight failed": "source-preflight-failed",
        "EMA structural preflight failed": "ema-preflight-failed",
        "Source identifier is in the reserved authority-import namespace": "reserved-namespace",
        "Ingestion Provenance requires an id": "provenance-id-missing",
        "FHIR_VALIDATOR_URL is required": "validator-not-configured",
        "Transformed Composition is missing": "composition-missing",
        "Official HL7 FHIR profile validation failed": "official-validation-failed",
        "Cloud Healthcare API profile validation failed": "cloud-validation-failed",
        "GOOGLE_CLOUD_PROJECT is required": "project-not-configured",
        "EVIDENCE_BUCKET is required": "evidence-bucket-not-configured",
        "Cloud KMS returned no manifest signature": "kms-no-signature",
        "KMS_MANIFEST_KEY must name a crypto key version": "kms-key-not-a-version",
        "Healthcare API project and dataset configuration are required":
          "healthcare-not-configured",
        "Application Default Credentials returned no access token": "no-access-token",
        "SOURCE_FHIR_STORE_ID is required": "source-store-not-configured",
        "TARGET_FHIR_STORE_ID is required": "target-store-not-configured",
      };
      return reasons[failure.message] ?? "unclassified";
    }

    if (error instanceof SubmissionReadError) {
      log("error", "Submission reference could not be read", {
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
    const rejected = error instanceof SubmissionRejectedError;
    log("error", "Request failed", {
      stage: "http",
      errorType: error.name,
      ...(rejected ? { errorCount: error.issues.length } : {}),
      ...(rejected ? {} : { reason: pipelineFailureReason(error) }),
    });
    return context.json(
      {
        error: rejected ? "submission-rejected" : "pipeline-failed",
        errorType: error.name,
        ...(rejected ? {} : { reason: pipelineFailureReason(error) }),
      },
      rejected ? 422 : 500,
    );
  });

  return app;
}
