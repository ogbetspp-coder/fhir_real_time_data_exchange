import { describe, expect, it } from "vitest";

import {
  readInfra,
  serviceAccountRoles,
  terraformBlocks,
  withoutResources,
} from "../support/terraform.js";
import { TRANSITIONAL_GRANTS } from "./transitional-grants.js";

// The worker's permissions, proven exhaustive (foundations C4; CMEK step 5c). Each role is bound on
// the narrowest resource that serves: a key, a bucket, a FHIR store, a table — never the project,
// except the two that exist only at project level. Until 2026-09-21 the worker edited FHIR
// resources in any dataset in the project and held a Document AI role nothing it runs uses. It can
// still edit the source store and delete or expire the ledger table through two transitional
// grants (audit B04, phase 1); the tests below prove the set that remains once they are removed.

const terraform = readInfra();
const block = (type: string, name: string) =>
  terraformBlocks(terraform).find((candidate) => candidate.type === type && candidate.name === name)
    ?.body ?? "";

describe("the worker identity during the transition (audit B04, phase 1)", () => {
  it("holds the least-privilege set plus exactly the two transitional grants", () => {
    const now = serviceAccountRoles(terraform, "worker");
    const after = serviceAccountRoles(withoutResources(terraform, TRANSITIONAL_GRANTS), "worker");
    const extra = [...now];
    for (const role of after) {
      const at = extra.findIndex(
        ({ type, role: name }) => type === role.type && name === role.role,
      );
      expect(at).toBeGreaterThanOrEqual(0);
      extra.splice(at, 1);
    }
    expect(extra).toEqual([
      { type: "google_healthcare_dataset_iam_member", role: "roles/healthcare.fhirResourceEditor" },
      { type: "google_bigquery_dataset_iam_member", role: "roles/bigquery.dataEditor" },
    ]);
  });
});

describe("the worker identity once the transitional grants are removed", () => {
  const roles = serviceAccountRoles(withoutResources(terraform, TRANSITIONAL_GRANTS), "worker");

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
        { type: "google_kms_crypto_key_iam_member", role: "roles/cloudkms.publicKeyViewer" },
      ]),
    );
    // objectViewer three times: the submissions, the approval heads and the Word drawing records.
    expect(roles).toHaveLength(11);
  });

  // It verifies a document's head before it publishes (docs/design/approval.md, D8): it reads the
  // heads and the approval key's public keys, and can neither sign an approval nor write a head.
  it("reads approvals, and can neither sign nor write one", () => {
    expect(block("google_storage_bucket_iam_member", "worker_heads_reader")).toMatch(
      /bucket\s*=\s*google_storage_bucket\.approval_heads\.name\s*\n\s*role\s*=\s*"roles\/storage\.objectViewer"/,
    );
    expect(block("google_kms_crypto_key_iam_member", "worker_approval_public_key")).toMatch(
      /crypto_key_id\s*=\s*google_kms_crypto_key\.approval_signing_hsm\.id\s*\n\s*role\s*=\s*"roles\/cloudkms\.publicKeyViewer"/,
    );
    const signers = roles.filter(({ role }) => role === "roles/cloudkms.signerVerifier");
    expect(signers).toHaveLength(1);
    expect(block("google_kms_crypto_key_iam_member", "worker_manifest_signer_hsm")).toMatch(
      /crypto_key_id\s*=\s*google_kms_crypto_key\.manifest_signing_hsm\.id/,
    );
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

describe("the store each store-level grant names", () => {
  it("is the source store for the source path and the validated store for the target path", () => {
    // A swap here would give the query service the unvalidated store and the worker write access
    // to its own input, while every grant still read "store-level".
    const paths = Object.fromEntries(
      [...terraform.matchAll(/^\s*(source|target)_fhir_store_path\s*=\s*"([^"\n]+)"$/gm)].map(
        (match) => [match[1] ?? "", match[2] ?? ""],
      ),
    );
    const prefix = "${var.project_id}/${var.region}/${google_healthcare_dataset.record.name}/";
    expect(paths).toEqual({
      source: `${prefix}\${local.source_fhir_store_id}`,
      target: `${prefix}\${local.target_fhir_store_id}`,
    });
    expect(terraform).toMatch(/source_fhir_store_id = "\$\{local\.name_prefix\}-source-r5"/);
    expect(terraform).toMatch(/target_fhir_store_id = "\$\{local\.name_prefix\}-validated-r5"/);
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
