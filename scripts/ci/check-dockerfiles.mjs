#!/usr/bin/env node

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { instructions } from "./dockerfile.mjs";

// Fails if any Dockerfile* at the repository root pulls an image by a reference not pinned by
// digest (`@sha256:<64 hex>`): `FROM <ref>`, `--from=<ref>` on COPY or ADD, or `from=<ref>` in a
// RUN `--mount`. A reference to a stage the file declares (`AS <name>`) or a stage index pulls no
// image and is skipped. Every step image of a Cloud Build configuration at the root
// (cloudbuild*.yaml) is held to the same rule, and every node image, Dockerfile or Cloud Build,
// must share one digest: a tag can be rebuilt and move the runtime's Unicode database under the
// normalisation the fidelity check depends on (ADR 0003); a digest cannot.
//
// The scan is textual and catches honest mistakes (writing `FROM node:22`). It does not resolve
// build arguments (`FROM ${BASE}`) or contact a registry; a disguised edit is for review (O3).
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

const FROM_LINE = /^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?\s*$/i;
const DIGEST = /@sha256:[0-9a-f]{64}$/;
const unquote = (value) => value.replace(/^["']|["']$/g, "");

// Every reference an instruction pulls an image by, with the keyword that pulled it.
function imageReferences(text) {
  const from = FROM_LINE.exec(text);
  if (from !== null) return [{ keyword: "FROM", ref: from[1] }];
  if (/^\s*(?:COPY|ADD)\b/i.test(text)) {
    return [...text.matchAll(/--from=(\S+)/gi)].map((m) => ({ keyword: "COPY --from", ref: m[1] }));
  }
  if (/^\s*RUN\b/i.test(text)) {
    return [...text.matchAll(/--mount=\S*?\bfrom=([^,\s]+)/gi)].map((m) => ({
      keyword: "RUN --mount from",
      ref: m[1],
    }));
  }
  return [];
}

const failures = [];
const nodeDigests = new Map();
const pinned = (where, keyword, ref) => {
  if (!DIGEST.test(ref))
    failures.push(`${where}: ${keyword} ${ref} is not pinned by @sha256 digest`);
  else if (ref.startsWith("node:")) nodeDigests.set(where, ref.slice(ref.indexOf("@") + 1));
};

for (const name of dockerfiles) {
  const lines = instructions(readFileSync(path.join(root, name), "utf8"));
  const stages = new Set(lines.map(({ text }) => FROM_LINE.exec(text)?.[2]?.toLowerCase()));
  for (const { line, text } of lines) {
    for (const { keyword, ref } of imageReferences(text)) {
      const image = unquote(ref);
      if (!stages.has(image.toLowerCase()) && !/^\d+$/.test(image)) {
        pinned(`${name}:${line}`, keyword, image);
      }
    }
  }
}

// Every `name:` key of a Cloud Build configuration is a step image the build runs with its
// identity's credentials. A substitution such as `${_BUILDER}` is not a digest, so it fails.
const buildConfigs = readdirSync(root)
  .filter((name) => /^cloudbuild.*\.ya?ml$/.test(name))
  .sort();
let builderSteps = 0;
for (const name of buildConfigs) {
  readFileSync(path.join(root, name), "utf8")
    .split(/\r?\n/)
    .forEach((line, index) => {
      const step = /^\s*(?:-\s+)?name:\s*(\S+)\s*(?:#.*)?$/.exec(line);
      if (step === null) return;
      builderSteps += 1;
      pinned(`${name}:${index + 1}`, "step name", unquote(step[1]));
    });
}

if (new Set(nodeDigests.values()).size > 1) {
  failures.push(
    `Node images (by FROM, COPY --from, RUN --mount or a Cloud Build step) pin different digests:\n${[
      ...nodeDigests,
    ]
      .map(([where, digest]) => `  ${where}: ${digest}`)
      .join("\n")}`,
  );
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log(
  `Dockerfile image references are pinned by digest (${dockerfiles.length} files, ${nodeDigests.size} node images sharing one digest); ${builderSteps} Cloud Build steps in ${buildConfigs.length} configurations pinned by digest`,
);
