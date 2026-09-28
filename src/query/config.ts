import { z } from "zod";

import { Token } from "../contracts/common.js";
import { ImageDigest } from "../contracts/query-tools.js";

// Configuration of the read-only query service (ADR 0004: own deployable, own identity, own
// configuration). It reads none of the worker's variables — no source store, no evidence
// bucket, no KMS key, no DRY_RUN — so a misconfiguration of one service cannot reach the other.

const Required = z.string().trim().min(1).max(256);

// An OAuth 2.0 client id is compared with a token's `aud`/`azp` and never interpreted; the
// grammar only keeps whitespace and the list separator out of a value.
const OAuthClientId = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$/);

export const QueryConfigSchema = z.object({
  GOOGLE_CLOUD_PROJECT: Required,
  GCP_LOCATION: Required.default("europe-west4"),
  HEALTHCARE_DATASET_ID: Required,
  // The validated store only. The query service has no knowledge of the source store.
  TARGET_FHIR_STORE_ID: Required,
  // The audience every caller's OIDC ID token must carry (normally the service URL).
  QUERY_AUDIENCE: z.string().trim().min(1).max(512),
  // Log the category of an authentication refusal (never the token, never the reason to the
  // caller). Off unless set: in production a refusal reason in a log is a hint about a
  // credential. On in dev, where a credential refused for an unknown reason is undiagnosable.
  QUERY_LOG_REJECTION_REASON: z
    .enum(["true", "false"])
    .default("false")
    .transform((value) => value === "true"),
  // Comma-separated OAuth 2.0 client ids whose access tokens are accepted on `Authorization`
  // (the Gemini Enterprise path: the end user's Google access token). Absent or empty: access
  // tokens are rejected and only ID tokens for QUERY_AUDIENCE authenticate.
  QUERY_OAUTH_CLIENT_IDS: z
    .string()
    .max(8_192)
    .default("")
    .transform((value) =>
      value
        .split(",")
        .map((id) => id.trim())
        .filter((id) => id.length > 0),
    )
    .pipe(z.array(OAuthClientId).max(64)),
  // Phase 1 entitlement backing: a JSON object of principal subject to the document Bundle ids
  // that principal may read. Firestore replaces this in phase 2.
  QUERY_ENTITLEMENTS_JSON: z.string().max(1_000_000).default("{}"),
  // Recorded in every audit record, so an answer can be tied to the code that produced it.
  QUERY_SERVICE_VERSION: Token.default("development"),
  // The container image digest, exactly `sha256:<64 hex>` (the infra sets it from the deployed
  // image; never a full image reference). Absent outside a container.
  IMAGE_DIGEST: ImageDigest.optional(),
  PORT: z.coerce.number().int().min(1).max(65_535).default(8080),
});

export type QueryConfig = z.infer<typeof QueryConfigSchema>;

export function loadQueryConfig(environment: NodeJS.ProcessEnv = process.env): QueryConfig {
  return QueryConfigSchema.parse(environment);
}
