import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { readInfra, terraformBlocks } from "../support/terraform.js";

// The resources that hold a record are never destroyed by an apply (docs/design/cmek-rollout.md,
// step 0). The deploy applies without a person reading the plan, and the provider treats several
// ordinary-looking edits — a retyped ledger column, an encryption key, a renamed dataset — as
// "destroy and create". Until 2026-09-21 nothing stopped that: deletion protection defaulted to
// false and none of these carried prevent_destroy. With these in place such a plan fails.

const terraform = readInfra();
const blocks = terraformBlocks(terraform);

const records: [type: string, name: string][] = [
  ["google_healthcare_dataset", "record"],
  ["google_bigquery_dataset", "ledger"],
  ["google_bigquery_table", "transformation_runs"],
  ["google_logging_project_bucket_config", "regulated_audit"],
  ["google_logging_project_bucket_config", "regulated_audit_cmek"],
  ["google_storage_bucket", "evidence"],
  ["google_storage_bucket", "submissions"],
  ["google_kms_crypto_key", "evidence_encryption"],
  ["google_kms_crypto_key", "manifest_signing"],
  ["google_kms_crypto_key", "record"],
  ["google_kms_crypto_key", "manifest_signing_hsm"],
];

describe("resources that hold a record", () => {
  it.each(records)("%s.%s cannot be destroyed by an apply", (type, name) => {
    const block = blocks.find((candidate) => candidate.type === type && candidate.name === name);
    expect(block, `${type}.${name} is declared`).toBeDefined();
    expect(block?.body).toMatch(/lifecycle\s*\{[^}]*prevent_destroy\s*=\s*true/);
  });

  it("the ledger table and the services are deletion-protected by default", () => {
    const variables = readFileSync("infra/variables.tf", "utf8");
    const variable = /variable "deletion_protection" \{([\s\S]*?)\n\}/.exec(variables)?.[1] ?? "";
    expect(variable).toMatch(/default\s*=\s*true/);
    const ledger = blocks.find(({ name }) => name === "transformation_runs");
    expect(ledger?.body).toMatch(/deletion_protection\s*=\s*var\.deletion_protection/);
  });
});

// No bucket can be made public (audit I-8). Until 2026-09-27 only the build staging bucket
// enforced it; a single allUsers grant on the evidence or submission bucket would have published
// it.
describe("every bucket", () => {
  const buckets = blocks.filter(({ type }) => type === "google_storage_bucket");

  it.each(buckets.map(({ name, body }) => [name, body]))(
    "%s enforces public access prevention",
    (_name, body) => {
      expect(body).toMatch(/^\s*public_access_prevention\s*=\s*"enforced"\s*$/m);
    },
  );

  it("includes the four Terraform creates, and the ones it does not are enforced by script", () => {
    expect(buckets.map(({ name }) => name).sort()).toEqual([
      "build_staging",
      "evidence",
      "profiles",
      "submissions",
    ]);
    // The state bucket, created by deploy.sh before Terraform exists (on its key, too: audit
    // I-10, test/infra/fresh-environment.test.ts), and the agent staging bucket, created by hand.
    const deploy = readFileSync("scripts/gcp/deploy.sh", "utf8");
    expect(deploy).toMatch(
      /gcloud --quiet storage buckets create "gs:\/\/\$\{bucket\}"[^;]*--public-access-prevention[^;]*--default-encryption-key="\$key"/,
    );
    const keys = readFileSync("scripts/gcp/storage-keys.sh", "utf8");
    expect(keys).toContain("--public-access-prevention >/dev/null");
    expect(keys).toMatch(/BUCKETS=\("\$STATE_BUCKET" "\$\{PROJECT_ID\}-ema-flow-agent-staging"\)/);
  });
});

describe("the profiles bucket", () => {
  it("expires old generations only, never the live profile set or the import marker", () => {
    // An unscoped `age = 30` rule deleted the import-fingerprint marker every month, and the next
    // deploy re-staged and re-imported every profile for nothing (audit I-9).
    const profiles = blocks.find(
      ({ type, name }) => type === "google_storage_bucket" && name === "profiles",
    );
    const rules = [...(profiles?.body ?? "").matchAll(/lifecycle_rule \{([\s\S]*?)\n {2}\}/g)].map(
      (match) => match[1] ?? "",
    );
    expect(rules).toHaveLength(1);
    expect(rules[0]).toMatch(/condition \{\s*days_since_noncurrent_time = 30\s*\}/);
    expect(rules[0]).not.toMatch(/\bage\s*=/);
  });
});
