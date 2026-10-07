import { readFileSync } from "node:fs";

import { beforeAll, describe, expect, it } from "vitest";

import { loadConfig, type AppConfig } from "../src/config.js";
import { loadEmaMapping, type EmaMapping } from "../src/fhir/mapping.js";
import { createSyntheticSubmission } from "../src/fixtures/synthetic-submission.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { ledgerRow, type LedgerRow } from "../src/gcp/evidence.js";
import { runPipeline } from "../src/pipeline.js";
import { drawn } from "./support/submission.js";

const COMMIT = { committedAt: "2026-09-27T16:47:12.000Z", bundleVersionId: "MTc5MDUyNzYz" };

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

async function fixtureRow(): Promise<LedgerRow> {
  const result = await runPipeline(
    {
      runId: "44444444-4444-4444-a444-444444444444",
      source: createSyntheticType2Bundle(mapping),
      sourceKind: "fixture",
      sourceResource: "fixture:test",
    },
    mapping,
    config,
  );
  return ledgerRow(result.evidence, COMMIT);
}

async function documentRow(): Promise<LedgerRow> {
  const { submission, fidelityReport, sourceText } = createSyntheticSubmission(mapping);
  const result = await runPipeline(
    {
      runId: "55555555-5555-4555-a555-555555555555",
      sourceKind: "document",
      submission,
      fidelityReport,
      sourceText,
      sourceResource: `document:${"0".repeat(64)}`,
    },
    mapping,
    config,
  );
  return ledgerRow(result.evidence, COMMIT);
}

describe("transformation ledger row", () => {
  it("leaves the ingestion columns null for a non-document run", async () => {
    const row = await fixtureRow();

    expect(row.source_kind).toBe("fixture");
    expect(row.contract_version).toBeNull();
    expect(row.ingestion_source_hash).toBeNull();
    expect(row.fidelity_status).toBeNull();
    expect(row.approval_hash).toBeNull();
    expect(row.run_id).toBe("44444444-4444-4444-a444-444444444444");
  });

  it("projects the approval and fidelity evidence of a document run", async () => {
    const { submission } = createSyntheticSubmission(mapping);
    const row = await documentRow();

    expect(row.source_kind).toBe("document");
    expect(row.contract_version).toBe("2.1.0");
    expect(row.fidelity_status).toBe("passed");
    expect(row.ingestion_source_hash).toBe(drawn(submission).sha256);
    expect(row.approval_hash).toBe(submission.approval.approvedContentSha256);
  });

  // The row is written by the worker and read by BigQuery: a column the row names that the table
  // lacks is refused at insert, and a table column the row never fills stays null forever.
  it("names exactly the columns the ledger table declares", async () => {
    const terraform = readFileSync("infra/main.tf", "utf8");
    const table = terraform.slice(
      terraform.indexOf('resource "google_bigquery_table" "transformation_runs"'),
    );
    const schema = table.slice(table.indexOf("schema = jsonencode(["), table.indexOf("])"));
    const columns = [...schema.matchAll(/name = "([a-z0-9_]+)"/g)].map(([, name]) => name);
    expect(Object.keys(await fixtureRow()).sort()).toEqual(columns.sort());
  });

  // Every queryable column is a hash, an enumeration, a timestamp, or an identifier: the ledger
  // is indexed by regulators' auditors, not read by them for content.
  it("keeps every queryable column free of prose and markup", async () => {
    for (const row of [await fixtureRow(), await documentRow()]) {
      const columns = Object.entries(row).filter(([column]) => column !== "manifest_json");
      for (const [column, value] of columns) {
        if (value === null) continue;
        expect(`${column}=${value}`).not.toMatch(/[<>]/);
        expect(`${column}=${value}`).not.toMatch(/ {1}\w+ {1}\w+ /);
      }
    }
  });
});

// Cloud KMS signs with a crypto key VERSION. Terraform passed the crypto KEY, so every run wrote
// its five evidence artefacts and then stopped at signing, with no signed manifest and a failure
// the pipeline could only call `unclassified`. The shape is now checked when the configuration
// loads, so a deployment with the wrong key does not start.
describe("the manifest signing key", () => {
  const KEY = "projects/p/locations/europe-west4/keyRings/evidence/cryptoKeys/manifest-signing";

  it("is refused at startup when it names a crypto key rather than a crypto key version", () => {
    expect(() => loadConfig({ NODE_ENV: "test", DRY_RUN: "true", KMS_MANIFEST_KEY: KEY })).toThrow(
      /must name a crypto key version/,
    );
    expect(
      loadConfig({
        NODE_ENV: "test",
        DRY_RUN: "true",
        KMS_MANIFEST_KEY: `${KEY}/cryptoKeyVersions/1`,
      }).KMS_MANIFEST_KEY,
    ).toBe(`${KEY}/cryptoKeyVersions/1`);
  });
});

// A persisted run used to start without a signing key or a ledger dataset, then write an
// unsigned manifest named `signed-manifest.json` and no ledger row.
describe("a persist-mode configuration", () => {
  const PERSIST = {
    NODE_ENV: "test",
    DRY_RUN: "false",
    GOOGLE_CLOUD_PROJECT: "synthetic-project",
    HEALTHCARE_DATASET_ID: "dataset",
    SOURCE_FHIR_STORE_ID: "source",
    TARGET_FHIR_STORE_ID: "target",
    EVIDENCE_BUCKET: "evidence",
    FHIR_VALIDATOR_URL: "http://validator.invalid",
    FHIR_ANALYTICS_DATASET: "analytics",
    KMS_MANIFEST_KEY:
      "projects/p/locations/europe-west4/keyRings/evidence/cryptoKeys/manifest-signing/cryptoKeyVersions/1",
    TRANSFORMATION_LEDGER_DATASET: "ledger",
  };

  it("starts with a signing key and a ledger dataset", () => {
    expect(loadConfig(PERSIST).DRY_RUN).toBe(false);
  });

  it.each(["KMS_MANIFEST_KEY", "TRANSFORMATION_LEDGER_DATASET"] as const)(
    "does not start without %s",
    (key) => {
      const environment: Record<string, string> = Object.fromEntries(
        Object.entries(PERSIST).filter(([name]) => name !== key),
      );
      expect(() => loadConfig(environment)).toThrow(`${key} is required when DRY_RUN=false`);
    },
  );
});
