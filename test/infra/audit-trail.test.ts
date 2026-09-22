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

  it.each(services)("copies %s into the retained audit log", (service) => {
    expect(sink).toContain(`"${service}"`);
  });
});
