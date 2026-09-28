import { describe, expect, it } from "vitest";

import { readInfra } from "../support/terraform.js";

// Every service that touches the record, or answers from it, writes Data Access audit logs and is
// copied into the retained regulated audit log (foundations C6). The Gemini Enterprise connector
// runs in Discovery Engine; without it, the connector's side of each tool call was invisible.

const services = [
  "healthcare.googleapis.com",
  "storage.googleapis.com",
  "bigquery.googleapis.com",
  "cloudkms.googleapis.com",
  "discoveryengine.googleapis.com",
  // Enables the Data Access logs of the IAM Service Account Credentials API too: which person
  // minted a token as the shared caller account that query audit records name (audit I-5).
  "iam.googleapis.com",
];

// Retained beside the services above, though they hold no record: who was granted what, who
// minted a token as whom, what changed on the project, and any change to this trail itself. Their
// Admin Activity logs otherwise live 400 days in _Required, against years for the evidence.
const governance = [
  "iam.googleapis.com",
  "iamcredentials.googleapis.com",
  "cloudresourcemanager.googleapis.com",
  "logging.googleapis.com",
];

describe("the audit trail", () => {
  const infra = readInfra();
  const auditConfig =
    /resource "google_project_iam_audit_config" "regulated_data_access" \{[\s\S]*?\]\)/.exec(
      infra,
    )?.[0] ?? "";
  const sink =
    /resource "google_logging_project_sink" "regulated_audit" \{[\s\S]*?EOT\s*\n\}/.exec(
      infra,
    )?.[0] ?? "";

  it.each(services)("turns on Data Access logs for %s", (service) => {
    expect(auditConfig).toContain(`"${service}"`);
  });

  it.each([...new Set([...services, ...governance])])(
    "copies %s into the retained audit log",
    (service) => {
      expect(sink).toContain(`"${service}"`);
    },
  );
});
