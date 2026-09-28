import { beforeAll, describe, expect, it, vi } from "vitest";

import { loadConfig, type AppConfig } from "../src/config.js";
import { loadEmaMapping, type EmaMapping } from "../src/fhir/mapping.js";
import { createSyntheticSubmission } from "../src/fixtures/synthetic-submission.js";
import { runPipeline } from "../src/pipeline.js";

// The run manifest is signed, and its fidelity status is the report's own. The gate refuses a
// report that did not pass (test/contracts/canonical-submission.test.ts); this file takes that
// check away to show the manifest never states `passed` on the gate's behalf.

vi.mock("../src/contracts/index.js", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/contracts/index.js")>();
  return {
    ...actual,
    verifyDocumentSubmission: (...args: Parameters<typeof actual.verifyDocumentSubmission>) => {
      const result = actual.verifyDocumentSubmission(...args);
      return { ...result, report: { ...result.report, status: "failed" } };
    },
  };
});

let mapping: EmaMapping;
let config: AppConfig;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  config = loadConfig({
    ALLOW_SYNTHETIC_SOURCES: "true",
    NODE_ENV: "test",
    DRY_RUN: "true",
    GCP_LOCATION: "europe-west4",
  });
});

describe("the run manifest's fidelity status", () => {
  it("is never passed for a report that failed, even past the gate", async () => {
    const { submission, fidelityReport, sourceText } = createSyntheticSubmission(mapping);

    await expect(
      runPipeline(
        {
          runId: "33333333-3333-4333-a333-333333333333",
          sourceKind: "document",
          submission,
          fidelityReport,
          sourceText,
          sourceResource: "document:synthetic-smpc",
        },
        mapping,
        config,
      ),
    ).rejects.toThrow("Only a passed fidelity report reaches run evidence");
  });
});
