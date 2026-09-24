import { beforeAll, describe, expect, it } from "vitest";

import { StorageUri } from "../src/contracts/common.js";
import { RunRequestSchema, verifyDocumentSubmission } from "../src/contracts/index.js";
import type { RunRequest } from "../src/contracts/index.js";
import { loadEmaMapping, type EmaMapping } from "../src/fhir/mapping.js";
import { sha256 } from "../src/lib/hash.js";
import {
  DEMO_SEED_ORDER,
  buildSeedPlan,
  buildSeedStep,
  demoObjectName,
  dryRunLines,
  parseRunResponse,
  runsEndpoint,
  seedRecordLine,
  seedStep,
  type DemoRunOutcome,
  type DemoSeedDeps,
} from "../scripts/demo/seed-plan.js";
import { SYNTHETIC } from "./support/submission.js";

// The seed script's decisions without the cloud: object naming, what goes inside the approved
// content, the request it posts, the order it does things in, and what it prints.

const BUCKET = "demo-submissions";
const WORKER_URL = "https://worker.example.run.app";

const OUTCOME: DemoRunOutcome = {
  runId: "11111111-1111-4111-a111-111111111111",
  status: "validated",
  manifestHash: "a".repeat(64),
  targetBundleId: "6f0ad2ff-5a2b-4cbb-a2ad-1b7e6d6a5c4f",
};

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

function recordingDeps(): { deps: DemoSeedDeps; writes: string[]; requests: RunRequest[] } {
  const writes: string[] = [];
  const requests: RunRequest[] = [];
  return {
    writes,
    requests,
    deps: {
      putObject: (objectName) => {
        writes.push(objectName);
        return Promise.resolve();
      },
      startRun: (request) => {
        requests.push(request);
        return Promise.resolve(OUTCOME);
      },
    },
  };
}

describe("demo seed plan", () => {
  it("names one folder per product and version", () => {
    expect(demoObjectName("synthetic-paracetamol", 1, "submission")).toBe(
      "demo/synthetic-paracetamol/v1/canonical-submission.json",
    );
    expect(demoObjectName("synthetic-demoxetine", 2, "fidelityReport")).toBe(
      "demo/synthetic-demoxetine/v2/fidelity-report.json",
    );
    expect(demoObjectName("synthetic-placebolol", 1, "sourceText")).toBe(
      "demo/synthetic-placebolol/v1/source-document-text.json",
    );
  });

  it("publishes all products at version 1 before the second version of the first", () => {
    expect(DEMO_SEED_ORDER).toEqual([
      { productId: "synthetic-paracetamol", version: 1 },
      { productId: "synthetic-demoxetine", version: 1 },
      { productId: "synthetic-placebolol", version: 1 },
      { productId: "synthetic-paracetamol", version: 2 },
    ]);
    expect(buildSeedPlan(mapping, BUCKET)).toHaveLength(DEMO_SEED_ORDER.length);
  });

  it("points the submission at the objects it will actually be stored beside", () => {
    const step = buildSeedStep(mapping, BUCKET, "synthetic-demoxetine", 2);
    const objects = new Map(step.objects.map((object) => [object.part, object]));
    const submission = objects.get("submission")?.value as {
      provenance: {
        sourceDocument: { extractedText: { uri: string } };
        fidelity: { reportUri: string };
      };
    };

    expect(submission.provenance.sourceDocument.extractedText.uri).toBe(
      objects.get("sourceText")?.uri,
    );
    expect(submission.provenance.fidelity.reportUri).toBe(objects.get("fidelityReport")?.uri);
    for (const object of step.objects) {
      expect([object.part, StorageUri.safeParse(object.uri).success]).toEqual([object.part, true]);
      expect(object.uri).toBe(`gs://${BUCKET}/${object.name}`);
    }
  });

  it("shapes a document run request the published contract accepts", () => {
    const step = buildSeedStep(mapping, BUCKET, "synthetic-paracetamol", 1);
    const parsed = RunRequestSchema.safeParse(step.request);

    expect(parsed.success).toBe(true);
    expect(step.request).toEqual({
      source: "document",
      submissionRef: {
        uri: `gs://${BUCKET}/demo/synthetic-paracetamol/v1/canonical-submission.json`,
        sha256: step.submissionSha256,
      },
    });
    const submission = step.objects.find(({ part }) => part === "submission")?.value;
    expect(sha256(submission)).toBe(step.submissionSha256);
  });

  it("writes a submission the ingress gate still accepts at its demo location", () => {
    const step = buildSeedStep(mapping, BUCKET, "synthetic-placebolol", 1);
    const value = (part: string): unknown =>
      step.objects.find((object) => object.part === part)?.value;

    expect(() =>
      verifyDocumentSubmission(
        {
          submission: value("submission"),
          fidelityReport: value("fidelityReport"),
          sourceText: value("sourceText"),
        },
        mapping.sourceCodeSystem,
        SYNTHETIC,
      ),
    ).not.toThrow();
  });

  it("writes the referenced parts before the submission that names them", async () => {
    const step = buildSeedStep(mapping, BUCKET, "synthetic-paracetamol", 2);
    const { deps, writes, requests } = recordingDeps();

    const record = await seedStep(step, deps);

    expect(writes).toEqual([
      "demo/synthetic-paracetamol/v2/source-document-text.json",
      "demo/synthetic-paracetamol/v2/fidelity-report.json",
      "demo/synthetic-paracetamol/v2/canonical-submission.json",
    ]);
    expect(requests).toEqual([step.request]);
    expect(record).toEqual({ productId: "synthetic-paracetamol", version: 2, ...OUTCOME });
  });

  it("prints identifiers, a status, and hashes, and nothing else", () => {
    const line: unknown = JSON.parse(
      seedRecordLine({ productId: "synthetic-demoxetine", version: 1, ...OUTCOME }),
    );

    expect(Object.keys(line as Record<string, unknown>).sort()).toEqual([
      "manifestHash",
      "productId",
      "runId",
      "status",
      "targetBundleId",
      "version",
    ]);
  });

  it("describes a dry run without naming anything but locations and hashes", () => {
    const step = buildSeedStep(mapping, BUCKET, "synthetic-demoxetine", 1);
    const lines = dryRunLines(step, `${WORKER_URL}/`);

    expect(lines).toEqual([
      `would-write gs://${BUCKET}/demo/synthetic-demoxetine/v1/source-document-text.json`,
      `would-write gs://${BUCKET}/demo/synthetic-demoxetine/v1/fidelity-report.json`,
      `would-write gs://${BUCKET}/demo/synthetic-demoxetine/v1/canonical-submission.json`,
      `would-post ${WORKER_URL}/v1/runs synthetic-demoxetine v1 ${step.submissionSha256}`,
    ]);
  });

  it("builds the runs endpoint from a worker URL with or without a trailing slash", () => {
    expect(runsEndpoint(WORKER_URL)).toBe(`${WORKER_URL}/v1/runs`);
    expect(runsEndpoint(`${WORKER_URL}//`)).toBe(`${WORKER_URL}/v1/runs`);
  });

  it("reads only the four fields it prints out of a worker response", () => {
    expect(parseRunResponse({ ...OUTCOME, mappingDecisions: 32, artifacts: ["gs://x/y"] })).toEqual(
      OUTCOME,
    );
    expect(() => parseRunResponse({ ...OUTCOME, manifestHash: "not-a-hash" })).toThrow();
    expect(() => parseRunResponse({ runId: OUTCOME.runId })).toThrow();
  });
});
