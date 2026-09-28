#!/usr/bin/env node
// The deploy's inputs, sealed where they are made and verified where they are used (audit B08,
// D-1). scripts/gcp/deploy-inputs.sh makes them in a job that holds no cloud credential (the
// deploy workflow's gate job); scripts/gcp/bootstrap.sh uses them in the job that holds the
// deployer's, which runs no installed package. Node built-ins only.
//
//   node scripts/fhir/deploy-inputs.mjs seal <dir>
//     Writes <dir>/manifest.json, every other file's path and SHA-256, and prints the manifest's
//     own SHA-256: the one value the using job must be given by a separate channel (a job output).
//
//   node scripts/fhir/deploy-inputs.mjs verify <dir> <manifest sha256>
//     Refuses the directory unless the manifest has that hash; every file it names is present
//     with its hash and no other file is; every standard the bootstrap uses is present under its
//     exact name with the SHA-256 fhir/standards.lock.json pins (a check that does not depend on
//     the job that made the directory); and the synthetic fixture is there.

import { readdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";

import { outputName, readLock, sha256File } from "./standards.mjs";

const MANIFEST = "manifest.json";
const FIXTURE = "synthetic-type2.json";
const CONSUMER = "bootstrap";

async function files(dir, prefix = "") {
  const found = [];
  for (const entry of await readdir(path.join(dir, prefix), { withFileTypes: true })) {
    const relative = prefix === "" ? entry.name : `${prefix}/${entry.name}`;
    if (entry.isDirectory()) found.push(...(await files(dir, relative)));
    else if (entry.isFile()) found.push(relative);
    else throw new Error(`${relative} is neither a file nor a directory`);
  }
  return found.sort();
}

async function seal(dir) {
  const manifest = {};
  for (const file of await files(dir)) {
    if (file === MANIFEST) continue;
    manifest[file] = await sha256File(path.join(dir, file));
  }
  const text = `${JSON.stringify({ files: manifest }, null, 2)}\n`;
  await writeFile(path.join(dir, MANIFEST), text);
  return createHash("sha256").update(text).digest("hex");
}

async function verify(dir, expected) {
  const problems = [];
  const text = await readFile(path.join(dir, MANIFEST));
  const actual = createHash("sha256").update(text).digest("hex");
  if (actual !== expected) {
    return [`${MANIFEST} has SHA-256 ${actual}, not the ${expected} the making job reported`];
  }
  const manifest = JSON.parse(text.toString("utf8")).files ?? {};
  const present = (await files(dir)).filter((file) => file !== MANIFEST);
  for (const file of present) {
    if (!(file in manifest)) problems.push(`${file} is not in the manifest`);
  }
  for (const [file, sha256] of Object.entries(manifest)) {
    const found = await sha256File(path.join(dir, file));
    if (found === undefined) problems.push(`${file} is missing`);
    else if (found !== sha256) problems.push(`${file} does not match the manifest`);
  }
  for (const artifact of (await readLock()).filter(({ usedBy }) => usedBy?.includes(CONSUMER))) {
    const file = `standards/${outputName(artifact)}`;
    if (manifest[file] !== artifact.sha256) {
      problems.push(`${file} is not ${artifact.name} as fhir/standards.lock.json pins it`);
    }
  }
  if (!(FIXTURE in manifest)) problems.push(`${FIXTURE} is missing`);
  return problems;
}

const [command, dir, expected] = process.argv.slice(2);
if (command === "seal" && dir !== undefined) {
  console.log(await seal(dir));
} else if (command === "verify" && dir !== undefined && /^[0-9a-f]{64}$/.test(expected ?? "")) {
  const problems = await verify(dir, expected);
  if (problems.length > 0) {
    for (const problem of problems) console.error(`deploy inputs: ${problem}`);
    process.exit(1);
  }
  console.log(`deploy inputs verified: manifest ${expected.slice(0, 16)}…`);
} else {
  console.error("Usage: deploy-inputs.mjs seal <dir> | verify <dir> <manifest sha256>");
  process.exit(2);
}
