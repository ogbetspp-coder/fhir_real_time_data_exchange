#!/usr/bin/env node

import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import {
  PACKAGE_LOCK,
  networkUse,
  packageSummary,
  readPackageLock,
  readSidecarPins,
  readWarningAllowlist,
  validatorWarnings,
  warningVerdict,
} from "./validator-pins.mjs";

// Runs the official HL7 FHIR validator — the same validator_cli.jar and the same five
// implementation-guide packages the worker's sidecar runs, at the same pins — over the
// resources a `{"source":"fixture"}` run sends it, and an authority import's Type 1 record with
// its EMA output, each with the Provenance a document run persists
// (scripts/ci/emit-validation-set.ts), against the same profiles, and over the repository's own
// definitions (fhir/generated/) against base R5. `npm run check`
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
// every such line is printed. It fails too on any "Warning @" line that the reviewed allowlist
// (WARNING_ALLOWLIST) does not name by file, location and message, and on an allowlist entry that
// no warning matched, so the list stays exactly what the validator says. The validator's exit code
// alone is not trusted — a validator that
// ran without its packages reports zero errors and exits 0, so the output must also show every
// -ig package loaded, and a non-zero exit with no error line is reported as a validator failure
// rather than a pass.
//
// The fixture is synthetic (AGENTS.md), so an error line quoting one of its values quotes no
// regulated narrative; this script is not for validating real product content.
//
// usage: node scripts/ci/official-validate.mjs [options]
//   --validator-dir DIR   where the jar and packages are cached (default .cache/official-validator);
//                         below the repository or the temporary directory, since home/ is rebuilt
//   --set-dir DIR         where the resources are emitted (default: a temporary directory)
//   --no-emit             validate what --set-dir already holds instead of emitting it
//   --offline             never download: a missing or checksum-mismatched artefact fails
//   --dockerfile FILE     the sidecar Dockerfile to read pins from (default Dockerfile.validator)
//   --package-lock FILE   the package list to seed from (default fhir/validator-packages.lock)
//   --seed-only           verify the artefacts and rebuild the package cache, then stop
//                         (test/ci/official-validate.test.ts holds the cache to its checksums)

const root = path.resolve(import.meta.dirname, "../..");
const WARNING_ALLOWLIST = path.join(root, "scripts", "ci", "official-validation-warnings.json");

function parseArgs(argv) {
  const options = {
    validatorDir: path.join(root, ".cache", "official-validator"),
    setDir: null,
    emit: true,
    offline: false,
    dockerfile: path.join(root, "Dockerfile.validator"),
    packageLock: path.join(root, PACKAGE_LOCK),
    seedOnly: false,
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
    else if (arg === "--package-lock") options.packageLock = path.resolve(value());
    else if (arg === "--seed-only") options.seedOnly = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  if (!options.emit && options.setDir === null) {
    throw new Error("--no-emit requires --set-dir");
  }
  // Each run deletes <validator-dir>/home, so the directory must be one this gate may own: below
  // the repository or the temporary directory, never either itself or anywhere else.
  const below = (parent) => {
    const relative = path.relative(parent, options.validatorDir);
    return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
  };
  if (!below(root) && !below(path.resolve(tmpdir()))) {
    throw new Error(
      `--validator-dir ${options.validatorDir} is outside the repository and the temporary directory`,
    );
  }
  return options;
}

function sha256Of(file) {
  return createHash("sha256").update(readFileSync(file)).digest("hex");
}

// A pinned artefact in `directory`: kept when its bytes match the pin; otherwise downloaded from
// its URL or, for the repository's own package, copied from the checkout, and kept only if it
// matches.
async function ensureArtefact(directory, { file, url, path: source, sha256 }, offline) {
  const target = path.join(directory, file);
  if (existsSync(target)) {
    if (sha256Of(target) === sha256) {
      console.log(`  ${file}: cached, checksum verified`);
      return;
    }
    if (offline && source === undefined) {
      throw new Error(`${target} does not match its pinned checksum (offline)`);
    }
    console.log(`  ${file}: cached copy does not match its pinned checksum; replacing it`);
    rmSync(target);
  } else if (offline && source === undefined) {
    throw new Error(`${target} is missing (offline)`);
  }

  let bytes;
  if (source !== undefined) {
    console.log(`  ${file}: copying ${source} from the checkout`);
    bytes = readFileSync(path.join(root, source));
  } else {
    console.log(`  ${file}: downloading ${url}`);
    const response = await fetch(url, { redirect: "follow" });
    if (!response.ok) {
      throw new Error(`${url} answered ${response.status} ${response.statusText}`);
    }
    bytes = Buffer.from(await response.arrayBuffer());
  }
  const actual = createHash("sha256").update(bytes).digest("hex");
  if (actual !== sha256) {
    throw new Error(`${file}: checksum ${actual} does not match pinned ${sha256}`);
  }
  const partial = `${target}.partial`;
  writeFileSync(partial, bytes);
  renameSync(partial, target);
  console.log(`  ${file}: ${bytes.length} bytes, checksum verified`);
}

// Installs one pinned package into the gate's own package cache, exactly as Dockerfile.validator
// does in the image: the registry tarball, verified against the lock's checksum, unpacked into
// <home>/.fhir/packages/<id>#<version>. Only the tarball is trusted between runs, and only after
// it is re-hashed; the cache is unpacked afresh from it on every run (a few seconds), because an
// unpacked tree is only as trustworthy as whatever last wrote to it. A marker holding the checksum
// once let an existing tree be reused unread, so a profile altered after unpacking (by an install
// script or tool the CI job ran before its cache was saved) would have been validated against.
async function seedPackage(validatorDir, cache, entry, offline) {
  const tarballs = path.join(validatorDir, "packages");
  mkdirSync(tarballs, { recursive: true });
  await ensureArtefact(
    tarballs,
    { file: `${entry.key}.tgz`, url: entry.url, sha256: entry.sha256 },
    offline,
  );

  const target = path.join(cache, entry.key);
  mkdirSync(target, { recursive: true });
  const untar = spawnSync("tar", ["-xzf", path.join(tarballs, `${entry.key}.tgz`), "-C", target], {
    encoding: "utf8",
  });
  if (untar.error !== undefined || untar.status !== 0) {
    throw new Error(`could not unpack ${entry.key}: ${untar.error?.message ?? untar.stderr}`);
  }
  console.log(`  ${entry.key}: unpacked into the package cache from the verified tarball`);
}

// The id and version a package declares for itself, read from package/package.json inside the
// tarball — the form the validator's "Package Summary" uses.
function igPackageId(tarball) {
  const read = spawnSync("tar", ["-xzOf", tarball, "package/package.json"], {
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
  });
  if (read.error !== undefined || read.status !== 0) {
    throw new Error(`could not read package.json from ${path.basename(tarball)}`);
  }
  const { name, version } = JSON.parse(read.stdout);
  if (typeof name !== "string" || typeof version !== "string") {
    throw new Error(`${path.basename(tarball)}: package.json has no name and version`);
  }
  return `${name}#${version}`;
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

// Validates several files of the set against the same profiles in one run of the validator, which
// loads its packages once (about 40 of a run's 45 seconds). It reports each file under a
// "-- <path> ---" header; each file's issues and warnings are read from its own report. A single
// file's report is the whole output.
function validate(java, pins, validatorDir, home, setDir, files, profiles) {
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
    ...files.map((file) => path.join(setDir, file)),
    ...pins.flags,
  ];
  // Package paths are relative to the working directory, which is the validator directory, so
  // the validator's own "Load <file>#<version>" line names the file the way it is checked below.
  for (const file of pins.packages) args.push("-ig", file);
  for (const profile of profiles) args.push("-profile", profile);

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

  // Each file's report; an issue or warning outside every report is stray.
  const reports = new Map(files.map((file) => [file, files.length === 1 ? lines : []]));
  let current = [];
  const outside = current;
  if (files.length > 1) {
    for (const line of lines) {
      const header = /^-- (.+?) -{3,}$/.exec(line);
      if (header !== null) current = reports.get(path.basename(header[1])) ?? outside;
      else if (/^-{10,}$/.test(line)) current = outside;
      else current.push(line);
    }
  }
  const byFile = new Map(
    [...reports].map(([file, report]) => [
      file,
      {
        reported: report.length > 0,
        issues: report.filter((line) => ISSUE_LINE.test(line)),
        warnings: validatorWarnings(report),
      },
    ]),
  );
  const stray = outside.filter((line) => ISSUE_LINE.test(line) || /^\s*Warning @/.test(line));

  return {
    status: run.status,
    byFile,
    stray,
    unloaded,
    network: networkUse(lines),
    loaded: packageSummary(lines),
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
  const lock = readPackageLock(options.packageLock);
  // The validator's home, package cache and all, rebuilt from nothing on every run: whatever an
  // earlier run, a restored CI cache or anything else left in it is deleted unread, so a package
  // dropped from the list cannot linger either. (Kept at this path rather than in a temporary
  // directory so scripts/dev/validator-server.sh can run the validator on the same cache.)
  const home = path.join(options.validatorDir, "home");
  rmSync(home, { recursive: true, force: true });
  // The checksum markers of the earlier cache, which nothing reads any more.
  rmSync(path.join(options.validatorDir, "seeded"), { recursive: true, force: true });
  const cache = path.join(home, ".fhir", "packages");
  mkdirSync(cache, { recursive: true });
  writeFileSync(path.join(cache, "packages.ini"), "[cache]\nversion = 4\n");
  console.log(
    `Package cache from ${path.relative(root, options.packageLock)} (${lock.length} packages):`,
  );
  const listed = new Set(lock.map(({ key }) => key));
  for (const entry of lock) await seedPackage(options.validatorDir, cache, entry, options.offline);
  const installed = readdirSync(cache).filter((name) => name.includes("#"));
  if (installed.length !== listed.size || installed.some((name) => !listed.has(name))) {
    throw new Error(`the package cache holds ${installed.join(", ")}, not the listed packages`);
  }
  if (options.seedOnly) {
    console.log("Package cache rebuilt from the verified tarballs (--seed-only).");
    return;
  }

  // Everything the validator may load: the listed packages and the five -ig files, by the id and
  // version each declares in its own package.json.
  const pinned = new Set(listed);
  for (const file of pins.packages) pinned.add(igPackageId(path.join(options.validatorDir, file)));

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

  const allowlist = readWarningAllowlist(WARNING_ALLOWLIST);
  const java = javaExecutable();
  let total = 0;
  const warnings = [];
  let failed = false;
  const summary = [];
  // One validator run per set of profiles, in the set's order.
  const batches = new Map();
  for (const entry of entries) {
    const key = JSON.stringify(entry.profiles);
    batches.set(key, [...(batches.get(key) ?? []), entry]);
  }
  for (const batch of batches.values()) {
    const files = batch.map(({ file }) => file);
    const { profiles } = batch[0];
    const which = files.join(", ");
    console.log(
      `\nValidating ${batch.map(({ file, resourceType }) => `${file} (${resourceType})`).join(", ")} against ${profiles.length} profile${profiles.length === 1 ? "" : "s"}`,
    );
    for (const profile of profiles) console.log(`  -profile ${profile}`);
    const result = validate(java, pins, options.validatorDir, home, setDir, files, profiles);

    const { installs, refused, other } = result.network;
    if (installs.length > 0 || other.length > 0) {
      failed = true;
      console.log("  VALIDATOR FAILURE: the validator needed something that is not pinned");
      for (const line of [...installs, ...other]) console.log(`  | ${line.trim()}`);
      summary.push(`${which}: ${installs.length} install(s), ${other.length} fetch error(s)`);
      continue;
    }
    if (result.loaded === undefined) {
      failed = true;
      console.log("  VALIDATOR FAILURE: no package summary, so what was loaded is unknown");
      for (const line of result.tail) console.log(`  | ${line}`);
      summary.push(`${which}: no package summary`);
      continue;
    }
    const unpinned = result.loaded.filter((key) => !pinned.has(key));
    if (unpinned.length > 0) {
      failed = true;
      console.log(
        `  VALIDATOR FAILURE: loaded packages that are not pinned: ${unpinned.join(", ")}`,
      );
      summary.push(`${which}: loaded unpinned ${unpinned.join(", ")}`);
      continue;
    }
    console.log(`  loaded ${result.loaded.length} packages, every one pinned`);
    if (refused.length > 0) {
      console.log(
        `  ${refused.length} optional lookup(s) refused by -no-http-access; no network used:`,
      );
      for (const line of refused) console.log(`  | ${line.trim()}`);
    }

    if (result.unloaded.length > 0) {
      failed = true;
      console.log(`  VALIDATOR FAILURE: packages not loaded: ${result.unloaded.join(", ")}`);
      for (const line of result.tail) console.log(`  | ${line}`);
      summary.push(`${which}: validator did not load ${result.unloaded.join(", ")}`);
      continue;
    }
    const unreported = files.filter((file) => !result.byFile.get(file).reported);
    if (unreported.length > 0 || result.stray.length > 0) {
      failed = true;
      console.log(
        `  VALIDATOR FAILURE: no report for ${unreported.join(", ") || "none"}; ${result.stray.length} issue(s) outside every report`,
      );
      for (const line of [...result.stray, ...result.tail]) console.log(`  | ${line}`);
      summary.push(`${which}: the validator's reports could not be read`);
      continue;
    }
    const errors = [...result.byFile.values()].reduce((sum, { issues }) => sum + issues.length, 0);
    if (errors === 0 && result.status !== 0) {
      failed = true;
      console.log(`  VALIDATOR FAILURE: exit ${result.status} with no error line`);
      for (const line of result.tail) console.log(`  | ${line}`);
      summary.push(`${which}: validator exited ${result.status} without a verdict`);
      continue;
    }
    for (const [file, { issues, warnings: found }] of result.byFile) {
      for (const line of issues) console.log(`  ${file}: ${line.trim()}`);
      total += issues.length;
      for (const { location, message } of found) warnings.push({ file, location, message });
      const counts = `${issues.length} errors, ${found.length} warnings`;
      console.log(`  ${file}: ${counts}`);
      summary.push(`${file}: ${counts}`);
    }
  }

  const { unlisted, stale } = warningVerdict(warnings, allowlist);
  for (const { file, location, message } of unlisted) {
    console.log(`\nWarning not in the allowlist: ${file} @ ${location}\n  ${message}`);
  }
  for (const { file, location, message } of stale) {
    console.log(
      `\nAllowlisted warning the validator no longer gives: ${file} @ ${location}\n  ${message}`,
    );
  }

  console.log("\nSummary");
  for (const line of summary) console.log(`  ${line}`);
  console.log(`  total: ${total} errors across ${entries.length} resources`);
  console.log(
    `  warnings: ${warnings.length}, ${warnings.length - unlisted.length} allowlisted, ${unlisted.length} not; allowlist: ${allowlist.length} entries, ${stale.length} stale (${path.relative(root, WARNING_ALLOWLIST)})`,
  );

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
  if (unlisted.length > 0 || stale.length > 0) {
    console.log(
      `::error title=Official validation::${unlisted.length} warnings not in the allowlist, ${stale.length} stale allowlist entries`,
    );
    process.exit(1);
  }
  console.log(
    "Official validation passed: every resource conforms to every profile, with only allowlisted warnings.",
  );
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(2);
});
