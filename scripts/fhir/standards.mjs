// The pinned standards (fhir/standards.lock.json): reading the lock, the file name each artifact is
// written under, and SHA-256 of a file. Node built-ins only, so the deploy job, which installs no
// packages (audit B08, D-1), can use it.

import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";

export const LOCK = "fhir/standards.lock.json";

export async function readLock(file = LOCK) {
  const lock = JSON.parse(await readFile(path.resolve(file), "utf8"));
  if (!Array.isArray(lock.artifacts) || lock.artifacts.length === 0) {
    throw new Error(`${file} lists no artifacts`);
  }
  const names = new Set();
  for (const artifact of lock.artifacts) {
    if (typeof artifact.name !== "string" || !/^[0-9a-f]{64}$/.test(artifact.sha256 ?? "")) {
      throw new Error(`${file}: an artifact has no name or no SHA-256`);
    }
    if (names.has(artifact.name)) throw new Error(`${file}: ${artifact.name} is listed twice`);
    names.add(artifact.name);
    if (artifact.usedBy !== undefined && !Array.isArray(artifact.usedBy)) {
      throw new Error(`${file}: ${artifact.name}'s usedBy is not a list`);
    }
  }
  return lock.artifacts;
}

// The file name an artifact is written under: its name, version and package made file-safe, and
// the extension of what it is. One name per pin, so a reader names the file exactly instead of
// globbing a directory where an older pin's file may remain.
export function outputName(artifact) {
  const fileName = new URL(artifact.url).pathname.split("/").at(-1);
  if (!fileName) throw new Error(`Cannot derive a file name for ${artifact.url}`);
  const versionSuffix = artifact.version ? `-${artifact.version}` : "";
  const packageSuffix = artifact.package
    ? `-${artifact.package.replaceAll(/[^a-zA-Z0-9.-]/g, "_")}`
    : "";
  const knownExtension = [".jar", ".zip", ".json", ".tgz"].find((candidate) =>
    fileName.endsWith(candidate),
  );
  const extension = knownExtension ?? (artifact.package ? ".tgz" : ".bin");
  return `${artifact.name.replaceAll(/[^a-zA-Z0-9.-]/g, "_")}${versionSuffix}${packageSuffix}${extension}`;
}

// SHA-256 of a file, streamed; undefined when there is no such file.
export async function sha256File(file) {
  const hash = createHash("sha256");
  try {
    for await (const chunk of createReadStream(file)) hash.update(chunk);
  } catch (error) {
    if (error?.code === "ENOENT") return undefined;
    throw error;
  }
  return hash.digest("hex");
}
