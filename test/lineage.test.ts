import { readFileSync } from "node:fs";
import path from "node:path";

import { beforeAll, describe, expect, it, vi } from "vitest";

import { loadConfig } from "../src/config.js";
import { loadEmaMapping, type EmaMapping } from "../src/fhir/mapping.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { runPipeline } from "../src/pipeline.js";

// Every Google client a persisted run touches is replaced, so the run reaches the lineage call
// without a project; the Data Lineage client itself is the one recording what it was sent.
const lineage = vi.hoisted(() => ({
  createProcess: vi.fn<(request: unknown) => Promise<{ name: string }[]>>(() =>
    Promise.resolve([{ name: "processes/p" }]),
  ),
  createRun: vi.fn(() => Promise.resolve([{ name: "processes/p/runs/r" }])),
  createLineageEvent: vi.fn(() => Promise.resolve([{ name: "processes/p/runs/r/events/e" }])),
}));

vi.mock("@google-cloud/lineage", () => ({
  LineageClient: class {
    public createProcess = lineage.createProcess;
    public createRun = lineage.createRun;
    public createLineageEvent = lineage.createLineageEvent;
  },
}));

const passed = { resourceType: "OperationOutcome", issue: [] };

vi.mock("../src/fhir/official-validator.js", () => ({
  OfficialFhirValidatorClient: class {
    public validate = (): Promise<typeof passed> => Promise.resolve(passed);
  },
}));

vi.mock("../src/gcp/healthcare.js", () => ({
  HealthcareApiClient: class {
    public validate = (): Promise<typeof passed> => Promise.resolve(passed);
    public persistPackage = (): Promise<object> => Promise.resolve({ resourceType: "Bundle" });
  },
}));

vi.mock("../src/gcp/evidence.js", () => ({
  GcpEvidenceStore: class {
    public writeJson = (runId: string, name: string): Promise<string> =>
      Promise.resolve(`gs://evidence/runs/${runId}/${name}`);
    public signManifest = (manifest: unknown): Promise<object> =>
      Promise.resolve({ manifest, manifestHash: "0".repeat(64) });
    public writeLedger = (): Promise<void> => Promise.resolve();
  },
}));

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

describe("lineage", () => {
  it("names the mapping version the loaded manifest declares, as the run manifest does", async () => {
    const config = loadConfig({
      ALLOW_SYNTHETIC_SOURCES: "true",
      NODE_ENV: "test",
      DRY_RUN: "false",
      GOOGLE_CLOUD_PROJECT: "synthetic-project",
      HEALTHCARE_DATASET_ID: "dataset",
      SOURCE_FHIR_STORE_ID: "source",
      TARGET_FHIR_STORE_ID: "target",
      EVIDENCE_BUCKET: "evidence",
      FHIR_VALIDATOR_URL: "http://validator.invalid",
      FHIR_ANALYTICS_DATASET: "analytics",
    });

    const result = await runPipeline(
      {
        runId: "33333333-3333-4333-a333-333333333333",
        source: createSyntheticType2Bundle(mapping),
        sourceKind: "fixture",
        sourceResource: "fixture:test",
      },
      mapping,
      config,
    );

    const published = JSON.parse(
      readFileSync(path.resolve("fhir/mappings/cap-smpc-en.json"), "utf8"),
    ) as { mappingVersion: string };
    expect(result.evidence.manifest.standards.mappingVersion).toBe(published.mappingVersion);
    expect(lineage.createProcess).toHaveBeenCalledTimes(1);
    const [request] = lineage.createProcess.mock.calls[0] ?? [];
    expect(request).toMatchObject({
      process: {
        attributes: { mapping: { stringValue: `cap-smpc-en#${published.mappingVersion}` } },
      },
    });
  });
});
