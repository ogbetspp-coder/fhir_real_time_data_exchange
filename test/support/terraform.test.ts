import { describe, expect, it } from "vitest";

import { serviceAccountRoles } from "./terraform.js";

// The reader every least-privilege test relies on. Each evasion below would once have been
// silently skipped, so "exactly these roles" would have passed while being false. Each must now
// either be counted or make the reader throw — never be dropped.

const account = `
resource "google_service_account" "build" {
  account_id   = "ema-flow-build-\${var.environment}"
  display_name = "builds"
}
`;

describe("serviceAccountRoles", () => {
  it("counts a grant by reference and ignores a grant over the account", () => {
    const terraform = `${account}
resource "google_project_iam_member" "log" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:\${google_service_account.build.email}"
}
resource "google_service_account_iam_member" "actas" {
  service_account_id = google_service_account.build.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:\${var.deployer_account}"
}
resource "google_cloud_run_v2_service" "svc" {
  template {
    service_account = google_service_account.build.email
  }
}
output "who" {
  value = google_service_account.build.email
}
`;
    expect(serviceAccountRoles(terraform, "build")).toEqual([
      { type: "google_project_iam_member", role: "roles/logging.logWriter" },
    ]);
  });

  it("refuses a role it cannot read", () => {
    const terraform = `${account}
resource "google_project_iam_member" "many" {
  for_each = toset(["roles/owner"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:\${google_service_account.build.email}"
}
`;
    expect(() => serviceAccountRoles(terraform, "build")).toThrow(/non-literal role/);
  });

  it("refuses a reference routed through a local", () => {
    const terraform = `${account}
locals {
  builder = "serviceAccount:\${google_service_account.build.email}"
}
resource "google_project_iam_member" "sneaky" {
  project = var.project_id
  role    = "roles/owner"
  member  = local.builder
}
`;
    expect(() => serviceAccountRoles(terraform, "build")).toThrow(/locals block/);
  });

  it("refuses a grant that names the account by literal e-mail", () => {
    const terraform = `${account}
resource "google_project_iam_member" "literal" {
  project = var.project_id
  role    = "roles/owner"
  member  = "serviceAccount:ema-flow-build-dev@p.iam.gserviceaccount.com"
}
`;
    expect(() => serviceAccountRoles(terraform, "build")).toThrow(/literal e-mail/);
  });

  it("refuses a policy assembled in a data source", () => {
    const terraform = `${account}
data "google_iam_policy" "p" {
  binding {
    role    = "roles/owner"
    members = ["serviceAccount:\${google_service_account.build.email}"]
  }
}
`;
    expect(() => serviceAccountRoles(terraform, "build")).toThrow(/data block|google_iam_policy/);
  });

  it("refuses a reference on a line of an IAM resource it does not recognise", () => {
    const terraform = `${account}
resource "google_project_iam_binding" "odd" {
  project = var.project_id
  role    = "roles/owner"
  members = concat([], [google_service_account.build.member])
  condition_hint = google_service_account.build.email
}
`;
    expect(() => serviceAccountRoles(terraform, "build")).toThrow(/unrecognised line/);
  });
});
