import { describe, expect, it } from "vitest";

import { readInfra, serviceAccountRoles, terraformBlocks } from "../support/terraform.js";

// The approval signer's permissions, proven exhaustive (ADR 0004, decision 5;
// docs/design/approval.md, "Infrastructure and controls"): it alone signs with approval-signing-hsm,
// it writes the heads, the reviews and the statement copies and nothing else, and it can neither
// read nor write the FHIR store, the ledger or any other key.

const terraform = readInfra();
const blocks = terraformBlocks(terraform);
const block = (type: string, name: string) =>
  blocks.find((candidate) => candidate.type === type && candidate.name === name)?.body ?? "";

describe("the signer identity", () => {
  const roles = serviceAccountRoles(terraform, "signer");

  it("holds exactly these roles", () => {
    expect(roles).toEqual(
      expect.arrayContaining([
        { type: "google_kms_crypto_key_iam_member", role: "roles/cloudkms.signerVerifier" },
        { type: "google_storage_bucket_iam_member", role: "roles/storage.objectCreator" },
        { type: "google_storage_bucket_iam_member", role: "roles/storage.objectViewer" },
        { type: "google_project_iam_member", role: "roles/logging.logWriter" },
      ]),
    );
    // Create twice (heads; evidence, conditioned), read three times (heads; evidence `reviews/`,
    // conditioned; submissions), sign once, log once.
    expect(roles).toHaveLength(7);
    expect(roles.some(({ role }) => /healthcare|bigquery|objectAdmin|admin/i.test(role))).toBe(
      false,
    );
  });

  it("signs with the approval key, and the approval key alone", () => {
    expect(block("google_kms_crypto_key_iam_member", "signer_approval_signer")).toMatch(
      /crypto_key_id\s*=\s*google_kms_crypto_key\.approval_signing_hsm\.id\s*\n\s*role\s*=\s*"roles\/cloudkms\.signerVerifier"/,
    );
    const signing = blocks.filter(
      ({ type, body }) =>
        type === "google_kms_crypto_key_iam_member" &&
        body.includes("google_kms_crypto_key.approval_signing_hsm.id") &&
        body.includes("roles/cloudkms.signerVerifier"),
    );
    expect(signing.map(({ name }) => name)).toEqual(["signer_approval_signer"]);
  });

  it("creates in the evidence bucket under reviews/ and approvals/ only, and reads reviews/ only", () => {
    expect(block("google_storage_bucket_iam_member", "signer_evidence_creator")).toMatch(
      /expression\s*=\s*"resource\.name\.startsWith\(\\"\$\{local\.evidence_objects\}reviews\/\\"\) \|\| resource\.name\.startsWith\(\\"\$\{local\.evidence_objects\}approvals\/\\"\)"/,
    );
    expect(block("google_storage_bucket_iam_member", "signer_reviews_reader")).toMatch(
      /expression\s*=\s*"resource\.name\.startsWith\(\\"\$\{local\.evidence_objects\}reviews\/\\"\)"/,
    );
    expect(terraform).toMatch(
      /evidence_objects = "projects\/_\/buckets\/\$\{google_storage_bucket\.evidence\.name\}\/objects\/"/,
    );
  });

  it("is invoked by the Workspace add-on's service account alone, and by nobody until it exists", () => {
    const invokers = blocks.filter(
      ({ type, body }) =>
        type === "google_cloud_run_v2_service_iam_member" &&
        body.includes("google_cloud_run_v2_service.signer.name"),
    );
    expect(invokers.map(({ name }) => name)).toEqual(["signer_addon_invoker"]);
    expect(invokers[0]?.body).toMatch(
      /count\s*=\s*var\.approval_addon_service_account == "" \? 0 : 1/,
    );
  });

  it("runs the image by digest, as its own account, and names its image in every statement", () => {
    const service = block("google_cloud_run_v2_service", "signer");
    expect(service).toMatch(/service_account\s*=\s*google_service_account\.signer\.email/);
    expect(service).toMatch(/condition\s*=\s*local\.signer_image_digest != null/);
    expect(service).toMatch(
      /name\s*=\s*"IMAGE_DIGEST"\s*\n\s*value\s*=\s*local\.signer_image_digest/,
    );
  });
});

describe("the approval key", () => {
  it("is an HSM RSA-PSS 3072 SHA-256 signing key, never destroyed", () => {
    const key = block("google_kms_crypto_key", "approval_signing_hsm");
    expect(key).toMatch(/name\s*=\s*"approval-signing-hsm"/);
    expect(key).toMatch(/purpose\s*=\s*"ASYMMETRIC_SIGN"/);
    expect(key).toMatch(/algorithm\s*=\s*"RSA_SIGN_PSS_3072_SHA256"/);
    expect(key).toMatch(/protection_level\s*=\s*"HSM"/);
    expect(key).toMatch(/prevent_destroy = true/);
  });
});
