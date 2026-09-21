import { beforeAll, describe, expect, it } from "vitest";

import { loadConfig, type AppConfig } from "../src/config.js";
import { loadEmaMapping, type EmaMapping } from "../src/fhir/mapping.js";
import { createSyntheticSubmission } from "../src/fixtures/synthetic-submission.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { GcpEvidenceStore, ledgerRow, type LedgerRow } from "../src/gcp/evidence.js";
import { runPipeline } from "../src/pipeline.js";

let mapping: EmaMapping;
let config: AppConfig;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  config = loadConfig({ NODE_ENV: "test", DRY_RUN: "true", GCP_LOCATION: "europe-west4" });
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
  return ledgerRow(result.evidence);
}

async function fixtureManifest() {
  const result = await runPipeline(
    {
      runId: "66666666-6666-4666-a666-666666666666",
      source: createSyntheticType2Bundle(mapping),
      sourceKind: "fixture",
      sourceResource: "fixture:test",
    },
    mapping,
    config,
  );
  return result.evidence.manifest;
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
  return ledgerRow(result.evidence);
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
    expect(row.contract_version).toBe("1.0.0");
    expect(row.fidelity_status).toBe("passed");
    expect(row.ingestion_source_hash).toBe(submission.provenance.sourceDocument.sha256);
    expect(row.approval_hash).toBe(submission.approval.approvedContentSha256);
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
// the pipeline could only call `unclassified`. The shape is checked before the network call so
// the configuration is named as the fault.
describe("the manifest signing key", () => {
  it("is refused when it names a crypto key rather than a crypto key version", async () => {
    const store = new GcpEvidenceStore(
      loadConfig({
        NODE_ENV: "test",
        DRY_RUN: "true",
        GCP_LOCATION: "europe-west4",
        GOOGLE_CLOUD_PROJECT: "test-project",
        KMS_MANIFEST_KEY:
          "projects/p/locations/europe-west4/keyRings/evidence/cryptoKeys/manifest-signing",
      }),
    );

    await expect(store.signManifest(await fixtureManifest())).rejects.toThrow(
      /must name a crypto key version/,
    );
  });

  it("leaves the manifest unsigned rather than guessing when no key is configured", async () => {
    const store = new GcpEvidenceStore(
      loadConfig({
        NODE_ENV: "test",
        DRY_RUN: "true",
        GCP_LOCATION: "europe-west4",
        GOOGLE_CLOUD_PROJECT: "test-project",
      }),
    );
    const signed = await store.signManifest(await fixtureManifest());

    expect(signed.signature).toBeUndefined();
    expect(signed.manifestHash).toMatch(/^[0-9a-f]{64}$/);
  });
});
