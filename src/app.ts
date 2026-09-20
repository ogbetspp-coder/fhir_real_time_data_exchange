import { Hono } from "hono";

import { loadConfig, type AppConfig } from "./config.js";
import { RunRequestSchema, SubmissionRejectedError } from "./contracts/index.js";
import { loadEmaMapping } from "./fhir/mapping.js";
import type { FhirBundle } from "./fhir/types.js";
import { createSyntheticType2Bundle } from "./fixtures/synthetic.js";
import { HealthcareApiClient } from "./gcp/healthcare.js";
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
    if (request.source === "document" && submissionReader === undefined) {
      return context.json({ error: "document-source-not-configured" }, 503);
    }

    // One correlation id for the whole request, so the reference read and the source fetch log
    // under the same runId as the pipeline they feed.
    const runId = request.runId ?? crypto.randomUUID();
    const mapping = await mappingPromise;
    let input: PipelineInput;

    if (request.source === "fixture") {
      input = {
        runId,
        sourceKind: "fixture",
        source: createSyntheticType2Bundle(mapping),
        sourceResource: "fixture:synthetic-type2-smpc",
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
    });
    return context.json(
      { error: rejected ? "submission-rejected" : "pipeline-failed", errorType: error.name },
      rejected ? 422 : 500,
    );
  });

  return app;
}
