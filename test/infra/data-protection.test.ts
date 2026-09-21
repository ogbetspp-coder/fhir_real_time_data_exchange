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
  ["google_healthcare_dataset", "epi"],
  ["google_healthcare_dataset", "record"],
  ["google_bigquery_dataset", "ledger"],
  ["google_bigquery_table", "transformation_runs"],
  ["google_logging_project_bucket_config", "regulated_audit"],
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
