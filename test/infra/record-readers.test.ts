import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { readInfra, terraformBlocks } from "../support/terraform.js";

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

  it("cover every bucket and dataset infra/ declares, as the Terraform output lists them", () => {
    const outputs = readFileSync("infra/outputs.tf", "utf8");
    const targets = /^output "record_readers_targets" \{\n([\s\S]*?)^\}/m.exec(outputs)?.[1] ?? "";
    const blocks = terraformBlocks(readInfra());
    const buckets = blocks
      .filter(({ type }) => type === "google_storage_bucket")
      .map(({ name }) => `google_storage_bucket.${name}.name`);
    const datasets = blocks
      .filter(({ type }) => type === "google_bigquery_dataset")
      .map(({ name }) => `google_bigquery_dataset.${name}.dataset_id`);
    expect(buckets.length).toBeGreaterThanOrEqual(4);
    expect(datasets.length).toBeGreaterThanOrEqual(2);
    const listed = (key: string) =>
      [
        ...(new RegExp(`${key} = \\[([\\s\\S]*?)\\]`).exec(targets)?.[1] ?? "").matchAll(
          /(google_[\w.]+)/g,
        ),
      ].map((match) => match[1]);
    expect(listed("buckets").sort()).toEqual(buckets.sort());
    expect(listed("datasets").sort()).toEqual(datasets.sort());
    expect(script).toContain("terraform -chdir=infra output -json record_readers_targets");
  });

  it("replace a dataset's access list only if it is unchanged since read", () => {
    expect(script).toContain('--header "If-Match: ${etag}"');
    expect(script).toContain("--request PATCH");
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

// The script run for real against stand-in terraform, gcloud and curl (audit B08, D-5). Until then
// it read GCP_PROJECT_ID first where deploy.sh read GOOGLE_CLOUD_PROJECT first, and skipped every
// bucket and dataset it could not find: with the two set to different projects, the apply ran in
// one while this narrowed nothing in the other, and exited 0.
const dirs: string[] = [];
afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

const TARGETS = {
  buckets: [
    "p-one-ema-flow-dev-evidence",
    "p-one-ema-flow-dev-submissions",
    "p-one-ema-flow-dev-profiles",
    "p-one-ema-flow-dev-build-staging",
  ],
  datasets: ["ema_flow_ledger_dev", "ema_flow_fhir_dev"],
};
const TOKEN = "ya29.access-token-never-in-argv";

type Options = { missing?: string[]; targets?: object | null; check?: boolean };

function readers(env: Record<string, string>, options: Options = {}) {
  const { missing = [], targets = TARGETS, check = false } = options;
  const dir = mkdtempSync(path.join(tmpdir(), "record-readers-"));
  dirs.push(dir);
  const calls = path.join(dir, "calls.log");
  writeFileSync(calls, "");
  writeFileSync(path.join(dir, "targets.json"), JSON.stringify(targets));
  writeFileSync(path.join(dir, "missing"), missing.map((name) => `${name}\n`).join(""));
  const stubs: Record<string, string> = {
    terraform: `echo "terraform $*" >>"${calls}"
[ "$*" = "-chdir=infra output -json record_readers_targets" ] || exit 2
[ -s "${dir}/targets.json" ] && [ "$(cat "${dir}/targets.json")" != "null" ] || exit 1
cat "${dir}/targets.json"`,
    gcloud: `echo "gcloud $*" >>"${calls}"
case "$*" in
  *"config get-value project"*) echo "(unset)" ;;
  *"print-access-token"*) printf '%s' '${TOKEN}' ;;
  *"storage buckets get-iam-policy"*)
    bucket="\${5#gs://}"
    if grep -qx "$bucket" "${dir}/missing"; then echo "ERROR: (gcloud.storage) 404 not found" >&2; exit 1; fi
    echo '{"bindings":[{"role":"roles/storage.legacyBucketReader","members":["projectViewer:p-one"]}]}' ;;
  *"storage buckets remove-iam-policy-binding"*) ;;
  *"projects get-iam-policy"*) echo '{"bindings":[]}' ;;
  *) echo "unexpected gcloud $*" >&2; exit 2 ;;
esac`,
    curl: `printf 'curl' >>"${calls}"; for a in "$@"; do printf ' [%s]' "$a" >>"${calls}"; done; echo >>"${calls}"
out=""; url=""; auth=""
while [ $# -gt 0 ]; do
  case "$1" in
    --output) out="$2"; shift 2 ;;
    --header) case "$2" in @*) auth="$(cat "\${2#@}")" ;; esac; shift 2 ;;
    --write-out|--request|--data-binary) shift 2 ;;
    --*) shift ;;
    *) url="$1"; shift ;;
  esac
done
[ "$auth" = "Authorization: Bearer ${TOKEN}" ] || { echo "no token in the header file" >&2; exit 2; }
dataset="\${url##*/}"
if grep -qx "$dataset" "${dir}/missing"; then printf 404; exit 0; fi
[ -n "$out" ] && printf '%s' '{"access":[{"specialGroup":"projectReaders","role":"READER"}],"etag":"e1"}' >"$out"
printf 200`,
  };
  for (const [name, body] of Object.entries(stubs)) {
    writeFileSync(path.join(dir, name), `#!/usr/bin/env bash\n${body}\n`);
    chmodSync(path.join(dir, name), 0o755);
  }
  const result = spawnSync(
    "bash",
    ["scripts/gcp/record-readers.sh", ...(check ? ["--check"] : [])],
    {
      encoding: "utf8",
      env: { PATH: `${dir}:${process.env.PATH ?? ""}`, ...env },
    },
  );
  return {
    status: result.status,
    out: `${result.stdout}${result.stderr}`,
    calls: readFileSync(calls, "utf8"),
  };
}

describe("the record's readers, run", { timeout: 30_000 }, () => {
  it("narrows every declared bucket and dataset in the resolved project", () => {
    const result = readers({ GOOGLE_CLOUD_PROJECT: "p-one", GCP_PROJECT_ID: "p-one" });
    expect([result.status, result.out]).toEqual([0, expect.any(String)]);
    for (const bucket of TARGETS.buckets) {
      expect(result.calls).toContain(`remove-iam-policy-binding gs://${bucket}`);
    }
    expect(result.calls.match(/\[--request\] \[PATCH\]/g)).toHaveLength(2);
    expect(result.out).toContain("p-one-ema-flow-agent-staging");
  });

  // Audit I-10: until then project viewers could read the state until storage-keys.sh was run.
  it("narrows the Terraform state bucket too, which deploy.sh makes outside Terraform", () => {
    const result = readers({ GOOGLE_CLOUD_PROJECT: "p-one" });
    expect(result.status).toBe(0);
    expect(result.calls).toContain("remove-iam-policy-binding gs://p-one-ema-flow-tfstate");
  });

  it("refuses two variables that name different projects, before touching anything", () => {
    const result = readers({ GOOGLE_CLOUD_PROJECT: "p-two", GCP_PROJECT_ID: "p-one" });
    expect(result.status).not.toBe(0);
    expect(result.out).toContain("name different projects");
    expect(result.calls).not.toContain("storage buckets");
  });

  it("fails when a bucket infra/ declares does not exist, instead of skipping it", () => {
    const result = readers(
      { GOOGLE_CLOUD_PROJECT: "p-one" },
      { missing: ["p-one-ema-flow-dev-evidence"] },
    );
    expect(result.status).not.toBe(0);
    expect(result.out).toContain(
      "p-one-ema-flow-dev-evidence: declared in infra/ but does not exist",
    );
  });

  it("fails when a dataset infra/ declares does not exist", () => {
    const result = readers({ GOOGLE_CLOUD_PROJECT: "p-one" }, { missing: ["ema_flow_fhir_dev"] });
    expect(result.status).not.toBe(0);
    expect(result.out).toContain("ema_flow_fhir_dev: declared in infra/ but does not exist");
  });

  it("skips only the hand-made agent bucket when it is absent", () => {
    const result = readers(
      { GOOGLE_CLOUD_PROJECT: "p-one" },
      { missing: ["p-one-ema-flow-agent-staging"] },
    );
    expect(result.status).toBe(0);
    expect(result.out).toContain("p-one-ema-flow-agent-staging: does not exist; skipped");
  });

  it("fails when Terraform lists nothing, or buckets of another project", () => {
    const none = readers({ GOOGLE_CLOUD_PROJECT: "p-one" }, { targets: null });
    expect(none.status).not.toBe(0);
    expect(none.out).toContain("names no record_readers_targets");
    const other = readers({ GOOGLE_CLOUD_PROJECT: "p-two" }, { targets: { ...TARGETS } });
    expect(other.status).not.toBe(0);
    expect(other.out).toContain("the state and the project disagree");
    expect(other.calls).not.toContain("storage buckets");
  });

  it("never puts the access token in curl's arguments", () => {
    const result = readers({ GOOGLE_CLOUD_PROJECT: "p-one" });
    expect(result.status).toBe(0);
    expect(result.calls).toContain("curl");
    expect(result.calls).not.toContain(TOKEN);
  });
});
