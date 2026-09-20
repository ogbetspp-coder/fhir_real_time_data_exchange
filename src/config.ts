import { z } from "zod";

import type { RunRequest } from "./contracts/run-request.js";

const optionalNonEmpty = z.string().trim().min(1).optional();

export type RunSource = RunRequest["source"];

// Every source the RunRequest contract names. The Record type keeps this object exactly equal to
// the contract's discriminator set: a source added to the contract without being listed here, or
// a name listed here that the contract lacks, is a compile error, so the allowlist can never
// silently miss one. Key order is the order operators see in documentation and defaults.
const runSourceSet: Record<RunSource, true> = {
  fixture: true,
  "healthcare-api": true,
  document: true,
};
export const RUN_SOURCES = Object.keys(runSourceSet) as [RunSource, ...RunSource[]];

// ADR 0002 consequences: the fixture and healthcare-api sources bypass the document gate, so a
// deployment that handles anything but synthetic content narrows this to `document`. Unset means
// every source is enabled. The value is a comma-separated subset of RUN_SOURCES; an unknown name
// or an empty list is a startup failure, never a silently ignored entry.
const EnabledRunSources = z
  .string()
  .optional()
  .transform((value) =>
    value === undefined ? [...RUN_SOURCES] : value.split(",").map((entry) => entry.trim()),
  )
  .pipe(z.array(z.enum(RUN_SOURCES)).min(1))
  .transform((sources) => [...new Set(sources)] as readonly RunSource[]);

const ConfigSchema = z
  .object({
    NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
    PORT: z.coerce.number().int().min(1).max(65_535).default(8080),
    GOOGLE_CLOUD_PROJECT: optionalNonEmpty,
    GCP_LOCATION: z.string().trim().min(1).default("europe-west4"),
    HEALTHCARE_DATASET_ID: optionalNonEmpty,
    SOURCE_FHIR_STORE_ID: optionalNonEmpty,
    TARGET_FHIR_STORE_ID: optionalNonEmpty,
    EVIDENCE_BUCKET: optionalNonEmpty,
    // The single bucket Zone A writes approved submissions to. Absent, the document source is
    // simply unavailable: this repository never reads a submission from anywhere else.
    SUBMISSION_BUCKET: optionalNonEmpty,
    ENABLED_RUN_SOURCES: EnabledRunSources,
    SUBMISSION_MAX_BYTES: z.coerce
      .number()
      .int()
      .min(1_024)
      .max(256 * 1_024 * 1_024)
      .default(64 * 1_024 * 1_024),
    FHIR_ANALYTICS_DATASET: optionalNonEmpty,
    TRANSFORMATION_LEDGER_DATASET: optionalNonEmpty,
    TRANSFORMATION_LEDGER_TABLE: z.string().trim().min(1).default("transformation_runs"),
    KMS_MANIFEST_KEY: optionalNonEmpty,
    FHIR_VALIDATOR_URL: z.url().optional(),
    DRY_RUN: z
      .enum(["true", "false"])
      .default("true")
      .transform((value) => value === "true"),
  })
  .superRefine((value, context) => {
    if (value.DRY_RUN) return;

    const required = [
      "GOOGLE_CLOUD_PROJECT",
      "HEALTHCARE_DATASET_ID",
      "SOURCE_FHIR_STORE_ID",
      "TARGET_FHIR_STORE_ID",
      "EVIDENCE_BUCKET",
      "FHIR_VALIDATOR_URL",
      "FHIR_ANALYTICS_DATASET",
    ] as const;

    for (const key of required) {
      if (value[key] === undefined) {
        context.addIssue({
          code: "custom",
          path: [key],
          message: `${key} is required when DRY_RUN=false`,
        });
      }
    }
  });

export type AppConfig = z.infer<typeof ConfigSchema>;

export function loadConfig(environment: NodeJS.ProcessEnv = process.env): AppConfig {
  return ConfigSchema.parse(environment);
}
