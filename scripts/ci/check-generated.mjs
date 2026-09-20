#!/usr/bin/env node

import { execSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

// Runs a generator and fails if it changed, added, or removed any file under the given paths,
// or left a file under them untouched (a stale artifact the generator no longer produces).
// Unlike `git diff --exit-code`, this sees untracked files, so generated artifacts that were
// never committed cannot pass the gate. Generators must rewrite every file they own on each
// run; a file whose modification time does not move is treated as not generated.
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
    digests.set(entry, {
      digest: createHash("sha256").update(readFileSync(entry)).digest("hex"),
      mtimeMs: statSync(entry).mtimeMs,
    });
  };
  for (const target of paths) visit(target);
  return digests;
}

const before = snapshot(targets);
execSync(command, { stdio: "inherit" });
const after = snapshot(targets);

const files = [...new Set([...before.keys(), ...after.keys()])].sort();
const changed = files.filter((file) => before.get(file)?.digest !== after.get(file)?.digest);
const stale = files.filter((file) => {
  const previous = before.get(file);
  const current = after.get(file);
  return previous !== undefined && current !== undefined && previous.mtimeMs === current.mtimeMs;
});

if (changed.length > 0) {
  console.error(
    `Generated files are out of date; run the generator and commit the result:\n${changed
      .map((file) => `  ${file}`)
      .join("\n")}`,
  );
  process.exit(1);
}
if (stale.length > 0) {
  console.error(
    `Files under the guarded paths were not produced by the generator; remove them:\n${stale
      .map((file) => `  ${file}`)
      .join("\n")}`,
  );
  process.exit(1);
}
console.log(`Generated files are up to date (${after.size} files checked)`);
