import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmodSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterAll, describe, expect, it } from "vitest";

// The deploy's FHIR bootstrap (scripts/gcp/bootstrap.sh), run for real in a copy of the scripts
// with a lock of its own, against stand-ins for terraform, gcloud and the FHIR store (audit B08).
// It must refuse inputs the gate job did not make (D-1); keep the target store holding exactly
// the pinned set, which until now it checked by a StructureDefinition count alone (D-7); seed the
// synthetic source only where synthetic content is accepted (D-7); and never pass the token to
// curl as an argument (D-7).

const dirs: string[] = [];
afterAll(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

const TOKEN = "ya29.bootstrap-token-never-in-argv";
const TARGET =
  "https://healthcare.googleapis.com/v1/projects/p-one/locations/europe-west4/datasets/ds/fhirStores/target/fhir";
const EXPECTED = [
  "CodeSystem/t1",
  "StructureDefinition/e1",
  "StructureDefinition/g1",
  "StructureDefinition/x1",
  "ValueSet/e2",
];

const sha256 = (data: string | Buffer) => createHash("sha256").update(data).digest("hex");
// The acknowledgement of a prune over its bound (common.sh, ema_flow_acknowledged): the commit
// being deployed and the first 16 hex of SHA-256 over the sorted candidates.
const ack = (commit: string, ...candidates: string[]) =>
  `${commit}:${sha256(
    [...candidates]
      .sort()
      .map((c) => `${c}\n`)
      .join(""),
  ).slice(0, 16)}`;

// The four packages bootstrap imports, each a package/ directory of resources, as tarballs.
const PACKAGES: [name: string, pkg: string, resources: [string, string][]][] = [
  ["HL7 Global ePI package", "g#1", [["StructureDefinition", "g1"]]],
  [
    "EMA EUePI package",
    "e#1",
    [
      ["StructureDefinition", "e1"],
      ["ValueSet", "e2"],
    ],
  ],
  ["HL7 R5 terminology dependency", "t#1", [["CodeSystem", "t1"]]],
  [
    "HL7 R5 extensions dependency",
    "x#1",
    [
      ["StructureDefinition", "x1"],
      ["Bundle", "not-imported"],
    ],
  ],
];

const CURL = `#!/usr/bin/env python3
import json, os, sys
state = os.environ["STUB_DIR"]
args = sys.argv[1:]
logged, out, method, url, header = [], None, "GET", "", ""
i = 0
while i < len(args):
    a = args[i]
    if a in ("--output", "--write-out", "--request", "--header", "--data-binary") and i + 1 < len(args):
        v = args[i + 1]
        if a == "--output": out = v
        if a == "--request": method = v
        if a == "--header" and v.startswith("@"): header = open(v[1:]).read().strip()
        logged += [a, v]; i += 2; continue
    if not a.startswith("--"): url = a
    logged.append(a); i += 1
with open(os.path.join(state, "calls.log"), "a") as log:
    log.write("curl " + " ".join(logged) + "\\n")
if header != "Authorization: Bearer ${TOKEN}":
    sys.exit("stub curl: no token in a header file")
store = os.path.join(state, "store.txt")
lines = [l for l in open(store).read().splitlines() if l.strip()] if os.path.exists(store) else []
def answer(code, body):
    text = json.dumps(body)
    if out: open(out, "w").write(text)
    else: sys.stdout.write(text)
    if "--write-out" in args: sys.stdout.write(str(code))
base = "${TARGET}/"
if method == "DELETE" and os.environ.get("STUB_REFUSE_DELETE"):
    answer(409, {"resourceType": "OperationOutcome", "issue": [{"code": "conflict", "severity": "error"}]}); sys.exit(0)
if method == "DELETE":
    ref = url[len(base):]
    open(store, "w").write("".join(l + "\\n" for l in lines if l.split(" ")[0] != ref))
    answer(200, {}); sys.exit(0)
if method == "PUT":
    open(os.path.join(state, "seeded"), "w").write(url)
    answer(200, {"resourceType": "Bundle"}); sys.exit(0)
# A search, one resource a page, each page linking to the next.
path, _, query = url[len(base):].partition("?")
kind = path.split("/")[0]
page = int(query.split("_page_token=")[1]) if "_page_token=" in query else 0
of_kind = [l for l in lines if l.startswith(kind + "/")]
def resource(line):
    ref, version = line.split(" ")
    return {"resource": {"resourceType": kind, "id": ref.split("/")[1], "meta": {"versionId": version}}}
entry = [resource(of_kind[page])] if page < len(of_kind) else []
# STUB_REPEAT: page 0 also answers page 1's entry, as a search that is not a snapshot can.
if os.environ.get("STUB_REPEAT") and page == 0 and len(of_kind) > 1:
    entry.append(resource(of_kind[1]))
# STUB_TOTAL_SKEW: the total grows by one on every page after the first.
total = len(of_kind) + (page if os.environ.get("STUB_TOTAL_SKEW") else 0)
link = []
if page + 1 < len(of_kind):
    following = os.environ.get("STUB_NEXT_BASE", base) + kind + "?_page_token=" + str(page + 1)
    link = [{"relation": "next", "url": following}]
answer(200, {"resourceType": "Bundle", "total": total, "entry": entry, "link": link})
`;

const GCLOUD = `#!/usr/bin/env bash
echo "gcloud $*" >>"$STUB_DIR/calls.log"
case "$*" in
  *"print-access-token"*) printf '%s' '${TOKEN}' ;;
  *"storage cat"*) cat "$STUB_DIR/marker" 2>/dev/null || exit 1 ;;
  *"storage cp - "*) cat >"$STUB_DIR/marker" ;;
  *"storage rsync"*) ;;
  *"fhir-stores import gcs"*)
    # An import adds and overwrites, each resource at a new version; it never removes.
    python3 - "$STUB_DIR/store.txt" "$STUB_DIR/imported.txt" <<'PY'
import os, sys
store, imported = sys.argv[1], sys.argv[2]
current = dict(l.split(" ") for l in open(store).read().splitlines() if l.strip()) if os.path.exists(store) else {}
for ref in open(imported).read().split():
    current[ref] = str(int(current.get(ref, "0")) + 1)
open(store, "w").write("".join(f"{r} {v}\\n" for r, v in sorted(current.items())))
PY
    ;;
  *) echo "unexpected gcloud $*" >&2; exit 2 ;;
esac
`;

const TERRAFORM = `#!/usr/bin/env bash
case "$*" in
  *"output -raw region") printf europe-west4 ;;
  *"output -raw healthcare_dataset_id") printf ds ;;
  *"output -raw source_fhir_store_id") printf source ;;
  *"output -raw target_fhir_store_id") printf target ;;
  *"output -raw profile_staging_bucket") printf p-one-profiles ;;
  *"output -raw workflow_name") printf wf ;;
  *) echo "unexpected terraform $*" >&2; exit 2 ;;
esac
`;

// A copy of the scripts bootstrap runs, a lock naming the four packages, and a sealed inputs
// directory as the gate job would hand over. Returns the root and the manifest's hash.
async function setUp() {
  const root = mkdtempSync(path.join(tmpdir(), "bootstrap-"));
  dirs.push(root);
  for (const file of [
    "scripts/gcp/bootstrap.sh",
    "scripts/gcp/common.sh",
    "scripts/fhir/deploy-inputs.mjs",
    "scripts/fhir/fetch-standards.mjs",
    "scripts/fhir/standards.mjs",
    "scripts/fhir/select-import-resources.mjs",
  ]) {
    mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
    copyFileSync(file, path.join(root, file));
  }
  const inputs = path.join(root, "inputs");
  mkdirSync(path.join(inputs, "standards"), { recursive: true });
  const artifacts = [];
  for (const [name, pkg, resources] of PACKAGES) {
    const work = path.join(root, "work", pkg.replace("#", "_"), "package");
    mkdirSync(work, { recursive: true });
    for (const [kind, id] of resources) {
      writeFileSync(
        path.join(work, `${kind}-${id}.json`),
        JSON.stringify({ resourceType: kind, id }),
      );
    }
    writeFileSync(path.join(work, "package.json"), "{}");
    const file = `${name.replaceAll(/[^a-zA-Z0-9.-]/g, "_")}-${pkg.replace("#", "_")}.tgz`;
    await run("tar", [
      "-czf",
      path.join(inputs, "standards", file),
      "-C",
      path.dirname(work),
      "package",
    ]);
    artifacts.push({
      name,
      package: pkg,
      url: `https://example.invalid/${pkg}.tgz`,
      sha256: sha256(readFileSync(path.join(inputs, "standards", file))),
      usedBy: ["bootstrap"],
    });
  }
  mkdirSync(path.join(root, "fhir"));
  writeFileSync(
    path.join(root, "fhir", "standards.lock.json"),
    JSON.stringify({ schemaVersion: "1.0.0", artifacts }),
  );
  writeFileSync(path.join(inputs, "synthetic-type2.json"), '{"resourceType":"Bundle"}');
  writeFileSync(
    path.join(root, "fhir", "deploy-inputs.lock.json"),
    JSON.stringify({ files: { "synthetic-type2.json": sha256('{"resourceType":"Bundle"}') } }),
  );
  const sealed = await run(process.execPath, ["scripts/fhir/deploy-inputs.mjs", "seal", inputs], {
    cwd: root,
  });
  const stubs = path.join(root, "stubs");
  mkdirSync(stubs);
  for (const [name, text] of [
    ["curl", CURL],
    ["gcloud", GCLOUD],
    ["terraform", TERRAFORM],
  ] as const) {
    writeFileSync(path.join(stubs, name), text);
    chmodSync(path.join(stubs, name), 0o755);
  }
  writeFileSync(path.join(stubs, "imported.txt"), EXPECTED.join("\n"));
  writeFileSync(path.join(stubs, "calls.log"), "");
  return { root, inputs, stubs, hash: sealed.out.trim() };
}

async function run(command: string, args: string[], options: { cwd?: string; env?: object } = {}) {
  const child = spawn(command, args, {
    cwd: options.cwd,
    env: { ...process.env, ...options.env },
  });
  let out = "";
  child.stdout.on("data", (chunk: Buffer) => (out += chunk.toString("utf8")));
  child.stderr.on("data", (chunk: Buffer) => (out += chunk.toString("utf8")));
  const status = await new Promise<number | null>((resolve) => child.on("close", resolve));
  return { status, out };
}

async function bootstrap(
  setup: Awaited<ReturnType<typeof setUp>>,
  env: Record<string, string> = {},
) {
  const result = await run("bash", [path.join(setup.root, "scripts/gcp/bootstrap.sh")], {
    env: {
      PATH: `${setup.stubs}:${process.env.PATH ?? ""}`,
      GOOGLE_CLOUD_PROJECT: "p-one",
      STUB_DIR: setup.stubs,
      DEPLOY_INPUTS_DIR: setup.inputs,
      DEPLOY_INPUTS_SHA256: setup.hash,
      ...env,
    },
  });
  const file = (name: string) =>
    existsSync(path.join(setup.stubs, name))
      ? readFileSync(path.join(setup.stubs, name), "utf8")
      : "";
  return {
    ...result,
    calls: file("calls.log"),
    store: file("store.txt").split("\n").filter(Boolean),
    marker: file("marker"),
    seeded: file("seeded"),
  };
}

const imports = (calls: string) =>
  calls.split("\n").filter((l) => l.includes("fhir-stores import")).length;

describe.concurrent("the deploy's FHIR bootstrap", { timeout: 60_000 }, () => {
  it("imports into an empty store, records both fingerprints, and seeds only if told to", async () => {
    const setup = await setUp();
    const first = await bootstrap(setup);
    expect(first.status).toBe(0);
    expect(imports(first.calls)).toBe(4);
    expect(first.store.map((line) => line.split(" ")[0])).toEqual(EXPECTED);
    expect(first.marker.split("\n").filter(Boolean)).toHaveLength(2);
    expect(first.seeded).toBe("");
    expect(first.out).toContain("No synthetic source seeded");

    const seeding = await bootstrap(setup, { ALLOW_SYNTHETIC_SOURCES: "true" });
    expect(seeding.status).toBe(0);
    expect(seeding.seeded).toContain("/fhirStores/source/fhir/Bundle/synthetic-type2-smpc");
  });

  it("skips the import while the set and the store are as recorded", async () => {
    const setup = await setUp();
    await bootstrap(setup);
    const second = await bootstrap(setup);
    expect(second.status).toBe(0);
    expect(second.out).toContain("sync and import skipped");
    expect(imports(second.calls)).toBe(4);
  });

  it("imports again, and deletes what the set does not have, when the store was changed", async () => {
    const setup = await setUp();
    await bootstrap(setup);
    // A profile added by hand, and a pinned one edited in the store (a new version).
    const store = path.join(setup.stubs, "store.txt");
    writeFileSync(
      store,
      readFileSync(store, "utf8").replace("ValueSet/e2 1", "ValueSet/e2 7") +
        "StructureDefinition/foreign 1\n",
    );
    // One of a set of five is over the prune's 5% bound, so this commit acknowledges it.
    const again = await bootstrap(setup, {
      ALLOW_REPLACE_ACK: ack("c1", "StructureDefinition/foreign"),
      DEPLOY_COMMIT: "c1",
    });
    expect(again.status).toBe(0);
    expect(imports(again.calls)).toBe(8);
    expect(again.calls).toMatch(/--request DELETE .*\/fhir\/StructureDefinition\/foreign/);
    expect(again.store.map((line) => line.split(" ")[0])).toEqual(EXPECTED);
    expect(again.out).toContain("1 resource(s) the pinned set does not have deleted");
  });

  it("imports again when a pinned resource was deleted from the store", async () => {
    const setup = await setUp();
    await bootstrap(setup);
    const store = path.join(setup.stubs, "store.txt");
    writeFileSync(store, readFileSync(store, "utf8").replace(/^CodeSystem\/t1 .*\n/m, ""));
    const again = await bootstrap(setup);
    expect(again.status).toBe(0);
    expect(again.out).toContain("1 not in target");
    expect(imports(again.calls)).toBe(8);
  });

  it("refuses a prune over its bound before importing, unless this commit acknowledges it", async () => {
    const setup = await setUp();
    writeFileSync(path.join(setup.stubs, "store.txt"), "StructureDefinition/foreign 1\n");
    const refused = await bootstrap(setup, {
      ALLOW_REPLACE_ACK: ack("old", "StructureDefinition/foreign"),
      DEPLOY_COMMIT: "c1",
    });
    expect(refused.status).not.toBe(0);
    expect(refused.out).toContain("Profile prune refused");
    expect(refused.out).toContain(`ALLOW_REPLACE_ACK=${ack("c1", "StructureDefinition/foreign")}`);
    expect(imports(refused.calls)).toBe(0);
    expect(refused.calls).not.toContain("DELETE");
  });

  it("never deletes a pinned resource, even when a search page repeats one", async () => {
    // The review's reproduction: page 0 also answers page 1's entry. Until the listing was made
    // unique, the repeat was a prune candidate, and a pinned StructureDefinition was deleted.
    const setup = await setUp();
    await bootstrap(setup);
    const again = await bootstrap(setup, { STUB_REPEAT: "1", FORCE_PROFILE_IMPORT: "true" });
    expect(again.status).toBe(0);
    expect(again.calls).not.toContain("DELETE");
    expect(again.store.map((line) => line.split(" ")[0])).toEqual(EXPECTED);
  });

  it("skips the prune, and records nothing, on a listing that moved while it was read", async () => {
    const setup = await setUp();
    await bootstrap(setup);
    const store = path.join(setup.stubs, "store.txt");
    writeFileSync(store, `${readFileSync(store, "utf8")}StructureDefinition/foreign 1\n`);
    const marker = readFileSync(path.join(setup.stubs, "marker"), "utf8");
    const result = await bootstrap(setup, {
      STUB_TOTAL_SKEW: "1",
      ALLOW_REPLACE_ACK: ack("c1", "StructureDefinition/foreign"),
      DEPLOY_COMMIT: "c1",
    });
    expect(result.status).toBe(0);
    expect(result.out).toContain("total changed between pages");
    expect(result.out).toContain("Profile set not confirmed");
    expect(result.calls).not.toContain("DELETE");
    expect(readFileSync(path.join(setup.stubs, "marker"), "utf8")).toBe(marker);
  });

  it("keeps the deploy green when the store refuses a delete, and says what to do", async () => {
    const setup = await setUp();
    await bootstrap(setup);
    const store = path.join(setup.stubs, "store.txt");
    writeFileSync(store, `${readFileSync(store, "utf8")}StructureDefinition/referenced 1\n`);
    const result = await bootstrap(setup, {
      STUB_REFUSE_DELETE: "1",
      ALLOW_REPLACE_ACK: ack("c1", "StructureDefinition/referenced"),
      DEPLOY_COMMIT: "c1",
    });
    expect(result.status).toBe(0);
    expect(result.out).toContain("Profile prune incomplete");
    expect(result.out).toContain("StructureDefinition/referenced (HTTP 409)");
    expect(result.out).toContain("remove or re-point that");
  });

  it("refuses inputs that are not what the gate job sealed, before touching the store", async () => {
    const setup = await setUp();
    writeFileSync(path.join(setup.inputs, "synthetic-type2.json"), '{"resourceType":"Other"}');
    const result = await bootstrap(setup, { ALLOW_SYNTHETIC_SOURCES: "true" });
    expect(result.status).not.toBe(0);
    expect(result.out).toContain("synthetic-type2.json does not match the manifest");
    expect(result.calls).toBe("");
  });

  it("follows a next-page link only within the store", async () => {
    const setup = await setUp();
    await bootstrap(setup);
    const result = await bootstrap(setup, { STUB_NEXT_BASE: "https://elsewhere.example/fhir/" });
    expect(result.status).not.toBe(0);
    expect(result.out).toContain("a next-page link leads outside the target store");
  });

  it("never hands curl the token as an argument", async () => {
    const setup = await setUp();
    const result = await bootstrap(setup, { ALLOW_SYNTHETIC_SOURCES: "true" });
    expect(result.status).toBe(0);
    expect(result.calls).toContain("curl");
    expect(result.calls).not.toContain(TOKEN);
  });
});
