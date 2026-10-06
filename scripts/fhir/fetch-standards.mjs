#!/usr/bin/env node
// Downloads the pinned standards in fhir/standards.lock.json, each checked against its SHA-256.
//
//   node scripts/fhir/fetch-standards.mjs [--used-by <consumer>] [--dest <dir>] [--lock <file>]
//   node scripts/fhir/fetch-standards.mjs --path "<artifact name>"
//
// --used-by keeps the artifacts whose `usedBy` names that consumer: the deploy's bootstrap uses
// five of the twelve, about 9 MB of 313, and until audit B08 (D-6) fetched all of them on every
// deploy. A file already at its path with the pinned hash is kept, not fetched again. A download
// streams to a temporary file beside its destination, hashed as it arrives, and is renamed into
// place only when the hash matches, so a failed or mismatched download leaves nothing behind; it
// is abandoned after FETCH_TIMEOUT_SECONDS (default 600) rather than hanging a deploy.
//
// --path prints the file name an artifact is written under (scripts/fhir/standards.mjs), so a
// reader names the file exactly rather than globbing a directory that may still hold an older
// pin's file. Node built-ins only.

import { createHash } from "node:crypto";
import { createWriteStream } from "node:fs";
import { mkdir, rename, rm } from "node:fs/promises";
import path from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";

import { LOCK, outputName, readLock, sha256File } from "./standards.mjs";

function options(argv) {
  const found = { dest: "fhir/vendor", lock: LOCK, usedBy: undefined, path: undefined };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    const value = argv[index + 1];
    const key = { "--dest": "dest", "--lock": "lock", "--used-by": "usedBy", "--path": "path" }[
      flag
    ];
    if (key === undefined || value === undefined) {
      throw new Error(
        "Usage: fetch-standards.mjs [--used-by <consumer>] [--dest <dir>] [--lock <file>] | --path <artifact name>",
      );
    }
    found[key] = value;
    index += 1;
  }
  return found;
}

async function download(artifact, output, timeoutMs) {
  const partial = `${output}.partial-${process.pid}`;
  const hash = createHash("sha256");
  try {
    let response;
    try {
      response = await fetch(artifact.url, {
        signal: globalThis.AbortSignal.timeout(timeoutMs),
      });
      if (!response.ok || response.body === null) {
        throw new Error(`Failed ${artifact.url}: ${response.status} ${response.statusText}`);
      }
      await pipeline(
        Readable.fromWeb(response.body),
        new Transform({
          transform(chunk, _encoding, callback) {
            hash.update(chunk);
            callback(null, chunk);
          },
        }),
        createWriteStream(partial),
      );
    } catch (error) {
      if (error?.name === "TimeoutError" || error?.name === "AbortError") {
        throw new Error(`${artifact.name}: no complete download within ${timeoutMs / 1000}s`, {
          cause: error,
        });
      }
      throw error;
    }
    const actual = hash.digest("hex");
    if (actual !== artifact.sha256) {
      throw new Error(
        `Checksum mismatch for ${artifact.name}: expected ${artifact.sha256}, received ${actual}`,
      );
    }
    await rename(partial, output);
    return actual;
  } finally {
    await rm(partial, { force: true });
  }
}

const args = options(process.argv.slice(2));
const artifacts = await readLock(args.lock);

if (args.path !== undefined) {
  const artifact = artifacts.find(({ name }) => name === args.path);
  if (artifact === undefined) throw new Error(`${args.lock} has no artifact named ${args.path}`);
  console.log(outputName(artifact));
} else {
  const wanted =
    args.usedBy === undefined
      ? artifacts
      : artifacts.filter(({ usedBy }) => usedBy?.includes(args.usedBy));
  if (wanted.length === 0) throw new Error(`${args.lock}: nothing is used by ${args.usedBy}`);
  const timeoutMs = Number(process.env.FETCH_TIMEOUT_SECONDS ?? "600") * 1000;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new Error("FETCH_TIMEOUT_SECONDS must be a positive number");
  }
  const destination = path.resolve(args.dest);
  await mkdir(destination, { recursive: true });
  for (const artifact of wanted) {
    if (artifact.url === undefined) {
      console.log(`${artifact.name}: in the repository at ${artifact.path}, not fetched`);
      continue;
    }
    const output = path.join(destination, outputName(artifact));
    const relative = path.relative(process.cwd(), output);
    if ((await sha256File(output)) === artifact.sha256) {
      console.log(`${artifact.name}: ${artifact.sha256} already at ${relative}`);
      continue;
    }
    const actual = await download(artifact, output, timeoutMs);
    console.log(`${artifact.name}: ${actual} -> ${relative}`);
  }
}
