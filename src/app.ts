import { Hono } from "hono";
import { z } from "zod";

import { loadConfig } from "./config.js";
import { loadEmaMapping } from "./fhir/mapping.js";
import type { FhirBundle } from "./fhir/types.js";
import { createSyntheticType2Bundle } from "./fixtures/synthetic.js";
import { HealthcareApiClient } from "./gcp/healthcare.js";
import { log } from "./lib/logger.js";
import { runPipeline } from "./pipeline.js";

const RunRequestSchema = z.discriminatedUnion("source", [
  z.object({
    source: z.literal("fixture"),
    runId: z.uuid().optional(),
  }),
  z.object({
    source: z.literal("healthcare-api"),
    bundleId: z.string().min(1),
    runId: z.uuid().optional(),
  }),
]);

export function createApp(): Hono {
  const app = new Hono();
  const config = loadConfig();
  const mappingPromise = loadEmaMapping();

  app.get("/healthz", (context) =>
    context.json({
      status: "ok",
      service: "ema-flow",
      dryRun: config.DRY_RUN,
      timestamp: new Date().toISOString(),
    }),
  );

  app.post("/v1/runs", async (context) => {
    const parsed = RunRequestSchema.safeParse(await context.req.json());
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

    const mapping = await mappingPromise;
    let source: FhirBundle;
    let sourceResource: string;

    if (parsed.data.source === "fixture") {
      source = createSyntheticType2Bundle(mapping);
      sourceResource = "fixture:synthetic-type2-smpc";
    } else {
      const healthcare = new HealthcareApiClient(config);
      source = await healthcare.readSourceResource<FhirBundle>(
        "Bundle",
        parsed.data.bundleId,
        parsed.data.runId ?? crypto.randomUUID(),
      );
      sourceResource = `Bundle/${parsed.data.bundleId}`;
    }

    const result = await runPipeline(
      {
        ...(parsed.data.runId === undefined ? {} : { runId: parsed.data.runId }),
        source,
        sourceKind: parsed.data.source === "fixture" ? "fixture" : "healthcare-api",
        sourceResource,
      },
      mapping,
      config,
    );

    return context.json({
      runId: result.runId,
      status: result.status,
      manifestHash: result.evidence.manifestHash,
      signature: result.evidence.signature,
      targetBundleId: result.emaBundle.id,
      mappingDecisions: result.mappingDecisions.length,
      validation: result.evidence.manifest.validation,
      artifacts: result.artifactUris,
    });
  });

  app.onError((error, context) => {
    log("error", "Request failed", {
      stage: "http",
      errorType: error.name,
    });
    return context.json(
      {
        error: "pipeline-failed",
        message: error.message,
      },
      500,
    );
  });

  return app;
}
