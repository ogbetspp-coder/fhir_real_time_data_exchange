import { spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { readInfra, terraformBlocks } from "../support/terraform.js";

// A new environment deploys from nothing (audit I-10), and dev's migration leftovers stay dev's
// (audit I-11). The deploy.sh functions run against a stand-in gcloud and terraform, under the
// bash on PATH and under macOS's /bin/bash 3.2.

const deploy = readFileSync("scripts/gcp/deploy.sh", "utf8");
const blocks = terraformBlocks(readInfra());
const block = (type: string, name: string) =>
  blocks.find((candidate) => candidate.type === type && candidate.name === name)?.body ?? "";
const shells = ["bash", ...(existsSync("/bin/bash") ? ["/bin/bash"] : [])];
const KEY =
  "projects/p-new/locations/europe-west4/keyRings/ema-flow-prod-record/cryptoKeys/platform-storage";
const dirs: string[] = [];

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

function extract(name: string): string {
  const body = new RegExp(`^${name}\\(\\) \\{\\n[\\s\\S]*?^\\}$`, "m").exec(deploy)?.[0];
  if (body === undefined) throw new Error(`${name} not found in deploy.sh`);
  return body;
}

// Runs one deploy.sh function with stand-ins that log each call; `bucket`, when given, is the
// existing state bucket's default key. The stand-in terraform knows `managed` addresses.
function run(shell: string, script: string, options: { bucket?: string; managed?: string } = {}) {
  const dir = mkdtempSync(path.join(tmpdir(), "fresh-environment-"));
  dirs.push(dir);
  if (options.bucket !== undefined) writeFileSync(path.join(dir, "bucket"), options.bucket);
  writeFileSync(path.join(dir, "managed"), options.managed ?? "");
  const stubs = {
    gcloud: `case "$*" in
  *"storage buckets describe"*) [ -f "${dir}/bucket" ] && cat "${dir}/bucket" ;;
  *"storage buckets create"*) printf '%s' "${KEY}" >"${dir}/bucket" ;;
  *"describe"*) exit 1 ;;
esac`,
    terraform: `case "$*" in *"state show "*) grep -qxF "$4" "${dir}/managed" ;; esac`,
  };
  for (const [name, body] of Object.entries(stubs)) {
    const file = path.join(dir, name);
    writeFileSync(file, `#!/usr/bin/env bash\necho "${name} $*" >>"${dir}/calls"\n${body}\n`);
    chmodSync(file, 0o755);
  }
  const result = spawnSync(
    shell,
    [
      "-c",
      `set -euo pipefail
source scripts/gcp/common.sh
PROJECT_ID=p-new REGION=europe-west4 ENVIRONMENT=prod DEPLOY_TEMP_FILES=()
tf_common_vars=(-var=project_id=p-new)
${extract("ensure_state_bucket")}
${extract("import_unmanaged")}
${script}`,
    ],
    { encoding: "utf8", env: { PATH: `${dir}:${process.env.PATH ?? ""}` } },
  );
  const calls = existsSync(path.join(dir, "calls"))
    ? readFileSync(path.join(dir, "calls"), "utf8").split("\n").filter(Boolean)
    : [];
  return { status: result.status, out: `${result.stdout}${result.stderr}`, calls };
}

const at = (calls: string[], pattern: RegExp) => calls.findIndex((call) => pattern.test(call));

describe("the state bucket", { timeout: 30_000 }, () => {
  it.each(shells)("in a new project is created on its key, made first, under %s", (shell) => {
    const { status, calls } = run(shell, "ensure_state_bucket");
    expect(status).toBe(0);
    const key = at(calls, /kms keys create platform-storage /);
    const grant = at(calls, /storage service-agent .*--authorize-cmek=/);
    const bucket = at(calls, /storage buckets create gs:\/\/p-new-ema-flow-tfstate /);
    expect(at(calls, /kms keyrings create ema-flow-prod-record /)).toBeLessThan(key);
    expect(key).toBeLessThan(grant);
    expect(grant).toBeLessThan(bucket);
    // As infra/keys.tf declares it: a destruction wait that differs would be a replacement.
    expect(calls[key]).toContain("--rotation-period=90d --destroy-scheduled-duration=120d");
    expect(calls[bucket]).toContain(`--public-access-prevention --default-encryption-key=${KEY}`);
    expect(calls).toContainEqual(
      expect.stringMatching(/buckets update .* --versioning --lifecycle-file=/),
    );
  });

  it.each(shells)("that exists on its key is only read, under %s", (shell) => {
    const { status, calls } = run(shell, "ensure_state_bucket", { bucket: KEY });
    expect(status).toBe(0);
    expect(calls.filter((call) => / (create|update|enable|service-agent) /.test(call))).toEqual([]);
  });

  it.each(shells)("on another key, or none, is refused, under %s", (shell) => {
    for (const bucket of ["", KEY.replace("platform-storage", "other")]) {
      const { status, out } = run(shell, "ensure_state_bucket", { bucket });
      expect(status).not.toBe(0);
      expect(out).toContain("State bucket off its key");
    }
  });

  it("is checked by init before terraform init, and its ring and key imported before the first apply", () => {
    const init = extract("phase_init");
    expect(init.indexOf("ensure_state_bucket")).toBeLessThan(
      init.indexOf("terraform -chdir=infra init"),
    );
    const apis = extract("phase_apis");
    const plan = apis.indexOf("plan_reviewed apis");
    for (const address of [
      "import_unmanaged google_kms_key_ring.record",
      `import_unmanaged 'google_kms_crypto_key.record["platform-storage"]'`,
    ]) {
      expect(apis.indexOf(address)).toBeGreaterThan(-1);
      expect(apis.indexOf(address)).toBeLessThan(plan);
    }
  });
});

describe("import_unmanaged", { timeout: 30_000 }, () => {
  const imports = (calls: string[]) => calls.filter((call) => call.includes(" import "));

  it("imports what exists but the state lacks, and nothing else", () => {
    const script =
      "import_unmanaged google_kms_key_ring.record the-id gcloud kms keyrings describe r";
    const missing = run("bash", script.replace("describe r", "list"));
    expect(imports(missing.calls)).toEqual([
      "terraform -chdir=infra import -var=project_id=p-new -var=worker_image=us-docker.pkg.dev/cloudrun/container/hello -var=validator_image=us-docker.pkg.dev/cloudrun/container/hello -var=query_image=us-docker.pkg.dev/cloudrun/container/hello google_kms_key_ring.record the-id",
    ]);
    expect(imports(run("bash", script).calls)).toEqual([]);
    const managed = run("bash", script.replace("describe r", "list"), {
      managed: "google_kms_key_ring.record\n",
    });
    expect(imports(managed.calls)).toEqual([]);
  });
});

describe("the service agents and roles a new project's first apply needs", () => {
  it("asks BigQuery for its encryption account, and creates the custom role, before the first apply", () => {
    const apis = extract("phase_apis");
    const plan = apis.indexOf("plan_reviewed apis");
    expect(apis.indexOf("/serviceAccount")).toBeGreaterThan(-1);
    expect(apis.indexOf("/serviceAccount")).toBeLessThan(plan);
    expect(apis).toContain("-target=google_project_iam_custom_role.ledger_appender \\");
  });

  it("creates Artifact Registry's and reads Logging's before the keys are granted", () => {
    expect(block("google_project_service_identity", "artifact_registry")).toContain(
      'service  = "artifactregistry.googleapis.com"',
    );
    expect(block("google_kms_crypto_key_iam_member", "record_agent")).toContain(
      "google_project_service_identity.artifact_registry",
    );
    expect(readFileSync("infra/keys.tf", "utf8")).toMatch(
      /logging_agent\s*=\s*data\.google_logging_project_settings\.current\.kms_service_account_id/,
    );
  });
});

describe("dev's migration leftovers", () => {
  const infra = readInfra();

  it.each([
    ["google_logging_project_bucket_config", "regulated_audit"],
    ["google_kms_crypto_key", "manifest_signing"],
  ])("%s.%s exists in dev alone, moved in place, never destroyed", (type, name) => {
    expect(block(type, name)).toMatch(/^\s+count\s+= var\.environment == "dev" \? 1 : 0$/m);
    expect(block(type, name)).toMatch(/prevent_destroy\s*=\s*true/);
    expect(infra).toContain(`moved {\n  from = ${type}.${name}\n  to   = ${type}.${name}[0]\n}`);
  });

  it("no longer include the epi removed block, the dataset override or the conversion script", () => {
    expect(infra).not.toContain("from = google_healthcare_dataset.epi");
    expect(existsSync("scripts/gcp/bq-cmek-convert.sh")).toBe(false);
    for (const file of ["scripts/gcp/reconcile-fhir-stores.sh", "scripts/gcp/bootstrap.sh"]) {
      expect(readFileSync(file, "utf8")).not.toContain("${HEALTHCARE_DATASET_OVERRIDE");
    }
  });

  it("grant nothing to the audit sink, which writes to a log bucket in its own project", () => {
    expect(deploy).not.toContain("writerIdentity");
    expect(deploy).not.toContain("roles/logging.bucketWriter");
  });
});
