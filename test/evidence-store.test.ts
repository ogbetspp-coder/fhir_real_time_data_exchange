import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { loadConfig, type AppConfig } from "../src/config.js";
import { loadEmaMapping } from "../src/fhir/mapping.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { GcpEvidenceStore, ledgerRow, type SignedManifest } from "../src/gcp/evidence.js";
import { sha256 } from "../src/lib/hash.js";
import { runPipeline } from "../src/pipeline.js";

// The evidence store's writes, with the three Google clients replaced at their method boundary:
// Storage's bucket().file().save(), BigQuery's dataset().table().insert() and KMS's
// asymmetricSign(). What is asserted is what the store asks of each client — object name,
// content, metadata, row — and what it refuses to ask for. Nothing here holds a credential or
// reaches the network.

type GoogleCalls = {
  storageOptions: unknown[];
  bigQueryOptions: unknown[];
  saves: { bucket: string; object: string; content: string; options: unknown }[];
  inserts: { dataset: string; table: string; rows: unknown[] }[];
  signs: { name: string; digest: { sha256: Buffer } }[];
  signature: Buffer | Uint8Array | null;
};

const google = vi.hoisted(() => {
  const calls: GoogleCalls = {
    storageOptions: [],
    bigQueryOptions: [],
    saves: [],
    inserts: [],
    signs: [],
    signature: Buffer.from("synthetic-signature"),
  };
  return calls;
});

vi.mock("@google-cloud/storage", () => ({
  Storage: class {
    constructor(options: unknown) {
      google.storageOptions.push(options);
    }
    bucket(bucket: string) {
      return {
        file: (object: string) => ({
          save: (content: string, options: unknown) => {
            google.saves.push({ bucket, object, content, options });
            return Promise.resolve();
          },
        }),
      };
    }
  },
}));

vi.mock("@google-cloud/bigquery", () => ({
  BigQuery: class {
    constructor(options: unknown) {
      google.bigQueryOptions.push(options);
    }
    dataset(dataset: string) {
      return {
        table: (table: string) => ({
          insert: (rows: unknown[]) => {
            google.inserts.push({ dataset, table, rows });
            return Promise.resolve([{}]);
          },
        }),
      };
    }
  },
}));

vi.mock("@google-cloud/kms", () => ({
  KeyManagementServiceClient: class {
    asymmetricSign(request: { name: string; digest: { sha256: Buffer } }) {
      google.signs.push(request);
      return Promise.resolve([{ signature: google.signature }]);
    }
  },
}));

const RUN_ID = "77777777-7777-4777-a777-777777777777";
const KEY_VERSION =
  "projects/p/locations/europe-west4/keyRings/evidence/cryptoKeys/manifest-signing/cryptoKeyVersions/1";

let signed: SignedManifest;

function config(overrides: Record<string, string> = {}): AppConfig {
  return loadConfig({
    ALLOW_SYNTHETIC_SOURCES: "true",
    NODE_ENV: "test",
    DRY_RUN: "true",
    GCP_LOCATION: "europe-west4",
    GOOGLE_CLOUD_PROJECT: "synthetic-project",
    EVIDENCE_BUCKET: "synthetic-evidence",
    TRANSFORMATION_LEDGER_DATASET: "ema_flow_ledger_dev",
    ...overrides,
  });
}

beforeAll(async () => {
  const mapping = await loadEmaMapping();
  const result = await runPipeline(
    {
      runId: RUN_ID,
      source: createSyntheticType2Bundle(mapping),
      sourceKind: "fixture",
      sourceResource: "fixture:test",
    },
    mapping,
    config(),
  );
  signed = result.evidence;
});

beforeEach(() => {
  google.storageOptions.length = 0;
  google.bigQueryOptions.length = 0;
  google.saves.length = 0;
  google.inserts.length = 0;
  google.signs.length = 0;
  google.signature = Buffer.from("synthetic-signature");
});

describe("constructing the evidence store", () => {
  it("binds Storage and BigQuery to the configured project", () => {
    new GcpEvidenceStore(config());
    expect(google.storageOptions).toEqual([{ projectId: "synthetic-project" }]);
    expect(google.bigQueryOptions).toEqual([{ projectId: "synthetic-project" }]);
  });

  it("refuses without a project rather than letting a client guess one", () => {
    const withoutProject = { ...config(), GOOGLE_CLOUD_PROJECT: undefined };
    expect(() => new GcpEvidenceStore(withoutProject)).toThrow("GOOGLE_CLOUD_PROJECT is required");
  });
});

describe("writing an evidence artefact", () => {
  it("saves pretty JSON under runs/<runId>/<name>.json with its hash in the object metadata", async () => {
    const value = { b: 2, a: [1, "x"] };
    const store = new GcpEvidenceStore(config());

    const uri = await store.writeJson(RUN_ID, "fidelity-report", value);

    expect(uri).toBe(`gs://synthetic-evidence/runs/${RUN_ID}/fidelity-report.json`);
    expect(google.saves).toEqual([
      {
        bucket: "synthetic-evidence",
        object: `runs/${RUN_ID}/fidelity-report.json`,
        content: `${JSON.stringify(value, null, 2)}\n`,
        options: {
          contentType: "application/json",
          resumable: false,
          metadata: { metadata: { runId: RUN_ID, sha256: sha256(value) } },
        },
      },
    ]);
  });

  it("refuses without an evidence bucket, before any write", async () => {
    const store = new GcpEvidenceStore({ ...config(), EVIDENCE_BUCKET: undefined });
    await expect(store.writeJson(RUN_ID, "manifest", {})).rejects.toThrow(
      "EVIDENCE_BUCKET is required",
    );
    expect(google.saves).toEqual([]);
  });
});

describe("writing the ledger row", () => {
  it("inserts exactly the ledger projection of the signed manifest into the configured table", async () => {
    const store = new GcpEvidenceStore(config({ TRANSFORMATION_LEDGER_TABLE: "runs_v2" }));

    await store.writeLedger(signed);

    expect(google.inserts).toEqual([
      { dataset: "ema_flow_ledger_dev", table: "runs_v2", rows: [ledgerRow(signed)] },
    ]);
  });

  it("writes nothing, and does not fail, when no ledger dataset is configured", async () => {
    const store = new GcpEvidenceStore({ ...config(), TRANSFORMATION_LEDGER_DATASET: undefined });
    await expect(store.writeLedger(signed)).resolves.toBeUndefined();
    expect(google.inserts).toEqual([]);
  });
});

describe("signing the manifest", () => {
  it("asks KMS to sign the manifest's SHA-256 digest with the configured key version", async () => {
    const store = new GcpEvidenceStore(config({ KMS_MANIFEST_KEY: KEY_VERSION }));

    const result = await store.signManifest(signed.manifest);

    expect(google.signs).toHaveLength(1);
    expect(google.signs[0]?.name).toBe(KEY_VERSION);
    expect(google.signs[0]?.digest.sha256.toString("hex")).toBe(sha256(signed.manifest));
    expect(result).toEqual({
      manifest: signed.manifest,
      manifestHash: sha256(signed.manifest),
      signature: {
        algorithm: "RSA_SIGN_PSS_2048_SHA256",
        keyVersion: KEY_VERSION,
        valueBase64: Buffer.from("synthetic-signature").toString("base64"),
      },
    });
  });

  it("accepts a signature KMS returns as a plain Uint8Array", async () => {
    google.signature = new Uint8Array([1, 2, 3]);
    const store = new GcpEvidenceStore(config({ KMS_MANIFEST_KEY: KEY_VERSION }));
    const result = await store.signManifest(signed.manifest);
    expect(result.signature?.valueBase64).toBe(Buffer.from([1, 2, 3]).toString("base64"));
  });

  it("refuses an answer that carries no signature", async () => {
    google.signature = null;
    const store = new GcpEvidenceStore(config({ KMS_MANIFEST_KEY: KEY_VERSION }));
    await expect(store.signManifest(signed.manifest)).rejects.toThrow(
      "Cloud KMS returned no manifest signature",
    );
  });

  it("never calls KMS with a crypto key rather than a version", async () => {
    const store = new GcpEvidenceStore(
      config({ KMS_MANIFEST_KEY: KEY_VERSION.replace("/cryptoKeyVersions/1", "") }),
    );
    await expect(store.signManifest(signed.manifest)).rejects.toThrow(
      /must name a crypto key version/,
    );
    expect(google.signs).toEqual([]);
  });
});
