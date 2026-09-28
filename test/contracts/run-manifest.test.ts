import { readFileSync } from "node:fs";

import { beforeAll, describe, expect, it } from "vitest";

import { loadConfig, type AppConfig } from "../../src/config.js";
import {
  AnyRunManifestSchema,
  RunManifestSchema,
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
  config = loadConfig({
    ALLOW_SYNTHETIC_SOURCES: "true",
    NODE_ENV: "test",
    DRY_RUN: "true",
    GCP_LOCATION: "europe-west4",
  });
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
    expect(manifest.ingestion?.contractVersion).toBe("2.0.0");
  });

  it("rejects a document run without an ingestion block", () => {
    const withoutIngestion: Record<string, unknown> = { ...manifest };
    delete withoutIngestion.ingestion;

    expect(issues(withoutIngestion)).toContain("document runs require an ingestion block");
  });

  // 2.0.0 was signed after the transaction, over its response's hash; its rows stay readable.
  it("still reads a version 2.0.0 persisted manifest", () => {
    // Up to 3.0.0 the standards were literals and the validator's image was not recorded.
    const standards: Record<string, unknown> = { ...manifest.standards };
    delete standards.packages;
    const runtime: Record<string, unknown> = { ...manifest.runtime };
    delete runtime.validatorImageDigest;
    const v2 = {
      ...manifest,
      standards,
      runtime,
      schemaVersion: "2.0.0",
      status: "persisted",
      dryRun: false,
      persistence: { targetStore: "store", transactionResponseHash: "d".repeat(64) },
    };
    expect(AnyRunManifestSchema.parse(v2).schemaVersion).toBe("2.0.0");
    expect(
      RunManifestSchema.safeParse({ ...v2, schemaVersion: RUN_MANIFEST_VERSION }).success,
    ).toBe(false);
  });

  // Signed before its transaction, a persist-mode manifest is `authorised`, never `persisted`;
  // the two statuses are two shapes, so the published schema enforces it too.
  it("requires an authorised run, and only one, to name its transaction and not be a dry run", () => {
    const authorised = {
      ...manifest,
      status: "authorised",
      dryRun: false,
      persistence: { targetStore: "store", transactionSha256: "e".repeat(64) },
    };
    expect(RunManifestSchema.safeParse(authorised).success).toBe(true);
    expect(AnyRunManifestSchema.safeParse(authorised).success).toBe(true);
    const unnamed: Record<string, unknown> = { ...authorised };
    delete unnamed.persistence;
    for (const wrong of [
      unnamed,
      { ...manifest, persistence: authorised.persistence },
      { ...authorised, dryRun: true },
      { ...manifest, dryRun: false },
      { ...authorised, status: "persisted" },
    ]) {
      expect(RunManifestSchema.safeParse(wrong).success, JSON.stringify(wrong.status)).toBe(false);
    }
  });

  it("publishes the rule in its JSON Schema: one shape per status", () => {
    type Shape = { properties: Record<string, unknown>; required: string[] };
    const published = JSON.parse(
      readFileSync("contracts/generated/run-manifest.schema.json", "utf8"),
    ) as { $defs: Record<string, Shape & { oneOf?: { $ref: string }[] }> };
    const shapes = (published.$defs.RunManifest?.oneOf ?? []).map(
      ({ $ref }) => published.$defs[$ref.replace("#/$defs/", "")] as Shape,
    );
    expect(shapes.map(({ properties }) => properties.status)).toEqual([
      { type: "string", const: "validated" },
      { type: "string", const: "authorised" },
    ]);
    expect(shapes.map(({ properties }) => properties.dryRun)).toEqual([
      { type: "boolean", const: true },
      { type: "boolean", const: false },
    ]);
    expect(shapes.map(({ required }) => required.includes("persistence"))).toEqual([false, true]);
    expect(Object.keys(shapes[0]?.properties ?? {})).not.toContain("persistence");
  });

  // Never written by any version: a refused run leaves no manifest.
  it.each(["rejected", "failed"])("no longer accepts the unused %s status", (status) => {
    expect(AnyRunManifestSchema.safeParse({ ...manifest, status }).success).toBe(false);
  });

  it("rejects an ingestion block on a non-document run", () => {
    const asFixture = { ...manifest, source: { ...manifest.source, kind: "fixture" } };

    expect(issues(asFixture)).toContain("only document runs carry an ingestion block");
  });
});
