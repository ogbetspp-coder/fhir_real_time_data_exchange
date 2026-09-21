import { z } from "zod";

import { Sha256Hex, Uuid } from "../../src/contracts/common.js";
import type { RunRequest } from "../../src/contracts/index.js";
import type { EmaMapping } from "../../src/fhir/mapping.js";
import {
  DEFAULT_SYNTHETIC_PRODUCT_ID,
  DEMONSTRATION_PRODUCT_IDS,
  type SyntheticProductId,
  type SyntheticVersion,
} from "../../src/fixtures/synthetic-products.js";
import { createSyntheticSubmission } from "../../src/fixtures/synthetic-submission.js";
import { sha256 } from "../../src/lib/hash.js";

// The pure half of `scripts/demo/seed.ts`: object naming, payload construction, request shaping,
// and response parsing. It performs no I/O, so `test/demo-seed.test.ts` can hold every one of
// these decisions without a network or a bucket.

export const DEMO_OBJECT_FILES = {
  sourceText: "source-document-text.json",
  fidelityReport: "fidelity-report.json",
  submission: "canonical-submission.json",
} as const;

export type DemoPart = keyof typeof DEMO_OBJECT_FILES;

// Write order, and it matters: the submission names the other two parts by URI and hash, so it
// is written last and a reader never resolves a submission whose parts are not there yet.
const WRITE_ORDER: readonly DemoPart[] = ["sourceText", "fidelityReport", "submission"];

export function demoObjectName(
  productId: SyntheticProductId,
  version: SyntheticVersion,
  part: DemoPart,
): string {
  return `demo/${productId}/v${version}/${DEMO_OBJECT_FILES[part]}`;
}

export function demoObjectUri(
  bucket: string,
  productId: SyntheticProductId,
  version: SyntheticVersion,
  part: DemoPart,
): string {
  return `gs://${bucket}/${demoObjectName(productId, version, part)}`;
}

export type DemoObject = {
  part: DemoPart;
  name: string;
  uri: string;
  value: unknown;
};

export type DemoSeedStep = {
  productId: SyntheticProductId;
  version: SyntheticVersion;
  objects: DemoObject[];
  submissionSha256: string;
  request: RunRequest;
};

export type DemoSeedTarget = {
  productId: SyntheticProductId;
  version: SyntheticVersion;
};

function target(productId: SyntheticProductId, version: SyntheticVersion): DemoSeedTarget {
  return { productId, version };
}

// All products at version 1, then the default product at version 2. Fixed, because the
// demonstration's second scene is "the same label, one sentence later", and that only reads as a
// version if version 1 was published first.
export const DEMO_SEED_ORDER: readonly DemoSeedTarget[] = [
  ...DEMONSTRATION_PRODUCT_IDS.map((productId) => target(productId, 1)),
  target(DEFAULT_SYNTHETIC_PRODUCT_ID, 2),
];

export function buildSeedStep(
  mapping: EmaMapping,
  bucket: string,
  productId: SyntheticProductId,
  version: SyntheticVersion,
): DemoSeedStep {
  const uri = (part: DemoPart): string => demoObjectUri(bucket, productId, version, part);
  // The two by-reference URIs sit inside the approved content, so they are set before the
  // fixture hashes anything: a submission that points somewhere else is a different submission.
  const fixture = createSyntheticSubmission(mapping, {
    product: productId,
    version,
    partUris: { fidelityReport: uri("fidelityReport"), sourceText: uri("sourceText") },
  });
  const values: Record<DemoPart, unknown> = {
    sourceText: fixture.sourceText,
    fidelityReport: fixture.fidelityReport,
    submission: fixture.submission,
  };

  return {
    productId,
    version,
    objects: WRITE_ORDER.map((part) => ({
      part,
      name: demoObjectName(productId, version, part),
      uri: uri(part),
      value: values[part],
    })),
    submissionSha256: sha256(fixture.submission),
    request: {
      source: "document",
      submissionRef: { uri: uri("submission"), sha256: sha256(fixture.submission) },
    },
  };
}

export function buildSeedPlan(mapping: EmaMapping, bucket: string): DemoSeedStep[] {
  return DEMO_SEED_ORDER.map(({ productId, version }) =>
    buildSeedStep(mapping, bucket, productId, version),
  );
}

// Only the fields the seed prints. The worker answers with more; nothing else is read, so
// nothing else can reach the console.
const RunResponseSchema = z.looseObject({
  runId: Uuid,
  status: z.string().min(1).max(64),
  manifestHash: Sha256Hex,
  targetBundleId: z.string().min(1).max(64),
});

export type DemoRunOutcome = {
  runId: string;
  status: string;
  manifestHash: string;
  targetBundleId: string;
};

export function parseRunResponse(body: unknown): DemoRunOutcome {
  const parsed = RunResponseSchema.safeParse(body);
  if (!parsed.success) {
    throw new Error(`Worker response is not a run result (${String(parsed.error.issues.length)})`);
  }
  const { runId, status, manifestHash, targetBundleId } = parsed.data;
  return { runId, status, manifestHash, targetBundleId };
}

export type DemoSeedRecord = DemoRunOutcome & {
  productId: SyntheticProductId;
  version: SyntheticVersion;
};

export type DemoSeedDeps = {
  putObject(objectName: string, value: unknown): Promise<void>;
  startRun(request: RunRequest): Promise<DemoRunOutcome>;
};

export async function seedStep(step: DemoSeedStep, deps: DemoSeedDeps): Promise<DemoSeedRecord> {
  for (const object of step.objects) {
    await deps.putObject(object.name, object.value);
  }
  const outcome = await deps.startRun(step.request);
  return { productId: step.productId, version: step.version, ...outcome };
}

// Identifiers, counts, and hashes only — never a line of the document it just published.
export function seedRecordLine(record: DemoSeedRecord): string {
  return JSON.stringify(record);
}

export function runsEndpoint(workerUrl: string): string {
  return `${workerUrl.replace(/\/+$/, "")}/v1/runs`;
}

export function dryRunLines(step: DemoSeedStep, workerUrl: string): string[] {
  return [
    ...step.objects.map((object) => `would-write ${object.uri}`),
    `would-post ${runsEndpoint(workerUrl)} ${step.productId} v${step.version} ${step.submissionSha256}`,
  ];
}
