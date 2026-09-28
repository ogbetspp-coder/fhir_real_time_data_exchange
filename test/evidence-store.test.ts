import { constants, generateKeyPairSync, sign, verify } from "node:crypto";

import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { loadConfig, type AppConfig } from "../src/config.js";
import { loadEmaMapping } from "../src/fhir/mapping.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import {
  EVIDENCE_RETRY_DELAYS_MS,
  GcpEvidenceStore,
  LEDGER_SCHEMA_RETRY_DELAYS_MS,
  ledgerRow,
  type SignedManifest,
} from "../src/gcp/evidence.js";
import { crc32c } from "../src/lib/crc32c.js";
import { canonicalJson, sha256 } from "../src/lib/hash.js";
import { runPipeline } from "../src/pipeline.js";

// The evidence store's writes, with the three Google clients replaced at their method boundary:
// Storage's bucket().file().save(), BigQuery's dataset().table().insert() and KMS's
// asymmetricSign(). What is asserted is what the store asks of each client — object name,
// content, metadata, row — and what it refuses to ask for. Nothing here holds a credential or
// reaches the network.

type SignRequest = {
  name: string;
  digest: { sha256: Buffer };
  digestCrc32c?: { value: number };
};

type GoogleCalls = {
  storageOptions: unknown[];
  bigQueryOptions: unknown[];
  saves: { bucket: string; object: string; content: string; options: unknown }[];
  // Failures the next saves answer with, in order; `landed` means the object was written anyway
  // (an answer lost on the way back).
  saveErrors: (Error & { landed?: boolean })[];
  inserts: { dataset: string; table: string; rows: unknown[]; options: unknown }[];
  insertErrors: Error[];
  signs: SignRequest[];
  // What KMS signs with, given the request: a fixed value by default, a real RSA-PSS key in
  // the round trip below.
  signer: (request: SignRequest) => Buffer | Uint8Array | null;
  // Overrides of what KMS reports about the request, for the refusals below.
  answer: { name?: string; verifiedDigestCrc32c?: boolean; signatureCrc32c?: string | null };
  crc: (bytes: Uint8Array) => number;
};

const google = vi.hoisted(() => {
  const calls: GoogleCalls = {
    storageOptions: [],
    bigQueryOptions: [],
    saves: [],
    saveErrors: [],
    inserts: [],
    insertErrors: [],
    signs: [],
    signer: () => Buffer.from("synthetic-signature"),
    answer: {},
    crc: () => 0,
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
            const failure = google.saveErrors.shift();
            if (failure === undefined || failure.landed === true) {
              google.saves.push({ bucket, object, content, options });
            }
            return failure === undefined ? Promise.resolve() : Promise.reject(failure);
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
          insert: (rows: unknown[], options: unknown) => {
            const failure = google.insertErrors.shift();
            if (failure !== undefined) return Promise.reject(failure);
            google.inserts.push({ dataset, table, rows, options });
            return Promise.resolve([{}]);
          },
        }),
      };
    }
  },
}));

// Answers as Cloud KMS does: whether the digest matched the CRC-32C it was sent, the key version
// that signed, and the signature's own CRC-32C.
vi.mock("@google-cloud/kms", () => ({
  KeyManagementServiceClient: class {
    asymmetricSign(request: SignRequest) {
      google.signs.push(request);
      const signature = google.signer(request);
      const verifiedDigestCrc32c =
        google.answer.verifiedDigestCrc32c ??
        request.digestCrc32c?.value === google.crc(request.digest.sha256);
      const signatureCrc32c =
        google.answer.signatureCrc32c === undefined
          ? signature === null
            ? null
            : { value: String(google.crc(signature)) }
          : google.answer.signatureCrc32c === null
            ? null
            : { value: google.answer.signatureCrc32c };
      return Promise.resolve([
        {
          signature,
          name: google.answer.name ?? request.name,
          verifiedDigestCrc32c,
          signatureCrc32c,
        },
      ]);
    }
  },
}));

google.crc = crc32c;

const RUN_ID = "77777777-7777-4777-a777-777777777777";
const KEY_VERSION =
  "projects/p/locations/europe-west4/keyRings/evidence/cryptoKeys/manifest-signing/cryptoKeyVersions/1";

let signed: SignedManifest;
// The same run as a persist-mode manifest, as the ledger receives it.
let authorised: SignedManifest;
const COMMIT = { committedAt: "2026-09-27T16:47:12.000Z", bundleVersionId: "MTc5MDUyNzYz" };
const NO_WAIT = [0, 0, 0];

function error(message: string, code?: number | string, landed?: boolean): Error {
  return Object.assign(new Error(message), { code, landed });
}

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
  authorised = {
    ...signed,
    manifest: {
      ...signed.manifest,
      status: "authorised",
      dryRun: false,
      persistence: { targetStore: "validated", transactionSha256: "e".repeat(64) },
    },
  };
});

beforeEach(() => {
  google.storageOptions.length = 0;
  google.bigQueryOptions.length = 0;
  google.saves.length = 0;
  google.inserts.length = 0;
  google.signs.length = 0;
  google.saveErrors.length = 0;
  google.insertErrors.length = 0;
  google.signer = () => Buffer.from("synthetic-signature");
  google.answer = {};
});

describe("constructing the evidence store", () => {
  it("binds Storage and BigQuery to the configured project", () => {
    new GcpEvidenceStore(config());
    // Storage's own retries are off: writeJson decides what a retried write's 412 means.
    expect(google.storageOptions).toEqual([
      { projectId: "synthetic-project", retryOptions: { autoRetry: false } },
    ]);
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
          // Never over an existing object: the run's first write claims its runId.
          preconditionOpts: { ifGenerationMatch: 0 },
          metadata: { metadata: { runId: RUN_ID, sha256: sha256(value) } },
        },
      },
    ]);
  });

  it("names a runId whose evidence already exists on the first attempt", async () => {
    const store = new GcpEvidenceStore(config(), NO_WAIT);
    google.saveErrors.push(error("Precondition Failed", 412));
    await expect(store.writeJson(RUN_ID, "source-type2", {})).rejects.toThrow(
      "Run evidence already exists for this run id",
    );
  });

  it("names Cloud Storage refusing the write, without retrying it", async () => {
    const store = new GcpEvidenceStore(config(), NO_WAIT);
    google.saveErrors.push(error("Forbidden", 403), error("unexpected second attempt", 500));
    await expect(store.writeJson(RUN_ID, "source-type2", {})).rejects.toThrow(
      "Cloud Storage refused an evidence write",
    );
    expect(google.saveErrors).toHaveLength(1);
  });

  it("retries a transient failure and writes the object", async () => {
    const store = new GcpEvidenceStore(config(), NO_WAIT);
    google.saveErrors.push(error("socket hang up", "ECONNRESET"), error("Unavailable", 503));
    await store.writeJson(RUN_ID, "commit", { a: 1 });
    expect(google.saves).toHaveLength(1);
  });

  // After the commit only: the first attempt wrote the object and its answer was lost, so the
  // retry's precondition finds it. This run has already claimed the runId with every earlier
  // object, and refusing would turn a committed run into an unrecorded one.
  it("takes a 412 after an unanswered attempt as its own write, after the commit only", async () => {
    const store = new GcpEvidenceStore(config(), NO_WAIT);
    google.saveErrors.push(error("Service Unavailable", 503, true), error("Precondition", 412));
    await expect(store.writeJson(RUN_ID, "commit", { a: 1 }, { afterCommit: true })).resolves.toBe(
      `gs://synthetic-evidence/runs/${RUN_ID}/commit.json`,
    );
    expect(google.saves).toHaveLength(1);
  });

  // Review round 2's repro. Run A wrote its claim under a runId and died; run B reuses the runId,
  // its claim's first attempt goes unanswered without landing, and the retry answers 412 for A's
  // object. Taking that as B's own write paired A's evidence with B's signed manifest. Before
  // the transaction every 412 fails closed: a wrong refusal leaves nothing live.
  it("refuses a 412 after an unanswered attempt before the commit: it may be another run's object", async () => {
    const store = new GcpEvidenceStore(config(), NO_WAIT);
    google.saveErrors.push(error("Service Unavailable", 503), error("Precondition", 412));
    await expect(store.writeJson(RUN_ID, "source-type2", { from: "B" })).rejects.toThrow(
      "Run evidence already exists for this run id",
    );
    expect(google.saves).toEqual([]);
  });

  // A 429 wrote nothing, so the 412 that follows it is another run's object even after the commit.
  it("does not count a rate-limited attempt as unanswered", async () => {
    const store = new GcpEvidenceStore(config(), NO_WAIT);
    google.saveErrors.push(error("Too Many Requests", 429), error("Precondition", 412));
    await expect(
      store.writeJson(RUN_ID, "commit", { a: 1 }, { afterCommit: true }),
    ).rejects.toThrow("Run evidence already exists for this run id");
  });

  it("gives up after its bounded retries, and passes a non-transient failure through", async () => {
    const store = new GcpEvidenceStore(config(), [0]);
    google.saveErrors.push(error("Unavailable", 503), error("Unavailable", 503));
    await expect(store.writeJson(RUN_ID, "commit", {})).rejects.toThrow("Unavailable");
    google.saveErrors.push(error("Bad Request", 400));
    await expect(store.writeJson(RUN_ID, "commit", {})).rejects.toThrow("Bad Request");
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
  it("inserts exactly the ledger row of the committed run into the configured table", async () => {
    const store = new GcpEvidenceStore(config({ TRANSFORMATION_LEDGER_TABLE: "runs_v2" }));

    await store.writeLedger(authorised, COMMIT);

    expect(google.inserts).toEqual([
      {
        dataset: "ema_flow_ledger_dev",
        table: "runs_v2",
        // The runId as insertId, so a retried insert is deduplicated.
        rows: [{ insertId: RUN_ID, json: ledgerRow(authorised, COMMIT) }],
        options: { raw: true },
      },
    ]);
  });

  it("records the commit: persisted, when, the transaction and the Bundle version", () => {
    expect(ledgerRow(authorised, COMMIT)).toMatchObject({
      status: "persisted",
      completed_at: COMMIT.committedAt,
      transaction_sha256: "e".repeat(64),
      target_bundle_version_id: COMMIT.bundleVersionId,
    });
  });

  it("retries a refused insert within its ordinary bound, then gives up", async () => {
    const store = new GcpEvidenceStore(config(), [0, 0], [0, 0, 0, 0]);
    google.insertErrors.push(error("Service Unavailable", 503));
    await store.writeLedger(authorised, COMMIT);
    expect(google.inserts).toHaveLength(1);

    google.insertErrors.push(error("one"), error("two"), error("three"), error("four"));
    await expect(store.writeLedger(authorised, COMMIT)).rejects.toThrow("three");
  });

  // BigQuery's streaming path can refuse a row naming a freshly added column for a few minutes
  // after Terraform adds it, and every persisted run writes the 3.0.0 columns. The client reports
  // it as a PartialFailureError whose row errors say "no such field": that refusal, and only
  // that one, gets the longer tail.
  it("keeps retrying a 'no such field' refusal through the schema-cache tail", async () => {
    const store = new GcpEvidenceStore(config(), [0, 0], [0, 0, 0, 0]);
    const schemaRefusal = (): Error =>
      Object.assign(new Error("A failure occurred during this request."), {
        name: "PartialFailureError",
        errors: [
          {
            row: {},
            errors: [{ reason: "invalid", message: "no such field: transaction_sha256." }],
          },
        ],
      });
    google.insertErrors.push(schemaRefusal(), schemaRefusal(), schemaRefusal(), schemaRefusal());
    await store.writeLedger(authorised, COMMIT);
    expect(google.inserts).toHaveLength(1);

    google.insertErrors.push(...Array.from({ length: 5 }, schemaRefusal));
    await expect(store.writeLedger(authorised, COMMIT)).rejects.toThrow("A failure occurred");
  });

  it("gives the schema tail about six minutes, a minute at most between attempts", () => {
    const total = LEDGER_SCHEMA_RETRY_DELAYS_MS.reduce((sum, delay) => sum + delay, 0);
    expect(total).toBeGreaterThanOrEqual(5 * 60_000);
    expect(total).toBeLessThanOrEqual(7 * 60_000);
    expect(Math.max(...LEDGER_SCHEMA_RETRY_DELAYS_MS)).toBe(60_000);
    expect(LEDGER_SCHEMA_RETRY_DELAYS_MS.length).toBeGreaterThan(EVIDENCE_RETRY_DELAYS_MS.length);
  });

  // The ledger row is the run's commit record; a persisted run without one is not recorded.
  it("refuses, rather than skipping the row, when no ledger dataset is configured", async () => {
    const store = new GcpEvidenceStore({ ...config(), TRANSFORMATION_LEDGER_DATASET: undefined });
    await expect(store.writeLedger(authorised, COMMIT)).rejects.toThrow(
      "TRANSFORMATION_LEDGER_DATASET is required",
    );
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
    expect(google.signs[0]?.digestCrc32c).toEqual({
      value: crc32c(Buffer.from(sha256(signed.manifest), "hex")),
    });
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
    google.signer = () => new Uint8Array([1, 2, 3]);
    const store = new GcpEvidenceStore(config({ KMS_MANIFEST_KEY: KEY_VERSION }));
    const result = await store.signManifest(signed.manifest);
    expect(result.signature?.valueBase64).toBe(Buffer.from([1, 2, 3]).toString("base64"));
  });

  it("refuses an answer that carries no signature", async () => {
    google.signer = () => null;
    const store = new GcpEvidenceStore(config({ KMS_MANIFEST_KEY: KEY_VERSION }));
    await expect(store.signManifest(signed.manifest)).rejects.toThrow(
      "Cloud KMS returned no manifest signature",
    );
  });

  it.each([
    [
      "a digest KMS did not verify",
      { verifiedDigestCrc32c: false },
      "Cloud KMS did not verify the manifest digest's checksum",
    ],
    [
      "another key version",
      { name: `${KEY_VERSION.slice(0, -1)}2` },
      "Cloud KMS signed with a key version other than the configured one",
    ],
    [
      "a signature whose checksum does not match",
      { signatureCrc32c: "1" },
      "Cloud KMS returned a signature that fails its checksum",
    ],
    [
      "a signature with no checksum",
      { signatureCrc32c: null },
      "Cloud KMS returned a signature that fails its checksum",
    ],
  ])("refuses an answer with %s", async (_name, answer, message) => {
    google.answer = answer;
    const store = new GcpEvidenceStore(config({ KMS_MANIFEST_KEY: KEY_VERSION }));
    await expect(store.signManifest(signed.manifest)).rejects.toThrow(message);
  });

  // There is no unsigned fallback: the store is used only in persist mode, and a persisted run's
  // manifest is always signed.
  it("refuses to sign, rather than returning an unsigned manifest, when no key is configured", async () => {
    const store = new GcpEvidenceStore(config());
    await expect(store.signManifest(signed.manifest)).rejects.toThrow(
      "KMS_MANIFEST_KEY is required",
    );
    expect(google.signs).toEqual([]);
  });

  // Every other test's KMS returns a fixed string, so nothing had ever checked that a signature
  // verifies. This one signs with a real RSA-PSS key as KMS's RSA_SIGN_PSS_2048_SHA256 does
  // (SHA-256, MGF1 SHA-256, a 32-byte salt) and verifies against the object as it was stored.
  it("writes a signed manifest whose signature verifies over the stored manifest", async () => {
    const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
    const pss = { padding: constants.RSA_PKCS1_PSS_PADDING, saltLength: 32 };
    // KMS signs a digest; this stand-in signs the message the digest was taken of, so it only
    // answers for a digest of the manifest's canonical JSON.
    const messages = new Map([[sha256(signed.manifest), canonicalJson(signed.manifest)]]);
    google.signer = (request) => {
      const message = messages.get(request.digest.sha256.toString("hex"));
      if (message === undefined) throw new Error("KMS was asked to sign an unknown digest");
      return sign("sha256", Buffer.from(message), { key: privateKey, ...pss });
    };
    const store = new GcpEvidenceStore(config({ KMS_MANIFEST_KEY: KEY_VERSION }));

    await store.writeJson(RUN_ID, "signed-manifest", await store.signManifest(signed.manifest));

    const stored = JSON.parse(google.saves[0]?.content ?? "") as SignedManifest;
    expect(stored.manifestHash).toBe(sha256(stored.manifest));
    const signature = Buffer.from(stored.signature?.valueBase64 ?? "", "base64");
    const verifies = (manifest: unknown): boolean =>
      verify("sha256", Buffer.from(canonicalJson(manifest)), { key: publicKey, ...pss }, signature);
    expect(verifies(stored.manifest)).toBe(true);
    expect(verifies({ ...stored.manifest, runId: "00000000-0000-4000-8000-000000000000" })).toBe(
      false,
    );
  });

  // The configuration refuses such a key at startup (test/evidence.test.ts); the store still
  // checks, for a configuration built without loadConfig.
  it("never calls KMS with a crypto key rather than a version", async () => {
    const store = new GcpEvidenceStore({
      ...config(),
      KMS_MANIFEST_KEY: KEY_VERSION.replace("/cryptoKeyVersions/1", ""),
    });
    await expect(store.signManifest(signed.manifest)).rejects.toThrow(
      /must name a crypto key version/,
    );
    expect(google.signs).toEqual([]);
  });
});
