import { z } from "zod";

import { ApprovalEnvironment } from "./contracts/approval.js";
import { GitCommit, ImageDigest, Token } from "./contracts/common.js";
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

// The run sources that bypass the document gate (ADR 0002 consequences).
const UNGATED_SOURCES: readonly RunSource[] = ["fixture", "healthcare-api"];

// A comma-separated subset of RUN_SOURCES; an unknown name or an empty list is a startup failure,
// never a silently ignored entry. Unset, it follows ALLOW_SYNTHETIC_SOURCES (below).
const EnabledRunSources = z
  .string()
  .optional()
  .transform((value) => value?.split(",").map((entry) => entry.trim()))
  .pipe(z.array(z.enum(RUN_SOURCES)).min(1).optional())
  .transform((sources) =>
    sources === undefined ? undefined : ([...new Set(sources)] as readonly RunSource[]),
  );

// Cloud KMS signs with a crypto key VERSION; a name that stops at the crypto key is refused by
// KMS with an opaque error, after the run's other work is done. Checked at startup instead.
const KMS_KEY_VERSION =
  /^projects\/[^/]+\/locations\/[^/]+\/keyRings\/[^/]+\/cryptoKeys\/[^/]+\/cryptoKeyVersions\/[^/]+$/;

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
    // Whether this deployment accepts synthetic content (docs/design/authority-import-contract.md,
    // D7). Off by default: then the gate refuses anything synthetic, the ungated sources default
    // to off, and enabling one is a startup failure.
    ALLOW_SYNTHETIC_SOURCES: z
      .enum(["true", "false"])
      .default("false")
      .transform((value) => value === "true"),
    // The most bytes one document run reads, across all three parts together (the submission,
    // its fidelity report and its extracted text). Sized to the worker container: parsed and
    // hashed, a byte read costs several in memory (192 MiB read measured 646 MB RSS), and the
    // worker runs four requests at once in 1 GiB (infra/run.tf).
    SUBMISSION_MAX_BYTES: z.coerce
      .number()
      .int()
      .min(1_024)
      .max(256 * 1_024 * 1_024)
      .default(48 * 1_024 * 1_024),
    FHIR_ANALYTICS_DATASET: optionalNonEmpty,
    TRANSFORMATION_LEDGER_DATASET: optionalNonEmpty,
    TRANSFORMATION_LEDGER_TABLE: z.string().trim().min(1).default("transformation_runs"),
    KMS_MANIFEST_KEY: z
      .string()
      .trim()
      .regex(KMS_KEY_VERSION, "KMS_MANIFEST_KEY must name a crypto key version")
      .optional(),
    FHIR_VALIDATOR_URL: z.url().optional(),
    // Whether a persisted document run publishes only under the document's signed head statement,
    // and links the version it wrote to it (docs/design/approval.md, D8 and D5). Off by default, and
    // off in every deploy until the demonstration documents are approved through the signer (the
    // design's step 6, which turns this and the query service's verification on together): with it
    // on, a document run without a valid head approval is refused `not-approved`.
    APPROVAL_ENFORCEMENT: z
      .enum(["on", "off"])
      .default("off")
      .transform((value) => value === "on"),
    // When it is on: the environment every statement it accepts names, the heads bucket it reads
    // the head from, and the one approval key version it trusts (a statement signed by any other
    // version, of this key or another, is refused).
    APPROVAL_ENVIRONMENT: ApprovalEnvironment.optional(),
    APPROVAL_HEADS_BUCKET: optionalNonEmpty,
    APPROVAL_SIGNING_KEY_VERSION: z
      .string()
      .trim()
      .regex(KMS_KEY_VERSION, "APPROVAL_SIGNING_KEY_VERSION must name a crypto key version")
      .optional(),
    // The code and the images a run manifest names (`runtime`, run manifest 5.0.0), each in its
    // own grammar: infra/run.tf sets the commit (`service_version`, which scripts/gcp/deploy.sh
    // passes as the full git SHA) and both image digests; Cloud Run sets K_REVISION. A value in
    // another shape is a startup failure, never a manifest that cannot be signed. Unset (off
    // Cloud Run), the manifest records `development`.
    GIT_COMMIT: GitCommit.optional(),
    IMAGE_DIGEST: ImageDigest.optional(),
    VALIDATOR_IMAGE_DIGEST: ImageDigest.optional(),
    WORKFLOW_REVISION: Token.optional(),
    K_REVISION: Token.optional(),
    DRY_RUN: z
      .enum(["true", "false"])
      .default("true")
      .transform((value) => value === "true"),
  })
  .superRefine((value, context) => {
    const ungated = (value.ENABLED_RUN_SOURCES ?? []).filter((source) =>
      UNGATED_SOURCES.includes(source),
    );
    if (!value.ALLOW_SYNTHETIC_SOURCES && ungated.length > 0) {
      context.addIssue({
        code: "custom",
        path: ["ENABLED_RUN_SOURCES"],
        message: `${ungated.join(", ")} bypass the document gate and need ALLOW_SYNTHETIC_SOURCES=true`,
      });
    }
    if (value.DRY_RUN) return;

    const required = [
      "GOOGLE_CLOUD_PROJECT",
      "HEALTHCARE_DATASET_ID",
      "SOURCE_FHIR_STORE_ID",
      "TARGET_FHIR_STORE_ID",
      "EVIDENCE_BUCKET",
      "FHIR_VALIDATOR_URL",
      "FHIR_ANALYTICS_DATASET",
      // A persisted run's manifest is signed and its ledger row written, always; there is no
      // unsigned or unrecorded persisted run.
      "KMS_MANIFEST_KEY",
      "TRANSFORMATION_LEDGER_DATASET",
    ] as const;

    // With APPROVAL_ENFORCEMENT on, every persisted run is verified against its approval: there is
    // no unapproved publication, and no deployment that cannot check.
    const approval = value.APPROVAL_ENFORCEMENT
      ? (["APPROVAL_ENVIRONMENT", "APPROVAL_HEADS_BUCKET", "APPROVAL_SIGNING_KEY_VERSION"] as const)
      : [];

    for (const key of [...required, ...approval]) {
      if (value[key] === undefined) {
        context.addIssue({
          code: "custom",
          path: [key],
          message: `${key} is required when DRY_RUN=false`,
        });
      }
    }
  });

const ResolvedConfigSchema = ConfigSchema.transform((value) => ({
  ...value,
  ENABLED_RUN_SOURCES:
    value.ENABLED_RUN_SOURCES ??
    (value.ALLOW_SYNTHETIC_SOURCES ? [...RUN_SOURCES] : (["document"] as readonly RunSource[])),
}));

export type AppConfig = z.infer<typeof ResolvedConfigSchema>;

export function loadConfig(environment: NodeJS.ProcessEnv = process.env): AppConfig {
  return ResolvedConfigSchema.parse(environment);
}
