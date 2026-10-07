import { z } from "zod";

import { ApprovalEnvironment } from "../contracts/approval.js";
import { ImageDigest } from "../contracts/common.js";

// Configuration of the approval signer (docs/design/approval.md, D3; ADR 0004: own deployable, own
// identity, own configuration). It reads none of the worker's or the query service's variables.
// infra/signer.tf sets every one of these.

const Required = z.string().trim().min(1).max(512);

const KMS_KEY_VERSION =
  /^projects\/[^/]+\/locations\/[^/]+\/keyRings\/[^/]+\/cryptoKeys\/[^/]+\/cryptoKeyVersions\/[0-9]+$/;

export const SignerConfigSchema = z.object({
  // The environment every statement names; one environment's approval never publishes in another.
  ENVIRONMENT: ApprovalEnvironment,
  GOOGLE_CLOUD_PROJECT: Required,
  // Where the submissions it reviews are read from, as the worker reads them.
  SUBMISSION_BUCKET: Required,
  SUBMISSION_MAX_BYTES: z.coerce
    .number()
    .int()
    .min(1_024)
    .max(256 * 1_024 * 1_024)
    .default(48 * 1_024 * 1_024),
  // The evidence bucket: the signer writes `reviews/` and `approvals/` there and nothing else.
  EVIDENCE_BUCKET: Required,
  // The heads bucket (the design's amendment, "Heads under retention").
  APPROVAL_HEADS_BUCKET: Required,
  // The key version the signer signs with: a version of `approval-signing-hsm`.
  APPROVAL_SIGNING_KEY_VERSION: z.string().trim().regex(KMS_KEY_VERSION),
  // The approver map (D2), `{ "<sub>": { "role": ..., "email": ... } }`. Empty: nobody approves.
  APPROVER_MAP_JSON: z.string().max(100_000).default("{}"),
  // The URL the Workspace add-on calls: the audience of the system ID token Google sends.
  ADDON_ENDPOINT_URL: z.url(),
  // The add-on's service account (the system ID token's email) and its OAuth client id (the user
  // ID token's audience), from the Google Workspace Marketplace SDK's HTTP deployment. Empty until
  // the owner creates the add-on: then every event is refused.
  ADDON_SERVICE_ACCOUNT: z.string().trim().max(254).default(""),
  ADDON_OAUTH_CLIENT_ID: z.string().trim().max(256).default(""),
  // As the worker's: whether synthetic submissions are reviewed at all (the gate's own rule).
  ALLOW_SYNTHETIC_SOURCES: z
    .enum(["true", "false"])
    .default("false")
    .transform((value) => value === "true"),
  // The signer's image, named in every statement it signs.
  IMAGE_DIGEST: ImageDigest,
  PORT: z.coerce.number().int().min(1).max(65_535).default(8080),
});

export type SignerConfig = z.infer<typeof SignerConfigSchema>;

export function loadSignerConfig(environment: NodeJS.ProcessEnv = process.env): SignerConfig {
  return SignerConfigSchema.parse(environment);
}
