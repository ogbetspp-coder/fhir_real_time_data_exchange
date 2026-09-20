import { beforeAll, describe, expect, it } from "vitest";

import { loadConfig, type AppConfig } from "../../src/config.js";
import {
  AnyRunManifestSchema,
  RUN_MANIFEST_VERSION,
  type RunManifest,
  type RunManifestV1,
} from "../../src/contracts/index.js";
import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import { createSyntheticSubmission } from "../../src/fixtures/synthetic-submission.js";
import { runPipeline } from "../../src/pipeline.js";

const DOCUMENT_RUN_ID = "44444444-4444-4444-a444-444444444444";

// A ledger row written before the ingestion block existed. ADR 0002 requires it to stay readable.
const LEGACY_MANIFEST: RunManifestV1 = {
  schemaVersion: "1.0.0",
  source: {
    kind: "healthcare-api",
    resource: "Bundle/legacy-type2",
    hash: "a".repeat(64),
  },
  runId: "33333333-3333-4333-a333-333333333333",
  startedAt: "2026-01-01T00:00:00Z",
  completedAt: "2026-01-01T00:00:05Z",
  status: "persisted",
  dryRun: false,
  standards: {
    fhir: "5.0.0",
    globalEpiPackage: "hl7.fhir.uv.emedicinal-product-info#1.0.0",
    emaPackage: "EUePI#1.0.0",
    qrdTemplate: "10.4",
    mappingVersion: "1.0.0",
  },
  validation: {
    preflightErrors: 0,
    officialValidationExecuted: true,
    officialProfileErrors: 0,
    cloudValidationExecuted: true,
    cloudProfileErrors: 0,
    profiles: ["https://example.org/StructureDefinition/legacy"],
  },
  transformation: {
    inputHash: "b".repeat(64),
    outputHash: "c".repeat(64),
    decisions: 32,
  },
  persistence: {
    targetStore: "legacy-store",
    transactionResponseHash: "d".repeat(64),
  },
  runtime: {
    sourceCommit: "0000000",
    imageDigest: "sha256:0000000000000000000000000000000000000000000000000000000000000000",
    workflowRevision: "legacy-revision",
  },
};

let mapping: EmaMapping;
let config: AppConfig;
let manifest: RunManifest;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  config = loadConfig({ NODE_ENV: "test", DRY_RUN: "true", GCP_LOCATION: "europe-west4" });
  const { submission, fidelityReport, sourceText } = createSyntheticSubmission(mapping);
  const result = await runPipeline(
    {
      runId: DOCUMENT_RUN_ID,
      sourceKind: "document",
      submission,
      fidelityReport,
      sourceText,
      sourceResource: "document:synthetic-smpc",
    },
    mapping,
    config,
  );
  manifest = result.evidence.manifest;
});

function issues(value: unknown): string[] {
  const parsed = AnyRunManifestSchema.safeParse(value);
  expect(parsed.success).toBe(false);
  return parsed.success ? [] : parsed.error.issues.map(({ message }) => message);
}

describe("run manifest contract", () => {
  it("still reads a version 1.0.0 manifest", () => {
    const parsed = AnyRunManifestSchema.parse(LEGACY_MANIFEST);

    expect(parsed.schemaVersion).toBe("1.0.0");
    expect(parsed.source.kind).toBe("healthcare-api");
  });

  it("rejects an ingestion block on a version 1.0.0 manifest", () => {
    expect(issues({ ...LEGACY_MANIFEST, ingestion: manifest.ingestion })).toContain(
      'Unrecognized key: "ingestion"',
    );
  });

  it("reads the manifest the current pipeline emits for a document run", () => {
    const parsed = AnyRunManifestSchema.safeParse(manifest);

    expect(parsed.success).toBe(true);
    expect(manifest.schemaVersion).toBe(RUN_MANIFEST_VERSION);
    expect(manifest.source.kind).toBe("document");
    expect(manifest.ingestion?.contractVersion).toBe("1.0.0");
  });

  it("rejects a document run without an ingestion block", () => {
    const withoutIngestion: Record<string, unknown> = { ...manifest };
    delete withoutIngestion.ingestion;

    expect(issues(withoutIngestion)).toContain("document runs require an ingestion block");
  });

  it("rejects an ingestion block on a non-document run", () => {
    const asFixture = { ...manifest, source: { ...manifest.source, kind: "fixture" } };

    expect(issues(asFixture)).toContain("only document runs carry an ingestion block");
  });
});
