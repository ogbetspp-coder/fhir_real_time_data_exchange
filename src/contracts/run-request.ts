import { z } from "zod";

import { FhirId, Sha256Hex, StorageUri, Uuid } from "./common.js";

// The wire shape of POST /v1/runs. It is a published contract because Zone A and the Workflows
// orchestration both construct it, and because it is where the by-reference rule is expressed:
// an approved hand-off is named by location and hash (ADR 0002 decision 5), never inlined. Real
// submissions are multi-megabyte, Workflows bodies are capped, and an inline body would transit
// execution history and logs.

export const RUN_REQUEST_VERSION = "1.0.0";

export const SubmissionRefSchema = z.strictObject({ uri: StorageUri, sha256: Sha256Hex }).meta({
  id: "SubmissionRef",
  description:
    "Cloud Storage location of a canonical submission, pinned by the SHA-256 of its canonical JSON value.",
});

export type SubmissionRef = z.infer<typeof SubmissionRefSchema>;

export const RunRequestSchema = z
  .discriminatedUnion("source", [
    z.strictObject({ source: z.literal("fixture"), runId: Uuid.optional() }),
    z.strictObject({
      source: z.literal("healthcare-api"),
      bundleId: FhirId,
      runId: Uuid.optional(),
    }),
    z.strictObject({
      source: z.literal("document"),
      submissionRef: SubmissionRefSchema,
      runId: Uuid.optional(),
    }),
  ])
  .meta({
    id: "RunRequest",
    description:
      "Request to run the deterministic pipeline. Strict: an unrecognised field is a rejection, never a silently ignored one.",
  });

export type RunRequest = z.infer<typeof RunRequestSchema>;
