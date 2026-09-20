#!/usr/bin/env node

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

// Fails if any Dockerfile* at the repository root has a FROM line whose image is not pinned by
// digest (`@sha256:<64 hex>`), or if two Dockerfiles that both start FROM a node image pin
// different digests. A tag can be rebuilt and move the runtime's Unicode database under the
// normalisation the fidelity check depends on (ADR 0003); a digest cannot.
//
// usage: node scripts/ci/check-dockerfiles.mjs [<repository root>]

const root = path.resolve(process.argv[2] ?? ".");
const dockerfiles = readdirSync(root)
  .filter((name) => name === "Dockerfile" || name.startsWith("Dockerfile."))
  .sort();

if (dockerfiles.length === 0) {
  console.error(`No Dockerfile* found at ${root}`);
  process.exit(1);
}

// `FROM [--platform=...] <image>[@sha256:<digest>] [AS <stage>]`; a stage name used as a base
// (`FROM build`) has no registry image to pin and is skipped.
const FROM_LINE = /^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+\S+)?\s*$/i;
const DIGEST = /@sha256:[0-9a-f]{64}$/;

const failures = [];
const nodeDigests = new Map();

for (const name of dockerfiles) {
  const lines = readFileSync(path.join(root, name), "utf8").split(/\r?\n/);
  const stages = new Set();
  lines.forEach((line, index) => {
    const match = FROM_LINE.exec(line);
    if (match === null) return;
    const image = match[1];
    const stage = /\sAS\s+(\S+)\s*$/i.exec(line)?.[1];
    if (stages.has(image)) return;
    if (stage !== undefined) stages.add(stage);
    if (!DIGEST.test(image)) {
      failures.push(`${name}:${index + 1}: FROM ${image} is not pinned by @sha256 digest`);
      return;
    }
    if (image.startsWith("node:")) {
      const digest = image.slice(image.indexOf("@") + 1);
      nodeDigests.set(name, digest);
    }
  });
}

const distinct = new Set(nodeDigests.values());
if (distinct.size > 1) {
  failures.push(
    `Dockerfiles that start FROM node pin different digests:\n${[...nodeDigests]
      .map(([name, digest]) => `  ${name}: ${digest}`)
      .join("\n")}`,
  );
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log(
  `Dockerfile base images are pinned by digest (${dockerfiles.length} files, ${nodeDigests.size} node images sharing one digest)`,
);
