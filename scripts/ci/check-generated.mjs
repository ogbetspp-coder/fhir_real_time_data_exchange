#!/usr/bin/env node

import { execSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

// Runs a generator and fails if it changed, added, or removed any file under the given paths.
// Unlike `git diff --exit-code`, this sees untracked files, so generated artifacts that were
// never committed cannot pass the gate.
//
// usage: node scripts/ci/check-generated.mjs --run "<command>" -- <path> [<path>...]

const args = process.argv.slice(2);
const runIndex = args.indexOf("--run");
const separator = args.indexOf("--");
const command = runIndex === -1 ? undefined : args[runIndex + 1];
const targets = separator === -1 ? [] : args.slice(separator + 1);
if (command === undefined || targets.length === 0) {
  console.error('usage: check-generated.mjs --run "<command>" -- <path> [<path>...]');
  process.exit(2);
}

function snapshot(paths) {
  const digests = new Map();
  const visit = (entry) => {
    if (!existsSync(entry)) {
      // A guarded path that does not exist would make the gate pass vacuously; fail closed.
      console.error(`Guarded path does not exist: ${entry}`);
      process.exit(1);
    }
    if (statSync(entry).isDirectory()) {
      for (const name of readdirSync(entry).sort()) visit(path.join(entry, name));
      return;
    }
    digests.set(entry, createHash("sha256").update(readFileSync(entry)).digest("hex"));
  };
  for (const target of paths) visit(target);
  return digests;
}

const before = snapshot(targets);
execSync(command, { stdio: "inherit" });
const after = snapshot(targets);

const changed = [...new Set([...before.keys(), ...after.keys()])]
  .filter((file) => before.get(file) !== after.get(file))
  .sort();

if (changed.length > 0) {
  console.error(
    `Generated files are out of date; run the generator and commit the result:\n${changed
      .map((file) => `  ${file}`)
      .join("\n")}`,
  );
  process.exit(1);
}
console.log(`Generated files are up to date (${after.size} files checked)`);
