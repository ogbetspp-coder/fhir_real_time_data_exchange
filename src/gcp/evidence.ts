import { BigQuery } from "@google-cloud/bigquery";
import { KeyManagementServiceClient } from "@google-cloud/kms";
import { Storage } from "@google-cloud/storage";

import type { AppConfig } from "../config.js";
import type { RunManifest } from "../contracts/run-manifest.js";
import { canonicalJson, sha256 } from "../lib/hash.js";

export type { RunManifest } from "../contracts/run-manifest.js";

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

  public constructor(private readonly config: AppConfig) {
    const projectId = config.GOOGLE_CLOUD_PROJECT;
    if (projectId === undefined) throw new Error("GOOGLE_CLOUD_PROJECT is required");

    this.#storage = new Storage({ projectId });
    this.#kms = new KeyManagementServiceClient();
    this.#bigQuery = new BigQuery({ projectId });
  }

  public async writeJson(runId: string, name: string, value: unknown): Promise<string> {
    const bucketName = this.config.EVIDENCE_BUCKET;
    if (bucketName === undefined) throw new Error("EVIDENCE_BUCKET is required");
    const objectName = `runs/${runId}/${name}.json`;
    await this.#storage
      .bucket(bucketName)
      .file(objectName)
      .save(`${JSON.stringify(value, null, 2)}\n`, {
        contentType: "application/json",
        resumable: false,
        metadata: {
          metadata: {
            runId,
            sha256: sha256(value),
          },
        },
      });
    return `gs://${bucketName}/${objectName}`;
  }

  public async signManifest(manifest: RunManifest): Promise<SignedManifest> {
    const manifestHash = sha256(manifest);
    const keyVersion = this.config.KMS_MANIFEST_KEY;
    if (keyVersion === undefined) return { manifest, manifestHash };

    const [response] = await this.#kms.asymmetricSign({
      name: keyVersion,
      digest: { sha256: Buffer.from(manifestHash, "hex") },
    });
    if (response.signature === null || response.signature === undefined) {
      throw new Error("Cloud KMS returned no manifest signature");
    }
    const signature = Buffer.isBuffer(response.signature)
      ? response.signature
      : Buffer.from(response.signature as Uint8Array);

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

  public async writeLedger(record: SignedManifest): Promise<void> {
    const dataset = this.config.TRANSFORMATION_LEDGER_DATASET;
    if (dataset === undefined) return;
    const table = this.config.TRANSFORMATION_LEDGER_TABLE;
    await this.#bigQuery
      .dataset(dataset)
      .table(table)
      .insert([
        {
          run_id: record.manifest.runId,
          completed_at: record.manifest.completedAt,
          status: record.manifest.status,
          source_hash: record.manifest.source.hash,
          output_hash: record.manifest.transformation.outputHash,
          manifest_hash: record.manifestHash,
          signature_key_version: record.signature?.keyVersion ?? null,
          manifest_json: canonicalJson(record.manifest),
        },
      ]);
  }
}
