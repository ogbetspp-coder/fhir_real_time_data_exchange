import { describe, expect, it } from "vitest";

import { loadConfig } from "../src/config.js";
import { loadEmaMapping } from "../src/fhir/mapping.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { runPipeline } from "../src/pipeline.js";

describe("pipeline", () => {
  it("returns signed-evidence-ready validation metadata in dry-run mode", async () => {
    const mapping = await loadEmaMapping();
    const config = loadConfig({
      NODE_ENV: "test",
      DRY_RUN: "true",
      GCP_LOCATION: "europe-west4",
    });
    const result = await runPipeline(
      {
        runId: "11111111-1111-4111-a111-111111111111",
        source: createSyntheticType2Bundle(mapping),
        sourceKind: "fixture",
        sourceResource: "fixture:test",
      },
      mapping,
      config,
    );

    expect(result.status).toBe("validated");
    expect(result.artifactUris).toEqual([]);
    expect(result.evidence.signature).toBeUndefined();
    expect(result.evidence.manifest.validation.preflightErrors).toBe(0);
    expect(result.evidence.manifest.validation.officialValidationExecuted).toBe(false);
    expect(result.evidence.manifest.validation.officialProfileErrors).toBe(0);
    expect(result.evidence.manifest.validation.cloudValidationExecuted).toBe(false);
    expect(result.evidence.manifest.validation.cloudProfileErrors).toBe(0);
    expect(result.evidence.manifest.transformation.decisions).toBe(32);
  });
});
