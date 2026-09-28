#!/usr/bin/env node

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

// Fails if any Dockerfile* at the repository root pulls image bytes from a reference that is
// not pinned by digest (`@sha256:<64 hex>`), or if two Dockerfiles that both start FROM a node
// image pin different digests. A tag can be rebuilt and move the runtime's Unicode database
// under the normalisation the fidelity check depends on (ADR 0003); a digest cannot.
//
// Four places pull image bytes and all four are scanned: `FROM <ref>`, `--from=<ref>` on COPY
// and ADD, `from=<ref>` inside a `--mount=` flag on RUN, and the `syntax` parser directive. The
// directive (`# syntax=<ref>`) names the frontend image BuildKit pulls and hands the whole file
// to before any FROM is read, so an unpinned one is a build program that can move. A reference
// that names a stage declared in the same file (`FROM ... AS build`, or a stage index such as
// `--from=0`) pulls no registry bytes and is skipped. One name is excluded from that skip: a
// `FROM`'s own `AS` name, which the instruction declares rather than refers to, so
// `FROM busybox AS busybox` is judged as the image reference `busybox` and not as a stage.
//
// The scan is textual. It does not resolve build arguments (`FROM ${BASE}`, `--from=$STAGE`),
// it does not contact a registry, and a digest it accepts is only as good as the registry
// content addressed by it.
//
// Cloud Build configurations at the root (cloudbuild*.yaml) are held to the same rule: every
// step's `name:` image must be pinned by digest, and a node builder shares the one node digest.
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
// Joined as the builder joins them (moby's parser, as scripts/ci/validator-pins.mjs does): the
// backslash removed and the next line appended with no space added, comment and blank lines
// dropped, inside a continuation too (audit B07 follow-up, review L3-b).
function instructions(text) {
  const joined = [];
  let open = null;
  text.split(/\r?\n/).forEach((line, index) => {
    if (/^\s*#/.test(line) || /^\s*$/.test(line)) return;
    const continued = /\\[ \t]*$/.test(line);
    const body = continued ? line.replace(/\\[ \t]*$/, "") : line;
    if (open === null) open = { line: index + 1, text: body };
    else open.text += body;
    if (!continued) {
      joined.push(open);
      open = null;
    }
  });
  if (open !== null) joined.push(open);
  return joined;
}

// The frontend images a file names through the `syntax` parser directive, with their lines.
// BuildKit honours the directive only in the leading run of directive lines, in the `#` form or
// the `//` one, or as a `syntax` key when the whole file is JSON. Every line of either form is
// read here wherever it stands, which fails closed: a directive BuildKit would honour is never
// missed, at the cost of rewording a later comment that merely looks like one.
const SYNTAX_DIRECTIVE = /^\uFEFF?\s*(?:#|\/\/)\s*syntax\s*=\s*(.*?)\s*$/i;

function syntaxDirectives(text) {
  const found = [];
  text.split(/\r?\n/).forEach((line, index) => {
    const directive = SYNTAX_DIRECTIVE.exec(line);
    if (directive !== null) found.push({ line: index + 1, ref: unquote(directive[1]) });
  });
  let json;
  try {
    json = JSON.parse(text.replace(/^\uFEFF/, ""));
  } catch {
    json = undefined;
  }
  if (json !== null && typeof json === "object" && !Array.isArray(json)) {
    for (const [key, value] of Object.entries(json)) {
      if (key.toLowerCase() === "syntax") found.push({ line: 1, ref: String(value) });
    }
  }
  return found;
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
  const source = readFileSync(path.join(root, name), "utf8");
  for (const { line, ref } of syntaxDirectives(source)) {
    if (!DIGEST.test(ref)) {
      failures.push(`${name}:${line}: # syntax ${ref} is not pinned by @sha256 digest`);
    }
  }
  const lines = instructions(source);

  // Stages are collected as the file is walked, so a reference resolves to a stage only if that
  // stage was declared ABOVE it. That is Docker's own rule — `COPY --from` and `RUN --mount`
  // cannot name a later stage — and it is what keeps the check from being weakened by a name:
  // judged against the whole file, `FROM alpine AS busybox` followed by `FROM busybox AS alpine`
  // shields both of its unpinned images behind the other's stage name.
  const stages = new Set();

  for (const { line, text } of lines) {
    for (const { keyword, ref } of imageReferences(text)) {
      const referenced = ref.toLowerCase();
      if (stages.has(referenced) || STAGE_INDEX.test(ref)) continue;
      if (!DIGEST.test(ref)) {
        failures.push(`${name}:${line}: ${keyword} ${ref} is not pinned by @sha256 digest`);
        continue;
      }
      if (keyword === "FROM" && ref.startsWith("node:")) {
        // Keyed by line, not by file: two node stages in one Dockerfile pinned to different
        // digests is exactly the drift this check exists to catch, and keying by file would
        // let the later stage overwrite the earlier one and hide it.
        nodeDigests.set(`${name}:${line}`, ref.slice(ref.indexOf("@") + 1));
      }
    }

    // Added after the instruction is judged, so `FROM busybox AS busybox` still pulls the
    // image `busybox` and is checked as one.
    const declared = FROM_LINE.exec(text)?.[3];
    if (declared !== undefined) stages.add(declared.toLowerCase());
  }
}

// Cloud Build configurations at the root (cloudbuild*.yaml). Every step's `name:` is an image
// the build runs with the build identity's credentials, so it is held to the same rule as a
// FROM. The scan is textual: any `name:` key (with or without the list dash) whose value is not
// a quoted or unquoted digest reference fails, including a substitution such as `${_BUILDER}`.
// It fails closed: a `name:` that is not an image (a step volume's) is judged too, so this
// check has to learn that shape before a configuration can use it.
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
      const ref = unquote(step[1]);
      if (!DIGEST.test(ref)) {
        failures.push(`${name}:${index + 1}: step name ${ref} is not pinned by @sha256 digest`);
      } else if (ref.startsWith("node:")) {
        nodeDigests.set(`${name}:${index + 1}`, ref.slice(ref.indexOf("@") + 1));
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
  `Dockerfile image references are pinned by digest (${dockerfiles.length} files, ${nodeDigests.size} node images sharing one digest); ${builderSteps} Cloud Build steps in ${buildConfigs.length} configurations pinned by digest`,
);
