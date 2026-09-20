import { z } from "zod";

import { Token } from "../contracts/common.js";

// Configuration of the read-only query service (ADR 0004: own deployable, own identity, own
// configuration). It reads none of the worker's variables — no source store, no evidence
// bucket, no KMS key, no DRY_RUN — so a misconfiguration of one service cannot reach the other.

const Required = z.string().trim().min(1).max(256);

export const QueryConfigSchema = z.object({
  GOOGLE_CLOUD_PROJECT: Required,
  GCP_LOCATION: Required.default("europe-west4"),
  HEALTHCARE_DATASET_ID: Required,
  // The validated store only. The query service has no knowledge of the source store.
  TARGET_FHIR_STORE_ID: Required,
  // The audience every caller's OIDC ID token must carry (normally the service URL).
  QUERY_AUDIENCE: z.string().trim().min(1).max(512),
  // Phase 1 entitlement backing: a JSON object of principal subject to organisation and the
  // document Bundle ids that principal may read. Firestore replaces this in phase 2.
  QUERY_ENTITLEMENTS_JSON: z.string().max(1_000_000).default("{}"),
  // Recorded in every audit record, so an answer can be tied to the code that produced it.
  QUERY_SERVICE_VERSION: Token.default("development"),
  IMAGE_DIGEST: Token.optional(),
  PORT: z.coerce.number().int().min(1).max(65_535).default(8080),
});

export type QueryConfig = z.infer<typeof QueryConfigSchema>;

export function loadQueryConfig(environment: NodeJS.ProcessEnv = process.env): QueryConfig {
  return QueryConfigSchema.parse(environment);
}
