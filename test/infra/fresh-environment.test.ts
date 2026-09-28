import { spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { readInfra, terraformBlocks } from "../support/terraform.js";

// A new environment (production) can be deployed from nothing (audit I-10), and dev's migration
// leftovers stay dev's (audit I-11). Until then a first deploy in a new project stopped three ways:
// the state bucket was created without a key, which the production folder's
// gcp.restrictNonCmekServices refuses, while the key was Terraform's, and Terraform keeps its state
// in that bucket; the BigQuery, Logging and Artifact Registry service agents, which Google creates
// only on first use, were granted their keys before anything had created them; and a custom role
// was bound in the apply that created it, before IAM knew it. These run the real functions from
// scripts/gcp/deploy.sh against stand-in gcloud, curl and terraform commands, under the bash on
// PATH and under macOS's /bin/bash 3.2, which runs the owner's `bash scripts/...`.

const deploy = readFileSync("scripts/gcp/deploy.sh", "utf8");
const blocks = terraformBlocks(readInfra());
const block = (type: string, name: string) =>
  blocks.find((candidate) => candidate.type === type && candidate.name === name)?.body ?? "";
const shells = ["bash", ...(existsSync("/bin/bash") ? ["/bin/bash"] : [])];
const dirs: string[] = [];

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

function extract(name: string): string {
  const body = new RegExp(`^${name}\\(\\) \\{\\n[\\s\\S]*?^\\}$`, "m").exec(deploy)?.[0];
  if (body === undefined) throw new Error(`${name} not found in deploy.sh`);
  return body;
}

const KEY =
  "projects/p-new/locations/europe-west4/keyRings/ema-flow-prod-record/cryptoKeys/platform-storage";

// Runs `script` after common.sh and the named deploy.sh functions, in `shell`, with stand-ins on
// PATH that log every call to calls.log; `state` is a directory the stand-ins may keep state in.
function run(
  shell: string,
  functions: string[],
  script: string,
  stubs: Record<string, string>,
  setup: (state: string) => void = () => undefined,
): { status: number | null; out: string; calls: string[] } {
  const dir = mkdtempSync(path.join(tmpdir(), "fresh-environment-"));
  dirs.push(dir);
  const state = path.join(dir, "state");
  writeFileSync(path.join(dir, "calls.log"), "");
  spawnSync("mkdir", [state]);
  setup(state);
  for (const [name, body] of Object.entries(stubs)) {
    const file = path.join(dir, name);
    writeFileSync(
      file,
      `#!/usr/bin/env bash\nSTATE="${state}"\nprintf '%s\\n' "${name} $*" >>"${dir}/calls.log"\n${body}\n`,
    );
    chmodSync(file, 0o755);
  }
  const result = spawnSync(
    shell,
    [
      "-c",
      `set -euo pipefail
source scripts/gcp/common.sh
PROJECT_ID=p-new REGION=europe-west4 ENVIRONMENT=prod REPOSITORY_ID=ema-flow-images
DEPLOY_TEMP_FILES=()
tf_common_vars=(-var=project_id=p-new -var=region=europe-west4 -var=environment=prod)
${functions.map(extract).join("\n")}
${script}`,
    ],
    { encoding: "utf8", env: { PATH: `${dir}:${process.env.PATH ?? ""}` } },
  );
  return {
    status: result.status,
    out: `${result.stdout}${result.stderr}`,
    calls: readFileSync(path.join(dir, "calls.log"), "utf8").split("\n").filter(Boolean),
  };
}

// A stand-in gcloud for the state bucket: the bucket exists when $STATE/bucket does, holding its
// described default key and public access prevention; key ring and key when their files do.
const GCLOUD = `case "$*" in
  *"storage buckets describe"*)
    if [ -f "$STATE/bucket" ]; then cat "$STATE/bucket"; exit 0; fi
    if [ -f "$STATE/denied" ]; then echo "ERROR: (gcloud.storage.buckets.describe) HTTPError 403: denied" >&2; exit 1; fi
    echo "ERROR: (gcloud.storage.buckets.describe) gs://p-new-ema-flow-tfstate not found: 404." >&2; exit 1 ;;
  *"kms keyrings describe"*) [ -f "$STATE/ring" ] ;;
  *"kms keyrings create"*) touch "$STATE/ring" ;;
  *"kms keys describe"*) [ -f "$STATE/key" ] ;;
  *"kms keys create"*) touch "$STATE/key" ;;
  *"storage buckets create"*)
    key=""; for a in "$@"; do case "$a" in --default-encryption-key=*) key="\${a#*=}" ;; esac; done
    printf '%s\\tenforced\\n' "$key" >"$STATE/bucket" ;;
  *"storage buckets update"*|*"services enable"*|*"storage service-agent"*) ;;
  *) echo "unexpected gcloud $*" >&2; exit 2 ;;
esac`;

describe("the state bucket in a new project", () => {
  it.each(shells)(
    "is created on the platform-storage key, after the key, never public, under %s",
    (shell) => {
      const result = run(shell, ["ensure_state_bucket"], "ensure_state_bucket", {
        gcloud: GCLOUD,
      });
      expect([result.status, result.out]).toEqual([0, expect.any(String)]);
      const at = (pattern: RegExp) => result.calls.findIndex((call) => pattern.test(call));
      const ring = at(/^gcloud --quiet kms keyrings create ema-flow-prod-record /);
      const key = at(/^gcloud --quiet kms keys create platform-storage /);
      const grant = at(/^gcloud --quiet storage service-agent /);
      const bucket = at(/^gcloud --quiet storage buckets create gs:\/\/p-new-ema-flow-tfstate /);
      expect([ring, key, grant, bucket].every((index) => index >= 0)).toBe(true);
      expect(ring).toBeLessThan(key);
      expect(key).toBeLessThan(grant);
      expect(grant).toBeLessThan(bucket);
      // The key as Terraform declares it: a destruction wait that differs would be a replacement
      // prevent_destroy refuses, once imported.
      const keyCall = result.calls[key] ?? "";
      expect(keyCall).toContain("--purpose=encryption");
      expect(keyCall).toContain("--rotation-period=90d");
      expect(keyCall).toContain("--destroy-scheduled-duration=120d");
      expect(keyCall).toContain(
        "--labels=application=ema-flow,environment=prod,managed_by=terraform,data_class=regulated-product-information,purpose=platform-storage",
      );
      expect(result.calls[grant]).toContain(`--authorize-cmek=${KEY}`);
      const bucketCall = result.calls[bucket] ?? "";
      expect(bucketCall).toContain(`--default-encryption-key=${KEY}`);
      expect(bucketCall).toContain("--public-access-prevention");
      expect(bucketCall).toContain("--uniform-bucket-level-access");
      expect(bucketCall).toContain("--location=europe-west4");
      expect(result.calls).toContainEqual(
        expect.stringMatching(
          /^gcloud --quiet storage buckets update gs:\/\/p-new-ema-flow-tfstate --versioning --lifecycle-file=/,
        ),
      );
      expect(result.out).toContain("on the platform-storage key");
    },
  );

  it("keeps an existing key ring and key, and creates only what is missing", () => {
    const result = run(
      shells[0] ?? "bash",
      ["ensure_state_bucket"],
      "ensure_state_bucket",
      {
        gcloud: GCLOUD,
      },
      (state) => {
        writeFileSync(path.join(state, "ring"), "");
        writeFileSync(path.join(state, "key"), "");
      },
    );
    expect(result.status).toBe(0);
    expect(result.calls.some((call) => call.includes("kms keyrings create"))).toBe(false);
    expect(result.calls.some((call) => call.includes("kms keys create"))).toBe(false);
    expect(result.calls.some((call) => call.includes("storage buckets create"))).toBe(true);
  });

  it("expires old state generations with the rules storage-keys.sh checks", () => {
    const lifecycle = spawnSync(
      "bash",
      ["-c", "source scripts/gcp/common.sh && ema_flow_state_lifecycle"],
      { encoding: "utf8" },
    );
    expect(JSON.parse(lifecycle.stdout)).toEqual({
      rule: [
        { action: { type: "Delete" }, condition: { isLive: false, numNewerVersions: 20 } },
        { action: { type: "Delete" }, condition: { isLive: false, daysSinceNoncurrentTime: 30 } },
      ],
    });
    expect(readFileSync("scripts/gcp/storage-keys.sh", "utf8")).toContain(
      'ema_flow_state_lifecycle >"$lifecycle"',
    );
  });
});

describe("an existing state bucket", () => {
  const existing = (shell: string, described: string) =>
    run(shell, ["ensure_state_bucket"], "ensure_state_bucket", { gcloud: GCLOUD }, (state) =>
      writeFileSync(path.join(state, "bucket"), described),
    );
  const writes = (calls: string[]) =>
    calls.filter((call) => / (create|update|enable|service-agent) /.test(call));

  it.each(shells)("on its key is only read, under %s", (shell) => {
    const result = existing(shell, `${KEY}\tenforced\n`);
    expect(result.status).toBe(0);
    expect(writes(result.calls)).toEqual([]);
    expect(result.out).not.toContain("::warning");
  });

  it.each(shells)(
    "that does not enforce public access prevention is reported, not refused or changed, under %s",
    (shell) => {
      // Dev's, made before the deploy set it, inherits it (2026-09-28).
      const result = existing(shell, `${KEY}\tinherited\n`);
      expect(result.status).toBe(0);
      expect(writes(result.calls)).toEqual([]);
      expect(result.out).toContain("does not enforce public access prevention");
    },
  );

  it.each(shells)("on another key, or none, is refused before Terraform, under %s", (shell) => {
    for (const described of [
      "\tenforced\n",
      `${KEY.replace("platform-storage", "other")}\tenforced\n`,
    ]) {
      const result = existing(shell, described);
      expect(result.status).not.toBe(0);
      expect(result.out).toContain("State bucket off its key");
      expect(writes(result.calls)).toEqual([]);
    }
  });

  it.each(shells)(
    "that cannot be read for any reason but its absence is refused, and nothing is created, under %s",
    (shell) => {
      const result = run(
        shell,
        ["ensure_state_bucket"],
        "ensure_state_bucket",
        { gcloud: GCLOUD },
        (state) => writeFileSync(path.join(state, "denied"), ""),
      );
      expect(result.status).not.toBe(0);
      expect(result.out).toContain("Could not read gs://p-new-ema-flow-tfstate");
      expect(writes(result.calls)).toEqual([]);
    },
  );

  it("is checked by init, before terraform init", () => {
    const init = extract("phase_init");
    expect(init.indexOf("ensure_state_bucket")).toBeGreaterThan(-1);
    expect(init.indexOf("ensure_state_bucket")).toBeLessThan(
      init.indexOf("terraform -chdir=infra init"),
    );
  });
});

describe("resources made before Terraform manages them", () => {
  // `terraform state show` succeeds for an address in $STATE/managed; `import` is recorded.
  const TERRAFORM = `case "$*" in
  *"state show "*) grep -qxF "$4" "$STATE/managed" 2>/dev/null ;;
  *" import "*) ;;
  *) exit 2 ;;
esac`;
  const importCalls = (calls: string[]) => calls.filter((call) => call.includes(" import "));

  it.each(shells)("are imported when they exist and the state lacks them, under %s", (shell) => {
    const result = run(
      shell,
      ["import_unmanaged"],
      `import_unmanaged 'google_kms_crypto_key.record["platform-storage"]' "${KEY}" gcloud kms keys describe platform-storage`,
      { terraform: TERRAFORM, gcloud: "exit 0" },
    );
    expect(result.status).toBe(0);
    expect(importCalls(result.calls)).toEqual([
      `terraform -chdir=infra import -var=project_id=p-new -var=region=europe-west4 -var=environment=prod -var=worker_image=us-docker.pkg.dev/cloudrun/container/hello -var=validator_image=us-docker.pkg.dev/cloudrun/container/hello -var=query_image=us-docker.pkg.dev/cloudrun/container/hello google_kms_crypto_key.record["platform-storage"] ${KEY}`,
    ]);
  });

  it("are left alone when already in the state, or when they do not exist", () => {
    const managed = run(
      "bash",
      ["import_unmanaged"],
      "import_unmanaged google_kms_key_ring.record id gcloud kms keyrings describe r",
      { terraform: TERRAFORM, gcloud: "exit 0" },
      (state) => writeFileSync(path.join(state, "managed"), "google_kms_key_ring.record\n"),
    );
    expect([managed.status, importCalls(managed.calls)]).toEqual([0, []]);
    expect(managed.calls.some((call) => call.startsWith("gcloud"))).toBe(false);
    const absent = run(
      "bash",
      ["import_unmanaged"],
      "import_unmanaged google_kms_key_ring.record id gcloud kms keyrings describe r",
      { terraform: TERRAFORM, gcloud: "exit 1" },
    );
    expect([absent.status, importCalls(absent.calls)]).toEqual([0, []]);
  });

  it("include the state bucket's key ring and key, imported before the first targeted apply", () => {
    const apis = extract("phase_apis");
    const plan = apis.indexOf("plan_reviewed apis");
    for (const address of [
      "import_unmanaged google_kms_key_ring.record",
      "import_unmanaged 'google_kms_crypto_key.record[\"platform-storage\"]'",
      "import_unmanaged google_artifact_registry_repository.images_cmek",
    ]) {
      const at = apis.indexOf(address);
      expect([address, at]).toEqual([address, expect.any(Number)]);
      expect([address, at >= 0 && at < plan]).toEqual([address, true]);
    }
  });
});

describe("the service agents the record keys are granted to", () => {
  const map = /record_keys = \{([\s\S]*?)\n {2}\}\n\}/.exec(readFileSync("infra/keys.tf", "utf8"));
  const keys = readFileSync("infra/keys.tf", "utf8");

  it("take Logging's from the project's Logging settings, which creates it", () => {
    expect(map?.[1]).toContain("agent   = local.logging_agent");
    expect(keys).toMatch(
      /logging_agent\s*=\s*data\.google_logging_project_settings\.current\.kms_service_account_id/,
    );
    expect(keys).not.toMatch(/^\s*logging_agent\s*=\s*"/m);
    // The read-only planner reads those settings on every plan.
    expect(readFileSync("scripts/gcp/plan-identity.sh", "utf8")).toMatch(
      /^\s+logging\.settings\.get$/m,
    );
  });

  it("create Artifact Registry's and Healthcare's before any key is granted", () => {
    for (const [name, service] of [
      ["artifact_registry", "artifactregistry.googleapis.com"],
      ["healthcare", "healthcare.googleapis.com"],
    ]) {
      expect(block("google_project_service_identity", name ?? "")).toContain(
        `service  = "${service}"`,
      );
      expect(block("google_kms_crypto_key_iam_member", "record_agent")).toContain(
        `google_project_service_identity.${name}`,
      );
    }
  });

  const agentStubs = (email: string, curlStatus = 0) => ({
    gcloud: `case "$*" in
  *"projects describe"*) echo 123456789 ;;
  *"print-access-token"*) printf ya29.never-in-argv ;;
  *) exit 2 ;;
esac`,
    curl: `for a in "$@"; do case "$a" in @*) cat "\${a#@}" >>"$STATE/headers" ;; esac; done
[ ${curlStatus} = 0 ] || exit ${curlStatus}
printf '{"kind":"bigquery#getServiceAccountResponse","email":"%s"}' "${email}"`,
  });

  it.each(shells)(
    "ask BigQuery for its encryption account, which creates it, and accept only the one infra/ grants, under %s",
    (shell) => {
      const good = run(
        shell,
        ["ensure_bigquery_agent"],
        "ensure_bigquery_agent",
        agentStubs("bq-123456789@bigquery-encryption.iam.gserviceaccount.com"),
      );
      expect([good.status, good.out]).toEqual([0, expect.stringContaining("present")]);
      expect(good.calls).toContainEqual(
        expect.stringContaining(
          "https://bigquery.googleapis.com/bigquery/v2/projects/p-new/serviceAccount",
        ),
      );
      expect(good.calls.join("\n")).not.toContain("ya29.never-in-argv");
      const other = run(
        shell,
        ["ensure_bigquery_agent"],
        "ensure_bigquery_agent",
        agentStubs("bq-999@bigquery-encryption.iam.gserviceaccount.com"),
      );
      expect(other.status).not.toBe(0);
      expect(other.out).toContain("not bq-123456789@bigquery-encryption.iam.gserviceaccount.com");
      const failed = run(
        shell,
        ["ensure_bigquery_agent"],
        "ensure_bigquery_agent",
        agentStubs("", 22),
      );
      expect(failed.status).not.toBe(0);
      expect(failed.out).toContain("Could not ask BigQuery");
    },
  );

  it("spell BigQuery's account the way the deploy checks it, and ask before the first apply", () => {
    expect(keys).toContain(
      'bigquery_encryption_agent = "bq-${data.google_project.current.number}@bigquery-encryption.iam.gserviceaccount.com"',
    );
    expect(deploy).toContain('expected="bq-${number}@bigquery-encryption.iam.gserviceaccount.com"');
    const apis = extract("phase_apis");
    expect(apis.indexOf("ensure_bigquery_agent")).toBeGreaterThan(-1);
    expect(apis.indexOf("ensure_bigquery_agent")).toBeLessThan(apis.indexOf("plan_reviewed apis"));
  });
});

describe("custom roles", () => {
  it("are created by the targeted apply before the images, never in the apply that binds them", () => {
    const roles = blocks
      .filter(({ type }) => type === "google_project_iam_custom_role")
      .map(({ name }) => `google_project_iam_custom_role.${name}`);
    expect(roles.length).toBeGreaterThan(0);
    const listed = spawnSync(
      "bash",
      ["-c", `${extract("custom_role_targets")}\ncustom_role_targets`],
      {
        encoding: "utf8",
      },
    );
    expect(listed.stdout.trim().split("\n")).toEqual(roles);
    const apis = extract("phase_apis");
    expect(apis).toMatch(
      /plan_reviewed apis \\\n(?:\s+-target=\S+ \\\n)+\s+"\$\{role_targets\[@\]\}" \\\n/,
    );
    expect(apis).toContain("done < <(custom_role_targets)");
  });
});

describe("dev's migration leftovers", () => {
  const infra = readInfra();

  it.each([
    ["google_logging_project_bucket_config", "regulated_audit"],
    ["google_kms_crypto_key", "manifest_signing"],
  ])("%s.%s exists in dev alone, moved in place rather than destroyed", (type, name) => {
    expect(block(type, name)).toMatch(/^\s+count\s+= var\.environment == "dev" \? 1 : 0$/m);
    expect(block(type, name)).toMatch(/prevent_destroy\s*=\s*true/);
    expect(infra).toContain(`moved {\n  from = ${type}.${name}\n  to   = ${type}.${name}[0]\n}`);
  });

  it("include no removed block, and nothing the deploy targets by its old address", () => {
    expect(infra).not.toContain("from = google_healthcare_dataset.epi");
    expect(deploy).not.toContain("-target=google_logging_project_bucket_config.regulated_audit ");
    expect(deploy).not.toMatch(/-target=google_logging_project_bucket_config\.regulated_audit \\/);
  });

  it("no longer include the migration's overrides and scripts", () => {
    expect(existsSync("scripts/gcp/bq-cmek-convert.sh")).toBe(false);
    for (const file of ["scripts/gcp/reconcile-fhir-stores.sh", "scripts/gcp/bootstrap.sh"]) {
      expect(readFileSync(file, "utf8")).not.toMatch(/\$\{HEALTHCARE_DATASET_OVERRIDE/);
    }
  });
});

describe("the audit sink", () => {
  it("is granted nothing by the deploy: a sink to a log bucket in its own project has no writer identity", () => {
    expect(deploy).not.toContain("writerIdentity");
    expect(deploy).not.toContain('roles/logging.bucketWriter"');
    expect(deploy).not.toContain("grant roles/logging.bucketWriter to it manually");
    expect(block("google_logging_project_sink", "regulated_audit")).toContain(
      "google_logging_project_bucket_config.regulated_audit_cmek.id",
    );
  });
});
