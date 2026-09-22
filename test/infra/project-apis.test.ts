import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// The project's APIs (docs/foundations.md, C2). infra/main.tf is the only list of them, and
// scripts/gcp/api-trim.sh disables what was measured unused and reports anything else enabled.

const main = readFileSync("infra/main.tf", "utf8");
const block =
  /resource "google_project_service" "required" \{[\s\S]*?\]\)[\s\S]*?\n\}/.exec(main)?.[0] ?? "";
const declared = [...block.matchAll(/"([a-z0-9-]+\.googleapis\.com)"/g)].map((m) => m[1] ?? "");
const trim = readFileSync("scripts/gcp/api-trim.sh", "utf8");
const deploy = readFileSync("scripts/gcp/deploy.sh", "utf8");
const bootstrapEnabled = [
  ...(
    /gcloud --quiet services enable \\\n([\s\S]*?)\n\s*--project/.exec(deploy)?.[1] ?? ""
  ).matchAll(/([a-z0-9-]+\.googleapis\.com)/g),
].map((m) => m[1] ?? "");
const unused = [
  ...(/^UNUSED=\(\n([\s\S]*?)\n\)$/m.exec(trim)?.[1] ?? "").matchAll(/^\s+(\S+)$/gm),
].map((m) => m[1] ?? "");

describe("the project's APIs", () => {
  it("are declared once each", () => {
    expect(declared.length).toBeGreaterThan(20);
    expect(new Set(declared).size).toBe(declared.length);
  });

  it("include everything the product calls", () => {
    for (const api of [
      "healthcare.googleapis.com",
      "bigquery.googleapis.com",
      "cloudkms.googleapis.com",
      "run.googleapis.com",
      "storage.googleapis.com",
      "workflows.googleapis.com",
      "discoveryengine.googleapis.com",
    ]) {
      expect(declared).toContain(api);
    }
  });

  it("are the only list: what the deploy enables before Terraform runs is a subset", () => {
    expect(bootstrapEnabled.length).toBeGreaterThan(5);
    expect(bootstrapEnabled.filter((api) => !declared.includes(api))).toEqual([]);
  });

  it("are never disabled by Terraform, so removing one from the list is not an outage", () => {
    expect(block).toContain("disable_on_destroy = false");
  });

  it("never disable one that is declared", () => {
    expect(unused.length).toBeGreaterThan(5);
    expect(unused.filter((api) => declared.includes(api))).toEqual([]);
  });
});
