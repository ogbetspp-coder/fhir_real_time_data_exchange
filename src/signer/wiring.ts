import { createHash } from "node:crypto";

import { KeyManagementServiceClient } from "@google-cloud/kms";
import { OAuth2Client } from "google-auth-library";
import type { Hono } from "hono";

import { parseApproverMap } from "../approval/approve.js";
import type { EmaMapping } from "../fhir/mapping.js";
import { GcsApprovalObjects, kmsKeySource } from "../gcp/approval-store.js";
import { GcsSubmissionReader } from "../gcp/submission-reader.js";
import { crc32c } from "../lib/crc32c.js";
import type { SignerConfig } from "./config.js";
import { googleAddOnIdentity } from "./identity.js";
import { createSignerApp } from "./service.js";

// The signer as src/signer/server.ts runs it, built from its configuration and nothing else, so a
// test can hold the wiring to the configuration. Nothing here reads the network until a request
// needs it.

export class ApprovalSigningError extends Error {
  public override readonly name = "ApprovalSigningError";
}

// Signs a message with the approval key version: Cloud KMS is given its SHA-256 digest, with the
// digest's CRC-32C, and the answer is checked the same way
// (https://cloud.google.com/kms/docs/data-integrity-guidelines), as the worker signs its manifests
// (src/gcp/evidence.ts).
export function kmsSigner(
  client: Pick<KeyManagementServiceClient, "asymmetricSign">,
  keyVersion: string,
): (message: Buffer) => Promise<Buffer> {
  return async (message) => {
    const digest = createHash("sha256").update(message).digest();
    const [response] = await client.asymmetricSign({
      name: keyVersion,
      digest: { sha256: digest },
      digestCrc32c: { value: crc32c(digest) },
    });
    const signature = response.signature;
    if (signature === null || signature === undefined || typeof signature === "string") {
      throw new ApprovalSigningError("Cloud KMS returned no approval signature");
    }
    const bytes = Buffer.from(signature);
    if (response.verifiedDigestCrc32c !== true || response.name !== keyVersion) {
      throw new ApprovalSigningError("Cloud KMS did not sign the approval digest as asked");
    }
    if (String(response.signatureCrc32c?.value) !== String(crc32c(bytes))) {
      throw new ApprovalSigningError(
        "Cloud KMS returned an approval signature that fails its checksum",
      );
    }
    return bytes;
  };
}

export function buildSignerApp(
  config: SignerConfig,
  mapping: EmaMapping,
  kms: Pick<
    KeyManagementServiceClient,
    "asymmetricSign" | "getPublicKey"
  > = new KeyManagementServiceClient(),
  google: OAuth2Client = new OAuth2Client(),
): Hono {
  const keyVersion = config.APPROVAL_SIGNING_KEY_VERSION;
  return createSignerApp({
    environment: config.ENVIRONMENT,
    mapping,
    allowSyntheticSources: config.ALLOW_SYNTHETIC_SOURCES,
    approvers: parseApproverMap(config.APPROVER_MAP_JSON),
    identity: googleAddOnIdentity({
      endpointUrl: config.ADDON_ENDPOINT_URL,
      addOnServiceAccount: config.ADDON_SERVICE_ACCOUNT,
      oauthClientId: config.ADDON_OAUTH_CLIENT_ID,
      certificates: async () => (await google.getFederatedSignonCertsAsync()).certs,
      client: google,
    }),
    submissions: new GcsSubmissionReader(config),
    heads: new GcsApprovalObjects(config.APPROVAL_HEADS_BUCKET),
    evidence: new GcsApprovalObjects(config.EVIDENCE_BUCKET),
    evidenceBucket: config.EVIDENCE_BUCKET,
    keys: kmsKeySource(keyVersion, kms),
    sign: kmsSigner(kms, keyVersion),
    signer: { imageDigest: config.IMAGE_DIGEST, keyVersion },
    endpointUrl: config.ADDON_ENDPOINT_URL,
  });
}
