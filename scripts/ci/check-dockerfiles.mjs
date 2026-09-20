#!/usr/bin/env node

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

// Fails if any Dockerfile* at the repository root pulls image bytes from a reference that is
// not pinned by digest (`@sha256:<64 hex>`), or if two Dockerfiles that both start FROM a node
// image pin different digests. A tag can be rebuilt and move the runtime's Unicode database
// under the normalisation the fidelity check depends on (ADR 0003); a digest cannot.
//
// Three places pull image bytes and all three are scanned: `FROM <ref>`, `--from=<ref>` on COPY
// and ADD, and `from=<ref>` inside a `--mount=` flag on RUN. A reference that names a stage
// declared in the same file (`FROM ... AS build`, or a stage index such as `--from=0`) pulls no
// registry bytes and is skipped.
//
// The scan is textual. It does not resolve build arguments (`FROM ${BASE}`, `--from=$STAGE`),
// it does not contact a registry, and a digest it accepts is only as good as the registry
// content addressed by it.
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

// `FROM [--flag=...]... <image>[@sha256:<digest>] [AS <stage>]`.
const FROM_LINE = /^\s*FROM\s+((?:--\S+\s+)*)(\S+)(?:\s+AS\s+(\S+))?\s*$/i;
const DIGEST = /@sha256:[0-9a-f]{64}$/;
// A stage index, which names an earlier stage of the same file rather than an image.
const STAGE_INDEX = /^\d+$/;

// Continuation lines are joined so a `--mount` or `--from` written after a trailing backslash
// is scanned; the reported line number is the first physical line of the joined instruction.
// Comment lines are dropped first, as the builder drops them.
function instructions(text) {
  const joined = [];
  let open = null;
  text.split(/\r?\n/).forEach((line, index) => {
    if (/^\s*#/.test(line)) return;
    const continued = /\\\s*$/.test(line);
    const body = continued ? line.replace(/\\\s*$/, "") : line;
    if (open === null) open = { line: index + 1, text: body };
    else open.text += ` ${body.trim()}`;
    if (!continued) {
      joined.push(open);
      open = null;
    }
  });
  if (open !== null) joined.push(open);
  return joined;
}

function unquote(value) {
  return value.replace(/^["']/, "").replace(/["']$/, "");
}

// Every reference an instruction pulls image bytes from, with the keyword that pulled it.
function imageReferences(text) {
  const found = [];

  const from = FROM_LINE.exec(text);
  if (from !== null) found.push({ keyword: "FROM", ref: unquote(from[2]) });

  if (/^\s*(?:COPY|ADD)\b/i.test(text)) {
    for (const match of text.matchAll(/--from=(\S+)/gi)) {
      found.push({ keyword: "COPY --from", ref: unquote(match[1]) });
    }
  }

  if (/^\s*RUN\b/i.test(text)) {
    for (const mount of text.matchAll(/--mount=(\S+)/gi)) {
      for (const part of mount[1].split(",")) {
        const value = /^from=(.+)$/i.exec(part);
        if (value !== null) found.push({ keyword: "RUN --mount from", ref: unquote(value[1]) });
      }
    }
  }

  return found;
}

const failures = [];
const nodeDigests = new Map();

for (const name of dockerfiles) {
  const lines = instructions(readFileSync(path.join(root, name), "utf8"));

  // Collected over the whole file first, so a reference is judged against every stage the file
  // declares rather than only the ones above it.
  const stages = new Set();
  for (const { text } of lines) {
    const stage = FROM_LINE.exec(text)?.[3];
    if (stage !== undefined) stages.add(stage.toLowerCase());
  }

  for (const { line, text } of lines) {
    for (const { keyword, ref } of imageReferences(text)) {
      if (stages.has(ref.toLowerCase()) || STAGE_INDEX.test(ref)) continue;
      if (!DIGEST.test(ref)) {
        failures.push(`${name}:${line}: ${keyword} ${ref} is not pinned by @sha256 digest`);
        continue;
      }
      if (keyword === "FROM" && ref.startsWith("node:")) {
        nodeDigests.set(name, ref.slice(ref.indexOf("@") + 1));
      }
    }
  }
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
  `Dockerfile image references are pinned by digest (${dockerfiles.length} files, ${nodeDigests.size} node images sharing one digest)`,
);
