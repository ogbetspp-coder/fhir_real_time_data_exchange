import { spawnSync } from "node:child_process";
import {
  chmodSync,
  existsSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

// The deploy's effective-IAM evidence (audit B08, D-3; UR-18, ADR 0004 decision 5). Until then it
// read the project and Healthcare policies of three accounts, missed every grant inherited from a
// folder or held on a bucket, a BigQuery dataset or table, a key or a service, never read the
// deployer, and asserted nothing, while UR-18 cited it as proof that an account held nothing
// else. These run the real export_effective_iam from deploy.sh, and scripts/ci/effective-iam.py,
// against stand-ins for gcloud, terraform and the BigQuery API.

const deploy = readFileSync("scripts/gcp/deploy.sh", "utf8");
const dirs: string[] = [];
afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

function extract(name: string): string {
  const body = new RegExp(`^${name}\\(\\) \\{\\n[\\s\\S]*?^\\}$`, "m").exec(deploy)?.[0];
  if (body === undefined) throw new Error(`${name} not found in deploy.sh`);
  return body;
}

const WORKER = "ema-flow-worker-dev@p-one.iam.gserviceaccount.com";
const QUERY = "ema-flow-query-dev@p-one.iam.gserviceaccount.com";
const CALLER = "ema-flow-caller-dev@p-one.iam.gserviceaccount.com";
const DEPLOYER = "ema-flow-deployer@p-one.iam.gserviceaccount.com";
const sa = (email: string) => `serviceAccount:${email}`;
const policy = (...bindings: [string, string][]) =>
  JSON.stringify({ bindings: bindings.map(([role, member]) => ({ role, members: [member] })) });

// What Terraform declares: the grants below, except the folder's roles/viewer on the worker.
const STATE = JSON.stringify({
  values: {
    root_module: {
      resources: [
        ["google_project_iam_member", "roles/logging.logWriter", WORKER],
        ["google_storage_bucket_iam_member", "roles/storage.objectCreator", WORKER],
        ["google_kms_crypto_key_iam_member", "roles/cloudkms.signerVerifier", WORKER],
        ["google_bigquery_dataset_iam_member", "roles/bigquery.dataEditor", WORKER],
        ["google_bigquery_table_iam_member", "projects/p-one/roles/ledgerAppender", WORKER],
        ["google_healthcare_fhir_store_iam_member", "projects/p-one/roles/fhirReader", QUERY],
        ["google_cloud_run_v2_service_iam_member", "roles/run.invoker", CALLER],
        ["google_project_iam_member", "roles/owner", "user:someone@company.eu"],
      ].map(([type, role, member]) => ({
        type,
        values: { role, member: member?.includes("@p-one.") ? sa(member) : member },
      })),
    },
  },
});

function stubs(dir: string, failing: boolean) {
  const answers: Record<string, string> = {
    "projects get-iam-policy": policy(
      ["roles/logging.logWriter", sa(WORKER)],
      ["roles/owner", "user:someone@company.eu"],
      ["roles/editor", sa(DEPLOYER)],
    ),
    "projects get-ancestors": "p-one\tproject\n111\tfolder\n222\torganization",
    "folders get-iam-policy": policy(["roles/viewer", sa(WORKER)]),
    "healthcare datasets get-iam-policy": "{}",
    "healthcare fhir-stores get-iam-policy": policy(["projects/p-one/roles/fhirReader", sa(QUERY)]),
    "storage buckets list": "p-one-ema-flow-dev-evidence",
    "storage buckets get-iam-policy": policy(["roles/storage.objectCreator", sa(WORKER)]),
    "kms keyrings list": "projects/p-one/locations/europe-west4/keyRings/r",
    "kms keys list": "projects/p-one/locations/europe-west4/keyRings/r/cryptoKeys/k",
    "kms keys get-iam-policy": policy(["roles/cloudkms.signerVerifier", sa(WORKER)]),
    "pubsub topics list": "",
    "artifacts repositories list": "",
    "run services list": "ema-flow-dev-query",
    "run services get-iam-policy": policy(["roles/run.invoker", sa(CALLER)]),
    "iam service-accounts list": WORKER,
    "iam service-accounts get-iam-policy": "{}",
    "auth list": DEPLOYER,
    "auth print-access-token": "ya29.token",
  };
  const cases = Object.entries(answers)
    .map(([key, value]) => `  *"${key}"*) printf '%s\\n' '${value}' ;;`)
    .join("\n");
  const scripts: Record<string, string> = {
    gcloud: `echo "gcloud $*" >>"${dir}/calls.log"
case "$*" in
  *"organizations get-iam-policy"*) exit 1 ;;
  *"storage cp"*) mkdir -p "${dir}/uploaded"; for a in "$@"; do [ -f "$a" ] && cp "$a" "${dir}/uploaded/"; done; exit 0 ;;
esac
${failing ? "exit 1" : ""}
case "$*" in
${cases}
  *) echo "unexpected gcloud $*" >&2; exit 2 ;;
esac`,
    terraform: `case "$*" in
  *"output -raw evidence_bucket") printf p-one-ema-flow-dev-evidence ;;
  *"output -raw healthcare_dataset_id") printf ds ;;
  *"output -raw source_fhir_store_id") printf source ;;
  *"output -raw target_fhir_store_id") printf target ;;
  *"show -json") printf '%s' '${STATE}' ;;
  *) exit 1 ;;
esac`,
    curl: `${failing ? "exit 22" : ""}
for a in "$@"; do url="$a"; done
case "$url" in
  *"/datasets?all=true"*) printf '%s' '{"datasets":[{"datasetReference":{"datasetId":"ledger"}}]}' ;;
  *"/datasets/ledger/tables?"*) printf '%s' '{"tables":[{"tableReference":{"tableId":"runs"}}]}' ;;
  *":getIamPolicy") printf '%s' '${policy(["projects/p-one/roles/ledgerAppender", sa(WORKER)])}' ;;
  *"/datasets/ledger") printf '%s' '{"access":[{"role":"WRITER","userByEmail":"${WORKER}"},{"role":"OWNER","specialGroup":"projectOwners"}]}' ;;
  *) exit 22 ;;
esac`,
  };
  for (const [name, body] of Object.entries(scripts)) {
    writeFileSync(path.join(dir, name), `#!/usr/bin/env bash\n${body}\n`);
    chmodSync(path.join(dir, name), 0o755);
  }
}

function exportIam({ failing = false } = {}) {
  const dir = mkdtempSync(path.join(tmpdir(), "effective-iam-"));
  dirs.push(dir);
  writeFileSync(path.join(dir, "calls.log"), "");
  stubs(dir, failing);
  const run = spawnSync(
    "bash",
    [
      "-c",
      `set -euo pipefail
source scripts/gcp/common.sh
PROJECT_ID=p-one REGION=europe-west4 ENVIRONMENT=dev SERVICE_VERSION=0123456789abcdef
${extract("iam_evidence_read")}
${extract("iam_evidence_list")}
${extract("bigquery_evidence_request")}
${extract("export_effective_iam")}
export_effective_iam
echo "returned $?"`,
    ],
    { encoding: "utf8", env: { PATH: `${dir}:${process.env.PATH ?? ""}` } },
  );
  const uploaded = path.join(dir, "uploaded");
  const reports = existsSync(uploaded)
    ? Object.fromEntries(
        readdirSync(uploaded).map((name) => [
          name,
          JSON.parse(readFileSync(path.join(uploaded, name), "utf8")) as {
            grants: { scope: string; role: string }[];
            undeclared?: string[];
            unread: string[];
          },
        ]),
      )
    : {};
  return { status: run.status, out: `${run.stdout}${run.stderr}`, reports };
}

describe("the deploy's effective-IAM evidence", () => {
  it("reads every kind of policy a grant can live in, the folders above the project included", () => {
    const { status, out, reports } = exportIam();
    expect([status, out]).toEqual([0, expect.stringContaining("returned 0")]);
    const worker = reports["effective-iam-worker.json"];
    expect(worker?.grants.map(({ scope, role }) => `${scope} ${role}`).sort()).toEqual([
      "bigquery-dataset roles/bigquery.dataEditor",
      "bigquery-table projects/p-one/roles/ledgerAppender",
      "bucket roles/storage.objectCreator",
      "folder roles/viewer",
      "kms-key roles/cloudkms.signerVerifier",
      "project roles/logging.logWriter",
    ]);
    // The stand-in answers both stores with the same policy.
    expect(reports["effective-iam-query.json"]?.grants.map(({ scope }) => scope)).toEqual([
      "fhir-store",
      "fhir-store",
    ]);
    expect(reports["effective-iam-caller.json"]?.grants.map(({ scope }) => scope)).toEqual([
      "cloud-run-service",
    ]);
  });

  it("names each role Terraform does not declare, and judges nothing else", () => {
    const { out, reports } = exportIam();
    expect(reports["effective-iam-worker.json"]?.undeclared).toEqual(["roles/viewer"]);
    expect(reports["effective-iam-query.json"]?.undeclared).toEqual([]);
    expect(reports["effective-iam-caller.json"]?.undeclared).toEqual([]);
    expect(out).toContain(
      "::warning title=Grant outside Terraform::worker holds roles/viewer (folder 111)",
    );
  });

  it("exports the deployer's grants, which are made outside Terraform, without judging them", () => {
    const { out, reports } = exportIam();
    const deployer = reports["effective-iam-deployer.json"];
    expect(deployer?.grants.map(({ role }) => role)).toEqual(["roles/editor"]);
    expect(deployer?.undeclared).toBeUndefined();
    expect(out).toContain("deployer: 1 grant(s)");
  });

  it("says which policies it could not read, rather than claiming they hold nothing", () => {
    const { out, reports } = exportIam();
    expect(reports["effective-iam-worker.json"]?.unread).toEqual(["organization 222"]);
    expect(out).toContain("Could not read the IAM policy of organization 222");
  });

  it("publishes only the per-identity reports, never another principal", () => {
    const { reports } = exportIam();
    expect(Object.keys(reports).sort()).toEqual([
      "effective-iam-caller.json",
      "effective-iam-deployer.json",
      "effective-iam-query.json",
      "effective-iam-worker.json",
    ]);
    expect(JSON.stringify(reports)).not.toContain("someone@company.eu");
  });

  it("never fails the deploy, even when nothing can be read", () => {
    const { status, out } = exportIam({ failing: true });
    expect(status).toBe(0);
    expect(out).toContain("returned 0");
    expect(out).toContain("IAM evidence incomplete");
  });

  it("runs after every apply", () => {
    expect(extract("phase_apply")).toMatch(/^ {2}export_effective_iam$/m);
  });
});
