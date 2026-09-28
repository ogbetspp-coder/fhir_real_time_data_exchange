import { describe, expect, it } from "vitest";

import { serviceAccountRoles, terraformBlocks } from "./terraform.js";

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

  it("does not count a brace inside a comment, a string or a heredoc", () => {
    // The audit's repro: an unmatched { in a comment made the bucket grant swallow the next
    // block, and a project-level roles/owner was reported as a bucket objectViewer grant.
    const owner = `
resource "google_project_iam_member" "owner" {
  project = var.project_id
  role    = "roles/owner"
  member  = "serviceAccount:\${google_service_account.build.email}"
}
`;
    for (const decoy of [
      "  # see docs {section 3",
      "  // see docs {section 3",
      "  /* see docs {section 3 */",
      '  description = "see docs {section 3"',
      '  description = "${join("}", ["{"])}"',
      "  description = <<-EOT\n    see docs {section 3\n  EOT",
    ]) {
      const terraform = `${account}
resource "google_storage_bucket_iam_member" "reader" {
  bucket = google_storage_bucket.staging.name
${decoy}
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:\${google_service_account.build.email}"
}
${owner}`;
      expect([decoy, serviceAccountRoles(terraform, "build")]).toEqual([
        decoy,
        [
          { type: "google_storage_bucket_iam_member", role: "roles/storage.objectViewer" },
          { type: "google_project_iam_member", role: "roles/owner" },
        ],
      ]);
    }
  });

  it("takes the role from code, never from a comment or a heredoc", () => {
    const terraform = `${account}
resource "google_project_iam_member" "log" {
  description = <<-EOT
  role = "roles/logging.logWriter"
  EOT
  project = var.project_id
  # role = "roles/logging.logWriter"
  role    = "roles/owner"
  member  = "serviceAccount:\${google_service_account.build.email}"
}
`;
    expect(serviceAccountRoles(terraform, "build")).toEqual([
      { type: "google_project_iam_member", role: "roles/owner" },
    ]);
  });

  it("does not count a reference that is only mentioned in a comment", () => {
    const terraform = `${account}
# google_service_account.build.email is granted below
resource "google_project_iam_member" "log" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:\${google_service_account.build.email}"
}
`;
    expect(serviceAccountRoles(terraform, "build")).toEqual([
      { type: "google_project_iam_member", role: "roles/logging.logWriter" },
    ]);
  });

  it("refuses unbalanced input and anything it cannot read at the top level", () => {
    const grant = `
resource "google_project_iam_member" "log" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:\${google_service_account.build.email}"
`;
    expect(() => serviceAccountRoles(`${account}${grant}`, "build")).toThrow(/never closes/);
    expect(() => serviceAccountRoles(`${account}${grant}}\n}\n`, "build")).toThrow(
      /unbalanced braces|unexpected top-level text/,
    );
    expect(() => terraformBlocks(`${account}resource "x" "y" {\n  a = "open\n}\n`)).toThrow(
      /unterminated string/,
    );
    expect(() => terraformBlocks(`${account}resource "x" "y" {\n  a = <<EOT\n  b\n}\n`)).toThrow(
      /unterminated heredoc/,
    );
    expect(() => terraformBlocks(`${account}/* open\n`)).toThrow(/unterminated \/\* comment/);
    expect(() => terraformBlocks(`${account}stray = 1\n`)).toThrow(/unexpected top-level text/);
  });

  it("refuses a grant with two role assignments", () => {
    const terraform = `${account}
resource "google_project_iam_member" "two" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:\${google_service_account.build.email}"
  condition {
    role = "roles/owner"
  }
}
`;
    expect(() => serviceAccountRoles(terraform, "build")).toThrow(/2 role assignments/);
  });
});

describe("a custom role", () => {
  const grant = `
resource "google_bigquery_table_iam_member" "append" {
  table_id = google_bigquery_table.t.table_id
  role     = google_project_iam_custom_role.appender.name
  member   = "serviceAccount:\${google_service_account.build.email}"
}
`;

  it("is read as its permissions, sorted", () => {
    const terraform = `${account}${grant}
resource "google_project_iam_custom_role" "appender" {
  role_id     = "appender_\${var.environment}"
  permissions = ["bigquery.tables.updateData", "bigquery.tables.get"]
}
`;
    expect(serviceAccountRoles(terraform, "build")).toEqual([
      {
        type: "google_bigquery_table_iam_member",
        role: "custom:bigquery.tables.get,bigquery.tables.updateData",
      },
    ]);
  });

  it("is refused when its permissions are not literal, or it is not declared", () => {
    const computed = `${account}${grant}
resource "google_project_iam_custom_role" "appender" {
  role_id     = "appender"
  permissions = local.permissions
}
`;
    expect(() => serviceAccountRoles(computed, "build")).toThrow(/quoted literals/);
    expect(() => serviceAccountRoles(`${account}${grant}`, "build")).toThrow(/not declared/);
    const many = `${account}${grant}
resource "google_project_iam_custom_role" "appender" {
  for_each    = toset(["a"])
  role_id     = "appender"
  permissions = ["bigquery.tables.get"]
}
`;
    expect(() => serviceAccountRoles(many, "build")).toThrow(/count or for_each/);
  });
});

describe("terraformBlocks", () => {
  it("returns each resource's body without its comments", () => {
    const blocks = terraformBlocks(`
resource "google_kms_crypto_key" "k" {
  lifecycle {
    # prevent_destroy = true
    prevent_destroy = false
  }
}
`);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]?.body).not.toMatch(/prevent_destroy\s*=\s*true/);
  });
});
