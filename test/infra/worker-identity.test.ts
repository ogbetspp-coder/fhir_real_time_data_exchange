import { describe, expect, it } from "vitest";

import { readInfra, serviceAccountRoles, terraformBlocks } from "../support/terraform.js";

// The worker's permissions, proven exhaustive (foundations C4; CMEK step 5c). Each role is bound on
// the narrowest resource that serves: a key, a bucket, a FHIR store, a table — never the project,
// except the two that exist only at project level. Until 2026-09-21 the worker edited FHIR
// resources in any dataset in the project and held a Document AI role nothing it runs uses; until
// 2026-09-27 it could edit the source store and delete or expire the ledger table.

const terraform = readInfra();
const block = (type: string, name: string) =>
  terraformBlocks(terraform).find((candidate) => candidate.type === type && candidate.name === name)
    ?.body ?? "";

describe("the worker identity", () => {
  const roles = serviceAccountRoles(terraform, "worker");

  it("holds exactly these roles, where each is bound", () => {
    expect(roles).toEqual(
      expect.arrayContaining([
        { type: "google_kms_crypto_key_iam_member", role: "roles/cloudkms.signerVerifier" },
        { type: "google_storage_bucket_iam_member", role: "roles/storage.objectCreator" },
        { type: "google_storage_bucket_iam_member", role: "roles/storage.objectViewer" },
        {
          type: "google_healthcare_fhir_store_iam_member",
          role: "roles/healthcare.fhirResourceReader",
        },
        {
          type: "google_healthcare_fhir_store_iam_member",
          role: "roles/healthcare.fhirResourceEditor",
        },
        {
          type: "google_bigquery_table_iam_member",
          role: "custom:bigquery.tables.get,bigquery.tables.updateData",
        },
        { type: "google_project_iam_member", role: "roles/datalineage.editor" },
        { type: "google_project_iam_member", role: "roles/logging.logWriter" },
      ]),
    );
    expect(roles).toHaveLength(8);
  });

  it("reads the source store and edits the validated store, and no other", () => {
    expect(roles.filter(({ role }) => role.startsWith("roles/healthcare."))).toEqual([
      {
        type: "google_healthcare_fhir_store_iam_member",
        role: "roles/healthcare.fhirResourceReader",
      },
      {
        type: "google_healthcare_fhir_store_iam_member",
        role: "roles/healthcare.fhirResourceEditor",
      },
    ]);
    expect(block("google_healthcare_fhir_store_iam_member", "worker_source_reader")).toMatch(
      /fhir_store_id\s*=\s*local\.source_fhir_store_path\s*\n\s*role\s*=\s*"roles\/healthcare\.fhirResourceReader"/,
    );
    expect(block("google_healthcare_fhir_store_iam_member", "worker_validated_editor")).toMatch(
      /fhir_store_id\s*=\s*local\.target_fhir_store_path\s*\n\s*role\s*=\s*"roles\/healthcare\.fhirResourceEditor"/,
    );
  });

  it("appends to the ledger table and cannot delete, expire or read it", () => {
    const grant = block("google_bigquery_table_iam_member", "worker_ledger_appender");
    expect(grant).toMatch(/table_id\s*=\s*google_bigquery_table\.transformation_runs\.table_id/);
    expect(roles.some(({ role }) => role.startsWith("roles/bigquery."))).toBe(false);
  });

  it("can create evidence but never read, overwrite or delete it (foundations C12)", () => {
    expect(roles.some(({ role }) => role === "roles/storage.objectAdmin")).toBe(false);
  });

  it("holds no Document AI role", () => {
    expect(roles.some(({ role }) => role.startsWith("roles/documentai."))).toBe(false);
  });
});

describe("the deploy identity's access to FHIR data", () => {
  it("is declared in Terraform and scoped to the one dataset", () => {
    // It was a grant made by hand on the old dataset, and was lost in the switch to the new one.
    const infra = readInfra();
    const grant =
      /resource "google_healthcare_dataset_iam_member" "deployer_fhir_editor" \{([\s\S]*?)\n\}/.exec(
        infra,
      )?.[1];
    expect(grant).toBeDefined();
    expect(grant).toContain("dataset_id = google_healthcare_dataset.record.id");
    expect(grant).toContain('role       = "roles/healthcare.fhirResourceEditor"');
    expect(grant).toContain('member     = "serviceAccount:${var.deployer_account}"');
  });
});
