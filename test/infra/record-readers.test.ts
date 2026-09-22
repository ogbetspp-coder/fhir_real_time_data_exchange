import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// Who may read the record (docs/foundations.md, C13): the services Terraform names, and the
// project's owners. Removing the owners' paths would lock the owner out, as it did on the state
// bucket; keeping the viewers' and editors' paths would let anyone given Viewer read the record.

const script = readFileSync("scripts/gcp/record-readers.sh", "utf8");
const workflow = readFileSync(".github/workflows/deploy.yml", "utf8");

describe("the record's readers", () => {
  it("lose project viewers and editors on every bucket, and keep project owners", () => {
    expect(script).toContain("if m.startswith(('projectViewer:','projectEditor:')):");
    expect(script).not.toMatch(/startswith\([^)]*projectOwner/);
  });

  it("lose project readers and writers on every dataset, and keep project owners", () => {
    expect(script).toContain('a.get("specialGroup") not in ("projectReaders", "projectWriters")');
    expect(script).not.toMatch(/not in \([^)]*projectOwners/);
  });

  it("cover the evidence, submissions, ledger and analytical copy", () => {
    expect(script).toContain("BUCKETS=(evidence submissions profiles build-staging)");
    expect(script).toContain('"ema_flow_ledger_${ENVIRONMENT}" "ema_flow_fhir_${ENVIRONMENT}"');
  });

  it("fail the deploy on any read error other than not-found", () => {
    expect(script).toContain('echo "${bucket}: cannot read its IAM policy');
    expect(script).toContain('echo "${dataset}: cannot read it');
    expect(script.match(/\n\s+exit 1\n/g)?.length ?? 0).toBeGreaterThanOrEqual(2);
  });

  it("replace a dataset's access list only if it is unchanged since read", () => {
    expect(script).toContain('update --etag "$etag" --source "$wanted"');
  });

  it("are enforced by every deploy, right after the apply", () => {
    const apply = workflow.indexOf("- name: Apply infrastructure");
    const readers = workflow.indexOf("- name: Record readers");
    const reconcile = workflow.indexOf("- name: Reconcile FHIR stores");
    expect(apply).toBeGreaterThan(0);
    expect(readers).toBeGreaterThan(apply);
    expect(reconcile).toBeGreaterThan(readers);
  });
});
