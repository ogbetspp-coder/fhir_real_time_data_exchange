import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

// What the deploy's bootstrap imports and seeds (audit B08, D-1 and D-6). The gate job makes it
// (scripts/gcp/deploy-inputs.sh), the deploy job verifies it (scripts/fhir/deploy-inputs.mjs), and
// the standards come from fetch-standards.mjs, which now fetches only what a consumer uses, keeps
// a file that already has its pinned hash, streams to disk, and gives up after a timeout.

const dirs: string[] = [];
const servers: Server[] = [];
afterEach(async () => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
  await Promise.all(
    servers.splice(0).map((server) => new Promise((resolve) => server.close(resolve))),
  );
});

const sha256 = (data: string | Buffer) => createHash("sha256").update(data).digest("hex");
const temp = (prefix: string) => {
  const dir = mkdtempSync(path.join(tmpdir(), prefix));
  dirs.push(dir);
  return dir;
};

async function node(args: string[], env: Record<string, string> = {}) {
  const child = spawn(process.execPath, args, {
    env: { PATH: process.env.PATH ?? "", ...env },
  });
  let out = "";
  child.stdout.on("data", (chunk: Buffer) => (out += chunk.toString("utf8")));
  child.stderr.on("data", (chunk: Buffer) => (out += chunk.toString("utf8")));
  const status = await new Promise<number | null>((resolve) => child.on("close", resolve));
  return { status, out };
}

// A local server for the lock's URLs: /a and /b answer their bodies; /slow never answers.
async function serve(bodies: Record<string, string>) {
  const hits: string[] = [];
  const server = createServer((request, response) => {
    hits.push(request.url ?? "");
    const body = bodies[request.url ?? ""];
    if (request.url === "/slow") return; // never answers
    if (body === undefined) {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200).end(body);
  });
  servers.push(server);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  return { base: `http://127.0.0.1:${String(port)}`, hits };
}

function lock(dir: string, artifacts: object[]) {
  const file = path.join(dir, "lock.json");
  writeFileSync(file, JSON.stringify({ schemaVersion: "1.0.0", artifacts }));
  return file;
}

describe("fetching the pinned standards", { timeout: 30_000 }, () => {
  it("fetches only what the consumer uses, and keeps a file that already has its hash", async () => {
    const { base, hits } = await serve({ "/a.json": "alpha", "/b.json": "beta" });
    const dir = temp("fetch-standards-");
    const file = lock(dir, [
      { name: "A", url: `${base}/a.json`, sha256: sha256("alpha"), usedBy: ["bootstrap"] },
      { name: "B", url: `${base}/b.json`, sha256: sha256("beta") },
    ]);
    const dest = path.join(dir, "out");
    const args = ["scripts/fhir/fetch-standards.mjs", "--lock", file, "--dest", dest];
    const first = await node([...args, "--used-by", "bootstrap"]);
    expect(first.status).toBe(0);
    expect(readdirSync(dest)).toEqual(["A.json"]);
    expect(hits).toEqual(["/a.json"]);
    const again = await node([...args, "--used-by", "bootstrap"]);
    expect(again.status).toBe(0);
    expect(again.out).toContain("already at");
    expect(hits).toEqual(["/a.json"]);
  });

  it("leaves nothing behind when the hash does not match", async () => {
    const { base } = await serve({ "/a.json": "tampered" });
    const dir = temp("fetch-standards-");
    const file = lock(dir, [{ name: "A", url: `${base}/a.json`, sha256: sha256("alpha") }]);
    const dest = path.join(dir, "out");
    const result = await node(["scripts/fhir/fetch-standards.mjs", "--lock", file, "--dest", dest]);
    expect(result.status).not.toBe(0);
    expect(result.out).toContain("Checksum mismatch for A");
    expect(readdirSync(dest)).toEqual([]);
  });

  it("gives up on a server that never answers", async () => {
    const { base } = await serve({});
    const dir = temp("fetch-standards-");
    const file = lock(dir, [{ name: "A", url: `${base}/slow`, sha256: sha256("alpha") }]);
    const result = await node(
      ["scripts/fhir/fetch-standards.mjs", "--lock", file, "--dest", path.join(dir, "out")],
      { FETCH_TIMEOUT_SECONDS: "1" },
    );
    expect(result.status).not.toBe(0);
    expect(result.out).toContain("no complete download within 1s");
  });

  it("names each file exactly, so a reader never globs past an older pin", async () => {
    const result = await node([
      "scripts/fhir/fetch-standards.mjs",
      "--path",
      "HL7 R5 terminology dependency",
    ]);
    expect(result.out.trim()).toBe("HL7_R5_terminology_dependency-hl7.terminology.r5_7.3.0.tgz");
    const bootstrap = readFileSync("scripts/gcp/bootstrap.sh", "utf8");
    expect(bootstrap).not.toMatch(/fhir\/vendor|_(PACKAGE|EXAMPLE)\[0\]/);
  });

  it("marks exactly the five standards bootstrap uses", () => {
    const pinned = JSON.parse(readFileSync("fhir/standards.lock.json", "utf8")) as {
      artifacts: { name: string; usedBy?: string[] }[];
    };
    expect(
      pinned.artifacts.filter(({ usedBy }) => usedBy?.includes("bootstrap")).map((a) => a.name),
    ).toEqual([
      "HL7 Global ePI package",
      "HL7 Global ePI Type 2 DrugX example",
      "EMA EUePI package",
      "HL7 R5 terminology dependency",
      "HL7 R5 extensions dependency",
    ]);
    const bootstrap = readFileSync("scripts/gcp/bootstrap.sh", "utf8");
    for (const name of [
      "HL7 Global ePI package",
      "EMA EUePI package",
      "HL7 R5 terminology dependency",
      "HL7 R5 extensions dependency",
    ]) {
      expect(bootstrap).toContain(`$(standard "${name}")`);
    }
  });
});

// A directory as deploy-inputs.sh makes it, with stand-in standards whose hashes are the lock's:
// the real lock is what verify reads, so each standard is written under its real name and pinned
// content cannot be faked here; instead verify is run against a lock in a directory of its own.
function inputs() {
  const root = temp("deploy-inputs-");
  const artifacts = [
    { name: "S1", url: "https://example.invalid/s1.tgz", package: "p#1", usedBy: ["bootstrap"] },
    { name: "S2", url: "https://example.invalid/s2.json", usedBy: ["bootstrap"] },
    { name: "Other", url: "https://example.invalid/o.zip" },
  ].map((artifact) => ({ ...artifact, sha256: sha256(artifact.name) }));
  mkdirSync(path.join(root, "fhir"));
  writeFileSync(
    path.join(root, "fhir", "standards.lock.json"),
    JSON.stringify({ schemaVersion: "1.0.0", artifacts }),
  );
  const dir = path.join(root, "inputs");
  mkdirSync(path.join(dir, "standards"), { recursive: true });
  writeFileSync(path.join(dir, "standards", "S1-p_1.tgz"), "S1");
  writeFileSync(path.join(dir, "standards", "S2.json"), "S2");
  writeFileSync(path.join(dir, "synthetic-type2.json"), "{}");
  return { root, dir };
}

const deployInputs = (cwd: string, ...args: string[]) =>
  spawnSync(process.execPath, [path.resolve("scripts/fhir/deploy-inputs.mjs"), ...args], {
    cwd,
    encoding: "utf8",
  });

describe("the deploy's inputs", { timeout: 30_000 }, () => {
  it("are sealed with one hash, and verified against it and the lock", () => {
    const { root, dir } = inputs();
    const seal = deployInputs(root, "seal", dir);
    expect(seal.status).toBe(0);
    const hash = seal.stdout.trim();
    expect(hash).toBe(sha256(readFileSync(path.join(dir, "manifest.json"))));
    const verify = deployInputs(root, "verify", dir, hash);
    expect([verify.status, verify.stderr]).toEqual([0, ""]);
  });

  it.each([
    ["another hash", (dir: string) => dir, "0".repeat(64), "not the"],
    [
      "a changed file",
      (dir: string) => (writeFileSync(path.join(dir, "synthetic-type2.json"), "{ }"), dir),
      undefined,
      "synthetic-type2.json does not match the manifest",
    ],
    [
      "an added file",
      (dir: string) => (writeFileSync(path.join(dir, "extra.sh"), "x"), dir),
      undefined,
      "extra.sh is not in the manifest",
    ],
  ])("are refused with %s", (_label, change, hash, reason) => {
    const { root, dir } = inputs();
    const sealed = deployInputs(root, "seal", dir).stdout.trim();
    change(dir);
    const verify = deployInputs(root, "verify", dir, hash ?? sealed);
    expect(verify.status).toBe(1);
    expect(verify.stderr).toContain(reason);
  });

  it("are refused when a standard is not the pinned one, however consistently sealed", () => {
    const { root, dir } = inputs();
    writeFileSync(path.join(dir, "standards", "S2.json"), "not S2");
    const sealed = deployInputs(root, "seal", dir).stdout.trim();
    const verify = deployInputs(root, "verify", dir, sealed);
    expect(verify.status).toBe(1);
    expect(verify.stderr).toContain(
      "standards/S2.json is not S2 as fhir/standards.lock.json pins it",
    );
  });

  it("are made in the gate job by a script that refuses a directory with anything in it", () => {
    const script = readFileSync("scripts/gcp/deploy-inputs.sh", "utf8");
    expect(script).toContain("--used-by bootstrap");
    expect(script).toContain("node_modules/.bin/tsx scripts/fhir/export-fixture.ts");
    const dir = temp("deploy-inputs-full-");
    writeFileSync(path.join(dir, "left-over"), "x");
    const run = spawnSync("bash", ["scripts/gcp/deploy-inputs.sh", dir], { encoding: "utf8" });
    expect(run.status).toBe(1);
    expect(run.stderr).toContain("is not empty");
  });
});

describe("the resources selected for import", () => {
  it("fail on a file that does not parse, rather than dropping it with a warning", () => {
    const dir = temp("select-import-");
    const source = path.join(dir, "package");
    mkdirSync(source);
    writeFileSync(
      path.join(source, "StructureDefinition-a.json"),
      JSON.stringify({ resourceType: "StructureDefinition", id: "a" }),
    );
    writeFileSync(path.join(source, "ValueSet-b.json"), '{"resourceType": "ValueSet",');
    const run = spawnSync(
      process.execPath,
      ["scripts/fhir/select-import-resources.mjs", source, path.join(dir, "out")],
      { encoding: "utf8" },
    );
    expect(run.status).not.toBe(0);
    expect(run.stderr).toContain("ValueSet-b.json");
    expect(existsSync(path.join(dir, "out", "StructureDefinition-a.json"))).toBe(true);
  });

  it("name the types the bootstrap reconciles the store against", () => {
    const run = spawnSync(
      process.execPath,
      ["scripts/fhir/select-import-resources.mjs", "--types"],
      {
        encoding: "utf8",
      },
    );
    expect(run.stdout.trim().split(" ")).toEqual(
      expect.arrayContaining(["StructureDefinition", "ValueSet", "CodeSystem"]),
    );
  });
});
