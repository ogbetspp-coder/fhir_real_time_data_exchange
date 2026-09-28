import { BigQuery } from "@google-cloud/bigquery";
import { KeyManagementServiceClient } from "@google-cloud/kms";
import { Storage } from "@google-cloud/storage";

import type { AppConfig } from "../config.js";
import type { RunManifest } from "../contracts/run-manifest.js";
import { crc32c } from "../lib/crc32c.js";
import { canonicalJson, sha256 } from "../lib/hash.js";
import { log } from "../lib/logger.js";

export type { RunManifest } from "../contracts/run-manifest.js";

// How long to wait before each retry of an evidence write or of the ledger insert. Bounded: about
// two minutes in all, inside the worker's request timeout. The ledger needs it most: BigQuery's
// streaming path caches a table's schema for a few minutes, so a row naming a freshly added column
// can be refused just after the column is added (infra/run.tf).
export const EVIDENCE_RETRY_DELAYS_MS: readonly number[] = [2_000, 5_000, 15_000, 30_000, 60_000];

// What the transaction wrote, as the ledger row records it.
export type LedgerCommit = {
  // When the transaction answered.
  committedAt: string;
  // The document Bundle version it wrote, when its response named one.
  bundleVersionId: string | null;
};

function statusCode(error: unknown): unknown {
  return (error as { code?: unknown } | null | undefined)?.code;
}

// A failure a retry may cure: no HTTP answer at all (a reset, a timeout), a rate limit, or a
// server error. Each leaves open whether the write landed.
function transient(error: unknown): boolean {
  const code = statusCode(error);
  return typeof code !== "number" || code === 429 || code >= 500;
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

export type SignedManifest = {
  manifest: RunManifest;
  manifestHash: string;
  signature?: {
    algorithm: "RSA_SIGN_PSS_2048_SHA256";
    keyVersion: string;
    valueBase64: string;
  };
};

export class GcpEvidenceStore {
  readonly #storage: Storage;
  readonly #kms: KeyManagementServiceClient;
  readonly #bigQuery: BigQuery;

  public constructor(
    private readonly config: AppConfig,
    private readonly retryDelaysMs: readonly number[] = EVIDENCE_RETRY_DELAYS_MS,
  ) {
    const projectId = config.GOOGLE_CLOUD_PROJECT;
    if (projectId === undefined) throw new Error("GOOGLE_CLOUD_PROJECT is required");

    // The client's own retries are off: a write it retried after an unanswered first attempt
    // could answer 412 for the object that attempt created, and only this class knows an earlier
    // attempt went unanswered (writeJson).
    this.#storage = new Storage({ projectId, retryOptions: { autoRetry: false } });
    this.#kms = new KeyManagementServiceClient();
    this.#bigQuery = new BigQuery({ projectId });
  }

  // Writes one evidence object, never over an existing one: `ifGenerationMatch: 0` makes the
  // write conditional on the object not existing. A run's first write therefore claims its runId,
  // and a runId already used (a caller's retry, or a replay) is refused before the run writes
  // anything to the FHIR store, rather than persisting a second version and then failing on the
  // first run's retained objects.
  //
  // A transient failure is retried. It leaves open whether the attempt landed, so a 412 on a
  // retry means that attempt's object is there and is taken as written; the worker holds
  // `objectCreator` and cannot read the object back to compare. A 412 on a first attempt is a
  // reused runId. (Only the claim is exposed to a wrong guess: if an earlier run with this runId
  // existed and the claim's first attempt went unanswered, the next object's first attempt still
  // answers 412, before the FHIR store is touched.) A 403 is Cloud Storage refusing the write.
  public async writeJson(runId: string, name: string, value: unknown): Promise<string> {
    const bucketName = this.config.EVIDENCE_BUCKET;
    if (bucketName === undefined) throw new Error("EVIDENCE_BUCKET is required");
    const objectName = `runs/${runId}/${name}.json`;
    const uri = `gs://${bucketName}/${objectName}`;
    let unanswered = false;
    for (let attempt = 0; ; attempt += 1) {
      try {
        await this.#storage
          .bucket(bucketName)
          .file(objectName)
          .save(`${JSON.stringify(value, null, 2)}\n`, {
            contentType: "application/json",
            resumable: false,
            preconditionOpts: { ifGenerationMatch: 0 },
            metadata: {
              metadata: {
                runId,
                sha256: sha256(value),
              },
            },
          });
        return uri;
      } catch (error) {
        const code = statusCode(error);
        if (code === 412 && unanswered) {
          log("warning", "Evidence object found written after an unanswered attempt", {
            runId,
            stage: "evidence",
          });
          return uri;
        }
        if (code === 412) {
          throw new Error("Run evidence already exists for this run id", { cause: error });
        }
        if (code === 403) {
          throw new Error("Cloud Storage refused an evidence write", { cause: error });
        }
        const delay = this.retryDelaysMs[attempt];
        if (!transient(error) || delay === undefined) throw error;
        unanswered = true;
        await sleep(delay);
      }
    }
  }

  // A persisted run's manifest is always signed: there is no unsigned fallback, since the store
  // is used only when DRY_RUN=false, where the configuration requires a key (src/config.ts).
  public async signManifest(manifest: RunManifest): Promise<SignedManifest> {
    const manifestHash = sha256(manifest);
    const keyVersion = this.config.KMS_MANIFEST_KEY;
    if (keyVersion === undefined) throw new Error("KMS_MANIFEST_KEY is required");

    // Cloud KMS signs with a crypto key VERSION. A name that stops at the crypto key is refused,
    // and the refusal arrives as an opaque client error that says nothing about which of the
    // pipeline's gates failed — which is how every run in this project came to write five
    // evidence artefacts and then stop, with no signed manifest and no explanation. Checked here
    // so the configuration is named as the fault, before a network call is made.
    if (!keyVersion.includes("/cryptoKeyVersions/")) {
      throw new Error("KMS_MANIFEST_KEY must name a crypto key version");
    }

    // The digest travels with its CRC-32C, and the answer is checked the same way: KMS says
    // whether the digest it received matched, which key version signed, and the signature's own
    // CRC-32C (https://cloud.google.com/kms/docs/data-integrity-guidelines).
    const digest = Buffer.from(manifestHash, "hex");
    const [response] = await this.#kms.asymmetricSign({
      name: keyVersion,
      digest: { sha256: digest },
      digestCrc32c: { value: crc32c(digest) },
    });
    if (response.signature === null || response.signature === undefined) {
      throw new Error("Cloud KMS returned no manifest signature");
    }
    const signature = Buffer.isBuffer(response.signature)
      ? response.signature
      : Buffer.from(response.signature as Uint8Array);
    if (response.verifiedDigestCrc32c !== true) {
      throw new Error("Cloud KMS did not verify the manifest digest's checksum");
    }
    if (response.name !== keyVersion) {
      throw new Error("Cloud KMS signed with a key version other than the configured one");
    }
    const signatureCrc32c = response.signatureCrc32c?.value;
    if (
      signatureCrc32c === null ||
      signatureCrc32c === undefined ||
      String(signatureCrc32c) !== String(crc32c(signature))
    ) {
      throw new Error("Cloud KMS returned a signature that fails its checksum");
    }

    return {
      manifest,
      manifestHash,
      signature: {
        algorithm: "RSA_SIGN_PSS_2048_SHA256",
        keyVersion,
        valueBase64: signature.toString("base64"),
      },
    };
  }

  // The ledger row records a committed run: written after the transaction succeeded, never
  // skipped (a persist-mode configuration without a ledger dataset does not start, src/config.ts),
  // and retried within a bound. The insertId lets BigQuery drop the duplicate a retry after an
  // unanswered insert could otherwise leave (best effort, within its deduplication window).
  public async writeLedger(record: SignedManifest, commit: LedgerCommit): Promise<void> {
    const dataset = this.config.TRANSFORMATION_LEDGER_DATASET;
    if (dataset === undefined) throw new Error("TRANSFORMATION_LEDGER_DATASET is required");
    const table = this.config.TRANSFORMATION_LEDGER_TABLE;
    const row = ledgerRow(record, commit);
    for (let attempt = 0; ; attempt += 1) {
      try {
        await this.#bigQuery
          .dataset(dataset)
          .table(table)
          .insert([{ insertId: row.run_id, json: row }], { raw: true });
        return;
      } catch (error) {
        const delay = this.retryDelaysMs[attempt];
        if (delay === undefined) throw error;
        log("warning", "Ledger insert failed; retrying", {
          runId: row.run_id,
          stage: "ledger",
          attempt: attempt + 1,
        });
        await sleep(delay);
      }
    }
  }
}

export type LedgerRow = {
  run_id: string;
  completed_at: string;
  status: string;
  source_kind: string;
  source_hash: string;
  output_hash: string;
  manifest_hash: string;
  signature_key_version: string | null;
  contract_version: string | null;
  ingestion_source_hash: string | null;
  fidelity_status: string | null;
  approval_hash: string | null;
  transaction_sha256: string | null;
  target_bundle_version_id: string | null;
  manifest_json: string;
};

// The queryable record of a committed run: its signed manifest's projection, and what the
// transaction wrote. Every column is a hash, an enumeration, a timestamp, or an identifier — the
// full manifest travels as JSON, and neither carries narrative (ADR 0002). Columns that only a
// document run populates are null everywhere else, which is why they could be added to the
// existing table in place. The row, not the manifest, says `persisted`: the manifest is signed
// before the transaction and says `authorised`. `completed_at` is when the transaction answered.
export function ledgerRow(record: SignedManifest, commit: LedgerCommit): LedgerRow {
  const { manifest } = record;
  const { ingestion } = manifest;
  return {
    run_id: manifest.runId,
    completed_at: commit.committedAt,
    status: "persisted",
    source_kind: manifest.source.kind,
    source_hash: manifest.source.hash,
    output_hash: manifest.transformation.outputHash,
    manifest_hash: record.manifestHash,
    signature_key_version: record.signature?.keyVersion ?? null,
    contract_version: ingestion?.contractVersion ?? null,
    ingestion_source_hash: ingestion?.sourceDocumentSha256 ?? null,
    fidelity_status: ingestion?.fidelity.status ?? null,
    approval_hash: ingestion?.approval.approvedContentSha256 ?? null,
    transaction_sha256:
      manifest.status === "authorised" ? manifest.persistence.transactionSha256 : null,
    target_bundle_version_id: commit.bundleVersionId,
    manifest_json: canonicalJson(manifest),
  };
}
