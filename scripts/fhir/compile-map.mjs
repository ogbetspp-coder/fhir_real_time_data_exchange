#!/usr/bin/env node
// Compiles the StructureMap twin (fhir/maps/type2-to-ema-cap-smpc-en.map, in the FHIR mapping
// language) into fhir/maps/StructureMap-type2-to-ema-cap-smpc-en.json with the pinned HL7
// validator, offline: the jar and the package cache that `npm run validate:official` verifies and
// seeds (.cache/official-validator), the sidecar's JVM properties (no route out) and its flags.
// scripts/fhir/generate-artifacts.ts copies the result into fhir/generated/ and the repository's
// own package. Needs Java 21.
//
//   node scripts/fhir/compile-map.mjs [--check] [--validator-dir DIR]
//
// --check compiles and fails if the committed file says anything else (test/official/ runs it in
// CI's Official validation job). The validator's FML metadata sets url, name, title, status,
// experimental and description but not id or version, and its `compile` exits 0 when it could not
// read the map, so the result is read from the file it writes, never from its exit code; this
// sets `id` (the map's file name) and `version` (the mapping manifest's mappingVersion).

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { readSidecarPins } from "../ci/validator-pins.mjs";

const root = path.resolve(import.meta.dirname, "../..");
export const MAP_SOURCE = path.join(root, "fhir", "maps", "type2-to-ema-cap-smpc-en.map");
export const MAP_COMPILED = path.join(
  root,
  "fhir",
  "maps",
  "StructureMap-type2-to-ema-cap-smpc-en.json",
);
export const MAP_ID = "type2-to-ema-cap-smpc-en";
export const MAP_URL = `https://khs.dev/fhir/StructureMap/${MAP_ID}`;
export const DEFAULT_VALIDATOR_DIR = path.join(root, ".cache", "official-validator");

function sha256(file) {
  return createHash("sha256").update(readFileSync(file)).digest("hex");
}

// The pinned validator in `validatorDir`: the jar and each -ig package of the sidecar, each held
// to its pin in Dockerfile.validator. A missing or different file fails: run
// `npm run validate:official` (or its --seed-only), which fetches, checks and caches them.
export function pinnedValidator(validatorDir = DEFAULT_VALIDATOR_DIR) {
  const pins = readSidecarPins(path.join(root, "Dockerfile.validator"));
  for (const artefact of pins.artefacts) {
    const file = path.join(validatorDir, artefact.file);
    if (!existsSync(file) || sha256(file) !== artefact.sha256) {
      throw new Error(
        `${file} is missing or not the pinned ${artefact.sha256}; run npm run validate:official first`,
      );
    }
  }
  const home = path.join(validatorDir, "home");
  if (!existsSync(path.join(home, ".fhir", "packages"))) {
    throw new Error(`${home} holds no package cache; run npm run validate:official first`);
  }
  return {
    version: pins.version,
    jar: path.join(validatorDir, "validator_cli.jar"),
    packages: pins.packages.map((file) => path.join(validatorDir, file)),
    flags: pins.flags,
    jvm: pins.jvmProperties.map((property) =>
      property.startsWith("-Duser.home=") ? `-Duser.home=${home}` : property,
    ),
    java: process.env.JAVA_HOME ? path.join(process.env.JAVA_HOME, "bin", "java") : "java",
  };
}

function compileFlags(flags) {
  const kept = [];
  for (let index = 0; index < flags.length; index += 1) {
    if (flags[index] === "-version" || flags[index] === "-tx") {
      kept.push(flags[index], flags[index + 1]);
      index += 1;
    } else if (flags[index] === "-no-http-access") {
      kept.push(flags[index]);
    } else if (flags[index] === "-jurisdiction" || flags[index] === "-locale") {
      index += 1;
    }
  }
  return kept;
}

// The map as the pinned validator compiles it, with its id and version set.
export function compileMap(validator = pinnedValidator()) {
  const out = mkdtempSync(path.join(tmpdir(), "compile-map-"));
  try {
    const file = path.join(out, "map.json");
    const run = spawnSync(
      validator.java,
      [
        ...validator.jvm,
        "-Xmx1536m",
        "-jar",
        validator.jar,
        "compile",
        MAP_URL,
        "-ig",
        MAP_SOURCE,
        // The sidecar's -version, -tx and -no-http-access: `compile` takes no -jurisdiction or
        // -locale, and a map has neither.
        ...compileFlags(validator.flags),
        "-output",
        file,
      ],
      { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 },
    );
    if (run.error !== undefined) throw run.error;
    if (!existsSync(file)) {
      const output = `${run.stdout}\n${run.stderr}`;
      const why = output.split("\n").filter((line) => /ignored due to error|Failure/.test(line));
      throw new Error(`the validator compiled nothing: ${why.join(" ") || `exit ${run.status}`}`);
    }
    const compiled = JSON.parse(readFileSync(file, "utf8"));
    if (compiled.resourceType !== "StructureMap" || compiled.url !== MAP_URL) {
      throw new Error(`the validator wrote no StructureMap ${MAP_URL}`);
    }
    const { mappingVersion } = JSON.parse(
      readFileSync(path.join(root, "fhir", "mappings", "cap-smpc-en.json"), "utf8"),
    );
    const { resourceType, url, ...rest } = compiled;
    delete rest.id;
    return { resourceType, id: MAP_ID, url, version: mappingVersion, ...rest };
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

export function serialise(map) {
  return `${JSON.stringify(map, null, 2)}\n`;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const argv = process.argv.slice(2);
  const check = argv.includes("--check");
  const at = argv.indexOf("--validator-dir");
  const map = compileMap(pinnedValidator(at === -1 ? undefined : path.resolve(argv[at + 1])));
  if (check) {
    if (readFileSync(MAP_COMPILED, "utf8") !== serialise(map)) {
      console.error(
        `${path.relative(root, MAP_COMPILED)} is not what the pinned validator compiles from ${path.relative(root, MAP_SOURCE)}; run node scripts/fhir/compile-map.mjs`,
      );
      process.exit(1);
    }
    console.log(`${path.relative(root, MAP_COMPILED)} is what the pinned validator compiles`);
  } else {
    writeFileSync(MAP_COMPILED, serialise(map));
    console.log(
      `compiled ${path.relative(root, MAP_SOURCE)} into ${path.relative(root, MAP_COMPILED)}`,
    );
  }
}
