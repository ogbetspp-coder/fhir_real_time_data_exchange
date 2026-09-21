#!/usr/bin/env node

import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import {
  PACKAGE_LOCK,
  downloadAttempts,
  readPackageLock,
  readSidecarPins,
} from "./validator-pins.mjs";

// Runs the official HL7 FHIR validator — the same validator_cli.jar and the same four
// implementation-guide packages the worker's sidecar runs, at the same pins — over the four
// resources a `{"source":"fixture"}` run sends it, against the same profiles. `npm run check`
// runs only the local structural preflights; official validation otherwise happens solely
// inside the deployed pipeline, which is how a fixture that fails it stayed invisible until a
// run was attempted. This is the CI gate that makes that class of defect visible on the pull
// request instead.
//
// One source of truth: the validator version, the package URLs, every checksum, the sidecar's
// flags (-version, -jurisdiction, -locale, -tx, and the ordered -ig list) and its JVM properties
// are parsed from Dockerfile.validator at run time, and the packages the validator resolves on
// its own come from fhir/validator-packages.lock — the same list the image installs. Nothing is
// restated here. A Dockerfile or list that cannot be parsed fails the run rather than
// validating with a guessed pin (scripts/ci/validator-pins.mjs).
//
// Hermetic: the validator runs with the sidecar's own closed proxy and a package cache seeded
// from the list, so it cannot fetch anything. Any line showing it tried to — installing a
// package, fetching, or failing to fetch — fails the run as a validator failure.
//
// Verdict: the run fails on any "Error @" or "Fatal @" line in the validator's output, and
// every such line is printed. The validator's exit code alone is not trusted — a validator that
// ran without its packages reports zero errors and exits 0, so the output must also show every
// -ig package loaded, and a non-zero exit with no error line is reported as a validator failure
// rather than a pass.
//
// The fixture is synthetic (AGENTS.md), so an error line quoting one of its values quotes no
// regulated narrative; this script is not for validating real product content.
//
// usage: node scripts/ci/official-validate.mjs [options]
//   --validator-dir DIR   where the jar and packages are cached (default .cache/official-validator)
//   --set-dir DIR         where the four resources are emitted (default: a temporary directory)
//   --no-emit             validate what --set-dir already holds instead of emitting it
//   --offline             never download: a missing or checksum-mismatched artefact fails
//   --dockerfile FILE     the sidecar Dockerfile to read pins from (default Dockerfile.validator)

const root = path.resolve(import.meta.dirname, "../..");

function parseArgs(argv) {
  const options = {
    validatorDir: path.join(root, ".cache", "official-validator"),
    setDir: null,
    emit: true,
    offline: false,
    dockerfile: path.join(root, "Dockerfile.validator"),
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const value = () => {
      const next = argv[index + 1];
      if (next === undefined) throw new Error(`${arg} requires a value`);
      index += 1;
      return next;
    };
    if (arg === "--validator-dir") options.validatorDir = path.resolve(value());
    else if (arg === "--set-dir") options.setDir = path.resolve(value());
    else if (arg === "--no-emit") options.emit = false;
    else if (arg === "--offline") options.offline = true;
    else if (arg === "--dockerfile") options.dockerfile = path.resolve(value());
    else throw new Error(`Unknown argument: ${arg}`);
  }
  if (!options.emit && options.setDir === null) {
    throw new Error("--no-emit requires --set-dir");
  }
  return options;
}

function sha256Of(file) {
  return createHash("sha256").update(readFileSync(file)).digest("hex");
}

async function ensureArtefact(directory, { file, url, sha256 }, offline) {
  const target = path.join(directory, file);
  if (existsSync(target)) {
    if (sha256Of(target) === sha256) {
      console.log(`  ${file}: cached, checksum verified`);
      return;
    }
    if (offline) throw new Error(`${target} does not match its pinned checksum (offline)`);
    console.log(`  ${file}: cached copy does not match its pinned checksum; re-downloading`);
    rmSync(target);
  } else if (offline) {
    throw new Error(`${target} is missing (offline)`);
  }

  console.log(`  ${file}: downloading ${url}`);
  const response = await fetch(url, { redirect: "follow" });
  if (!response.ok) {
    throw new Error(`${url} answered ${response.status} ${response.statusText}`);
  }
  const bytes = Buffer.from(await response.arrayBuffer());
  const actual = createHash("sha256").update(bytes).digest("hex");
  if (actual !== sha256) {
    throw new Error(`${file}: downloaded checksum ${actual} does not match pinned ${sha256}`);
  }
  const partial = `${target}.partial`;
  writeFileSync(partial, bytes);
  renameSync(partial, target);
  console.log(`  ${file}: ${bytes.length} bytes, checksum verified`);
}

// Installs one pinned package into the gate's own package cache, exactly as Dockerfile.validator
// does in the image: the registry tarball, verified against the lock's checksum, unpacked into
// <home>/.fhir/packages/<id>#<version>. A marker outside the cache records which checksum was
// unpacked, so a changed pin re-unpacks and an unchanged one is not unpacked twice.
async function seedPackage(validatorDir, home, entry, offline) {
  const tarballs = path.join(validatorDir, "packages");
  mkdirSync(tarballs, { recursive: true });
  await ensureArtefact(
    tarballs,
    { file: `${entry.key}.tgz`, url: entry.url, sha256: entry.sha256 },
    offline,
  );

  const cache = path.join(home, ".fhir", "packages");
  const target = path.join(cache, entry.key);
  const markers = path.join(validatorDir, "seeded");
  const marker = path.join(markers, entry.key);
  mkdirSync(markers, { recursive: true });
  if (existsSync(target) && existsSync(marker) && readFileSync(marker, "utf8") === entry.sha256) {
    return;
  }
  rmSync(target, { recursive: true, force: true });
  mkdirSync(target, { recursive: true });
  const untar = spawnSync("tar", ["-xzf", path.join(tarballs, `${entry.key}.tgz`), "-C", target], {
    encoding: "utf8",
  });
  if (untar.error !== undefined || untar.status !== 0) {
    throw new Error(`could not unpack ${entry.key}: ${untar.error?.message ?? untar.stderr}`);
  }
  writeFileSync(marker, entry.sha256);
  console.log(`  ${entry.key}: unpacked into the package cache`);
}

function emitValidationSet(setDir) {
  const tsx = path.join(root, "node_modules", ".bin", "tsx");
  const script = path.join(root, "scripts", "ci", "emit-validation-set.ts");
  const run = spawnSync(tsx, [script, setDir], { cwd: root, encoding: "utf8" });
  if (run.error !== undefined) throw run.error;
  if (run.status !== 0) {
    process.stderr.write(run.stderr);
    throw new Error(`emit-validation-set.ts exited ${run.status}`);
  }
}

function javaExecutable() {
  const home = process.env.JAVA_HOME;
  if (home !== undefined && home !== "") return path.join(home, "bin", "java");
  return "java";
}

const ISSUE_LINE = /^\s*(?:Error|Fatal) @/;

function validate(java, pins, validatorDir, home, setDir, entry) {
  // The sidecar's own JVM properties — the closed proxy included — with the package cache moved
  // to the gate's seeded copy.
  const properties = pins.jvmProperties.map((property) =>
    property.startsWith("-Duser.home=") ? `-Duser.home=${home}` : property,
  );
  const args = [
    ...properties,
    "-Xmx3g",
    "-jar",
    path.join(validatorDir, "validator_cli.jar"),
    path.join(setDir, entry.file),
    ...pins.flags,
  ];
  // Package paths are relative to the working directory, which is the validator directory, so
  // the validator's own "Load <file>#<version>" line names the file the way it is checked below.
  for (const file of pins.packages) args.push("-ig", file);
  for (const profile of entry.profiles) args.push("-profile", profile);

  const run = spawnSync(java, args, {
    cwd: validatorDir,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });
  if (run.error !== undefined) {
    throw new Error(`Could not start ${java}: ${run.error.message}`);
  }
  const output = `${run.stdout}\n${run.stderr}`;
  const lines = output.split(/\r?\n/);

  // The proof that the packages were in play: the validator names each -ig it loaded. A run
  // without them reports zero errors on a resource the profiles would reject.
  const unloaded = pins.packages.filter((file) => {
    const loaded = new RegExp(`^\\s*Load (?:.*/)?${file.replace(/\./g, "\\.")}#`);
    return !lines.some((line) => loaded.test(line));
  });
  const issues = lines.filter((line) => ISSUE_LINE.test(line));

  return {
    status: run.status,
    issues,
    unloaded,
    attempts: downloadAttempts(lines),
    summary: lines.find((line) => /Package Summary/.test(line))?.trim(),
    tail: lines.filter(Boolean).slice(-30),
  };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const pins = readSidecarPins(options.dockerfile);
  console.log(
    `Official validation: validator ${pins.version}, ${pins.packages.length} packages, flags ${pins.flags.join(" ")} (from ${path.relative(root, options.dockerfile)})`,
  );

  mkdirSync(options.validatorDir, { recursive: true });
  console.log(`Validator artefacts in ${options.validatorDir}:`);
  for (const artefact of pins.artefacts) {
    await ensureArtefact(options.validatorDir, artefact, options.offline);
  }

  // The packages the validator resolves beyond the -ig files, from the same list the image
  // installs. The validator then runs with no network route: a dependency missing from the list
  // is a failure, not a download.
  const lock = readPackageLock(path.join(root, PACKAGE_LOCK));
  const home = path.join(options.validatorDir, "home");
  const cache = path.join(home, ".fhir", "packages");
  mkdirSync(cache, { recursive: true });
  writeFileSync(path.join(cache, "packages.ini"), "[cache]\nversion = 4\n");
  console.log(`Package cache from ${PACKAGE_LOCK} (${lock.length} packages):`);
  for (const entry of lock) await seedPackage(options.validatorDir, home, entry, options.offline);

  const setDir =
    options.setDir ??
    path.join(tmpdir(), `official-validation-set-${process.pid}-${Date.now().toString(36)}`);
  if (options.emit) {
    mkdirSync(setDir, { recursive: true });
    console.log(`Emitting the fixture's validation set to ${setDir}`);
    emitValidationSet(setDir);
  }
  const manifest = path.join(setDir, "validation-set.json");
  if (!existsSync(manifest)) throw new Error(`${manifest} is missing`);
  const entries = JSON.parse(readFileSync(manifest, "utf8"));
  if (!Array.isArray(entries) || entries.length === 0) {
    throw new Error(`${manifest} holds no entries`);
  }

  const java = javaExecutable();
  let total = 0;
  let failed = false;
  const summary = [];
  for (const entry of entries) {
    const label = `${entry.file} (${entry.resourceType}) against ${entry.profiles.length} profile${entry.profiles.length === 1 ? "" : "s"}`;
    console.log(`\nValidating ${label}`);
    for (const profile of entry.profiles) console.log(`  -profile ${profile}`);
    const result = validate(java, pins, options.validatorDir, home, setDir, entry);

    if (result.attempts.length > 0) {
      failed = true;
      console.log("  VALIDATOR FAILURE: the validator reached for the network");
      for (const line of result.attempts) console.log(`  | ${line.trim()}`);
      summary.push(`${entry.file}: validator attempted ${result.attempts.length} download(s)`);
      continue;
    }
    if (result.summary !== undefined) console.log(`  ${result.summary}`);

    if (result.unloaded.length > 0) {
      failed = true;
      console.log(`  VALIDATOR FAILURE: packages not loaded: ${result.unloaded.join(", ")}`);
      for (const line of result.tail) console.log(`  | ${line}`);
      summary.push(`${entry.file}: validator did not load ${result.unloaded.join(", ")}`);
      continue;
    }
    if (result.issues.length === 0 && result.status !== 0) {
      failed = true;
      console.log(`  VALIDATOR FAILURE: exit ${result.status} with no error line`);
      for (const line of result.tail) console.log(`  | ${line}`);
      summary.push(`${entry.file}: validator exited ${result.status} without a verdict`);
      continue;
    }
    for (const line of result.issues) console.log(`  ${line.trim()}`);
    total += result.issues.length;
    console.log(`  ${result.issues.length} error${result.issues.length === 1 ? "" : "s"}`);
    summary.push(`${entry.file}: ${result.issues.length} errors`);
  }

  console.log("\nSummary");
  for (const line of summary) console.log(`  ${line}`);
  console.log(`  total: ${total} errors across ${entries.length} resources`);

  if (!options.setDir) rmSync(setDir, { recursive: true, force: true });

  if (failed) {
    console.log("::error title=Official validation::the validator did not deliver a verdict");
    process.exit(2);
  }
  if (total > 0) {
    console.log(
      `::error title=Official validation::${total} errors from the official HL7 validator`,
    );
    process.exit(1);
  }
  console.log("Official validation passed: every resource conforms to every profile.");
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(2);
});
