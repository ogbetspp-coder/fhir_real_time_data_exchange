import { describe, expect, it } from "vitest";

import { readInfra, serviceAccountRoles } from "../support/terraform.js";

// The worker's permissions, proven exhaustive (foundations C4; CMEK step 5c). Each role is bound on
// the narrowest resource that serves: a key, a bucket, a dataset — never the project, except the
// two that exist only at project level. Until 2026-09-21 the worker edited FHIR resources in any
// dataset in the project and held a Document AI role nothing it runs uses.

describe("the worker identity", () => {
  const roles = serviceAccountRoles(readInfra(), "worker");

  it("holds exactly these roles, where each is bound", () => {
    expect(roles).toEqual(
      expect.arrayContaining([
        { type: "google_kms_crypto_key_iam_member", role: "roles/cloudkms.signerVerifier" },
        { type: "google_storage_bucket_iam_member", role: "roles/storage.objectAdmin" },
        { type: "google_storage_bucket_iam_member", role: "roles/storage.objectViewer" },
        {
          type: "google_healthcare_dataset_iam_member",
          role: "roles/healthcare.fhirResourceEditor",
        },
        { type: "google_bigquery_dataset_iam_member", role: "roles/bigquery.dataEditor" },
        { type: "google_project_iam_member", role: "roles/datalineage.editor" },
        { type: "google_project_iam_member", role: "roles/logging.logWriter" },
      ]),
    );
    expect(roles).toHaveLength(7);
  });

  it("edits FHIR resources in one dataset, never across the project", () => {
    expect(roles.filter(({ role }) => role.startsWith("roles/healthcare."))).toEqual([
      { type: "google_healthcare_dataset_iam_member", role: "roles/healthcare.fhirResourceEditor" },
    ]);
  });

  it("holds no Document AI role", () => {
    expect(roles.some(({ role }) => role.startsWith("roles/documentai."))).toBe(false);
  });
});
