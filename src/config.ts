import { z } from "zod";

const optionalNonEmpty = z.string().trim().min(1).optional();

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
