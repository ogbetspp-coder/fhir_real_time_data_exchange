#!/usr/bin/env node

import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

// Runs the official HL7 FHIR validator — the same validator_cli.jar and the same four
// implementation-guide packages the worker's sidecar runs, at the same pins — over the four
// resources a `{"source":"fixture"}` run sends it, against the same profiles. `npm run check`
// runs only the local structural preflights; official validation otherwise happens solely
// inside the deployed pipeline, which is how a fixture that fails it stayed invisible until a
// run was attempted. This is the CI gate that makes that class of defect visible on the pull
// request instead.
//
// One source of truth: the validator version, the package URLs, every checksum, and the
// sidecar's flags (-version, -tx, and the ordered -ig list) are parsed from Dockerfile.validator
// at run time, never restated here. A Dockerfile whose ARG, RUN, or CMD lines cannot be parsed
// fails the run rather than validating with a guessed pin.
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

// Comment lines dropped and continuation lines joined, as the builder reads them.
function instructions(text) {
  const joined = [];
  let open = null;
  for (const line of text.split(/\r?\n/)) {
    if (/^\s*#/.test(line)) continue;
    const continued = /\\\s*$/.test(line);
    const body = continued ? line.replace(/\\\s*$/, "") : line;
    if (open === null) open = body;
    else open += ` ${body.trim()}`;
    if (!continued) {
      joined.push(open);
      open = null;
    }
  }
  if (open !== null) joined.push(open);
  return joined;
}

const SHA256_HEX = /^[0-9a-f]{64}$/;

// The pins: ARG name=value lines, the `curl ... "<url>" -o <file> && echo "${SHA} <file>" |
// sha256sum --check` pairs of the RUN instruction with ${ARG} substituted, and the sidecar's
// validator flags from the CMD line. Each is required; a Dockerfile that has drifted from this
// shape fails here rather than being silently half-read.
function readSidecarPins(dockerfile) {
  const lines = instructions(readFileSync(dockerfile, "utf8"));
  const name = path.basename(dockerfile);

  const args = new Map();
  for (const line of lines) {
    const match = /^\s*ARG\s+([A-Z0-9_]+)=(\S+)\s*$/.exec(line);
    if (match !== null) args.set(match[1], match[2]);
  }
  if (args.size === 0) throw new Error(`${name}: no ARG name=value line could be parsed`);

  const substitute = (text) =>
    text.replace(/\$\{([A-Z0-9_]+)\}/g, (_, key) => {
      const value = args.get(key);
      if (value === undefined) throw new Error(`${name}: \${${key}} is not declared by an ARG`);
      return value;
    });

  const artefacts = [];
  const download =
    /curl\s+(?:-\S+\s+)*"([^"]+)"\s+-o\s+(\S+)\s+&&\s+echo\s+"\$\{([A-Z0-9_]+)\}\s+(\S+)"\s+\|\s+sha256sum\s+--check/g;
  for (const line of lines) {
    if (!/^\s*RUN\b/.test(line)) continue;
    for (const match of line.matchAll(download)) {
      const [, url, file, checksumArg, checkedFile] = match;
      if (file !== checkedFile) {
        throw new Error(`${name}: ${file} is downloaded but ${checkedFile} is checksummed`);
      }
      const sha256 = args.get(checksumArg);
      if (sha256 === undefined || !SHA256_HEX.test(sha256)) {
        throw new Error(`${name}: ARG ${checksumArg} is missing or is not a SHA-256 hex digest`);
      }
      artefacts.push({ file, url: substitute(url), sha256 });
    }
  }
  const jar = artefacts.find(({ file }) => file === "validator_cli.jar");
  if (jar === undefined) throw new Error(`${name}: no checksummed validator_cli.jar download`);

  const cmd = lines.find((line) => /^\s*CMD\s+\[/.test(line));
  if (cmd === undefined) throw new Error(`${name}: no CMD [...] line`);
  let tokens;
  try {
    tokens = JSON.parse(cmd.replace(/^\s*CMD\s+/, ""));
  } catch {
    throw new Error(`${name}: the CMD line is not a JSON array`);
  }
  // The server-mode arguments (subcommand, port, -allowNetworkAccess) are the sidecar's; the
  // validation arguments are shared with CLI mode and are what is reproduced here.
  const flags = [];
  const packages = [];
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === "-version" || token === "-tx") {
      const value = tokens[index + 1];
      if (typeof value !== "string") throw new Error(`${name}: CMD ${token} has no value`);
      flags.push(token, value);
      index += 1;
    } else if (token === "-ig") {
      const value = tokens[index + 1];
      if (typeof value !== "string") throw new Error(`${name}: CMD -ig has no value`);
      const file = path.posix.basename(value);
      if (!artefacts.some((artefact) => artefact.file === file)) {
        throw new Error(`${name}: CMD loads ${value}, which the RUN line does not download`);
      }
      packages.push(file);
      index += 1;
    }
  }
  if (!flags.includes("-version") || !flags.includes("-tx")) {
    throw new Error(`${name}: CMD does not carry both -version and -tx`);
  }
  if (packages.length === 0) throw new Error(`${name}: CMD loads no -ig package`);
  const expectedPackages = artefacts.filter(({ file }) => file !== "validator_cli.jar");
  if (packages.length !== expectedPackages.length) {
    throw new Error(
      `${name}: CMD loads ${packages.length} packages but the RUN line downloads ${expectedPackages.length}`,
    );
  }

  return { version: args.get("VALIDATOR_VERSION") ?? "unknown", artefacts, flags, packages };
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

function validate(java, pins, validatorDir, setDir, entry) {
  const args = [
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

  return { status: run.status, issues, unloaded, tail: lines.filter(Boolean).slice(-30) };
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
    const result = validate(java, pins, options.validatorDir, setDir, entry);

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
