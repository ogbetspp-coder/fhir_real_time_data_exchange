import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { readSidecarPins } from "../../scripts/ci/validator-pins.mjs";

// The official-validation gate's package cache (scripts/ci/official-validate.mjs). CI restores it
// from actions/cache, and whatever ran in the job before the cache was saved could have written
// to it, so only the tarballs, re-hashed on every run, are trusted: the unpacked tree is rebuilt
// from them each time. Until that, a marker holding the checksum let a tampered tree be reused.
// Run offline over fake artefacts whose checksums a copy of Dockerfile.validator pins, with
// --seed-only, so no validator or network is needed.

const SCRIPT = path.resolve("scripts/ci/official-validate.mjs");
const directories: string[] = [];

afterEach(() => {
  for (const directory of directories.splice(0)) {
    rmSync(directory, { recursive: true, force: true });
  }
});

const sha256 = (bytes: Buffer): string => createHash("sha256").update(bytes).digest("hex");

// A validator directory holding fake artefacts, a Dockerfile pinning their checksums and a lock of
// two small packages already downloaded into it.
function fixture(): { dir: string; dockerfile: string; lock: string; cache: string } {
  const work = mkdtempSync(path.join(tmpdir(), "official-validate-"));
  directories.push(work);
  const dir = path.join(work, "validator");
  mkdirSync(path.join(dir, "packages"), { recursive: true });

  let dockerfile = readFileSync("Dockerfile.validator", "utf8");
  for (const artefact of readSidecarPins("Dockerfile.validator").artefacts) {
    const bytes = Buffer.from(`fake ${artefact.file}\n`);
    writeFileSync(path.join(dir, artefact.file), bytes);
    dockerfile = dockerfile.replaceAll(artefact.sha256, sha256(bytes));
  }
  writeFileSync(path.join(work, "Dockerfile.validator"), dockerfile);

  const lines: string[] = [];
  for (const [id, version] of [
    ["example.fhir.core", "1.0.0"],
    ["example.fhir.terms", "2.0.0"],
  ] as const) {
    const source = path.join(work, "source", id);
    mkdirSync(path.join(source, "package"), { recursive: true });
    writeFileSync(
      path.join(source, "package", "package.json"),
      JSON.stringify({ name: id, version }),
    );
    writeFileSync(
      path.join(source, "package", "StructureDefinition-Bundle.json"),
      JSON.stringify({ resourceType: "StructureDefinition", id: "Bundle", min: 1 }),
    );
    const tarball = path.join(dir, "packages", `${id}#${version}.tgz`);
    execFileSync("tar", ["-czf", tarball, "-C", source, "package"]);
    lines.push(`${id} ${version} ${sha256(readFileSync(tarball))}`);
  }
  const lock = path.join(work, "validator-packages.lock");
  writeFileSync(lock, `# fixture\n${lines.join("\n")}\n`);

  return {
    dir,
    dockerfile: path.join(work, "Dockerfile.validator"),
    lock,
    cache: path.join(dir, "home", ".fhir", "packages"),
  };
}

function seed(f: ReturnType<typeof fixture>): { status: number; output: string } {
  const run = spawnSync(
    process.execPath,
    [
      SCRIPT,
      "--offline",
      "--seed-only",
      "--validator-dir",
      f.dir,
      "--dockerfile",
      f.dockerfile,
      "--package-lock",
      f.lock,
    ],
    { encoding: "utf8" },
  );
  return { status: run.status ?? -1, output: `${run.stdout}${run.stderr}` };
}

describe("the official-validation package cache", () => {
  it("unpacks every listed package from its verified tarball", () => {
    const f = fixture();
    const result = seed(f);
    expect(result.status).toBe(0);
    expect(result.output).toContain("Package cache rebuilt from the verified tarballs");
    expect(readdirSync(f.cache).sort()).toEqual([
      "example.fhir.core#1.0.0",
      "example.fhir.terms#2.0.0",
      "packages.ini",
    ]);
  });

  it("rebuilds a tampered cache from the tarballs instead of trusting what is there", () => {
    const f = fixture();
    expect(seed(f).status).toBe(0);
    const profile = path.join(
      f.cache,
      "example.fhir.core#1.0.0",
      "package",
      "StructureDefinition-Bundle.json",
    );
    const pinned = readFileSync(profile, "utf8");

    // A weakened profile, a package nobody listed, and a marker of the kind the cache used to
    // trust, all left behind as a restored cache would carry them.
    writeFileSync(profile, JSON.stringify({ resourceType: "StructureDefinition", min: 0 }));
    mkdirSync(path.join(f.cache, "planted.fhir#9.9.9", "package"), { recursive: true });
    writeFileSync(path.join(f.cache, "stray.json"), "{}");
    mkdirSync(path.join(f.dir, "seeded"), { recursive: true });
    const tarball = path.join(f.dir, "packages", "example.fhir.core#1.0.0.tgz");
    writeFileSync(
      path.join(f.dir, "seeded", "example.fhir.core#1.0.0"),
      sha256(readFileSync(tarball)),
    );

    expect(seed(f).status).toBe(0);
    expect(readFileSync(profile, "utf8")).toBe(pinned);
    expect(readdirSync(f.cache).sort()).toEqual([
      "example.fhir.core#1.0.0",
      "example.fhir.terms#2.0.0",
      "packages.ini",
    ]);
    expect(existsSync(path.join(f.dir, "seeded"))).toBe(false);
  });

  it("refuses a validator directory it may not own, before deleting anything", () => {
    const f = fixture();
    for (const outside of [path.parse(process.cwd()).root, process.cwd(), tmpdir()]) {
      const run = spawnSync(
        process.execPath,
        [SCRIPT, "--offline", "--seed-only", "--validator-dir", outside, "--package-lock", f.lock],
        { encoding: "utf8" },
      );
      expect([outside, run.status]).toEqual([outside, 2]);
      expect(run.stderr).toContain("is outside the repository and the temporary directory");
    }
  });

  it("refuses a tarball that no longer matches its pin, offline", () => {
    const f = fixture();
    const tarball = path.join(f.dir, "packages", "example.fhir.terms#2.0.0.tgz");
    writeFileSync(tarball, "altered");
    const result = seed(f);
    expect(result.status).toBe(2);
    expect(result.output).toContain("does not match its pinned checksum (offline)");
  });
});
