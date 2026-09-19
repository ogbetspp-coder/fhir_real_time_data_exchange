#!/usr/bin/env node

import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const lockPath = path.resolve("fhir/standards.lock.json");
const destination = path.resolve("fhir/vendor");
const lock = JSON.parse(await readFile(lockPath, "utf8"));
await mkdir(destination, { recursive: true });

for (const artifact of lock.artifacts) {
  const fileName = new URL(artifact.url).pathname.split("/").at(-1);
  if (!fileName) throw new Error(`Cannot derive filename for ${artifact.url}`);
  const versionSuffix = artifact.version ? `-${artifact.version}` : "";
  const packageSuffix = artifact.package
    ? `-${artifact.package.replaceAll(/[^a-zA-Z0-9.-]/g, "_")}`
    : "";
  const knownExtension = [".jar", ".zip", ".json", ".tgz"].find((candidate) =>
    fileName.endsWith(candidate),
  );
  const extension = knownExtension ?? (artifact.package ? ".tgz" : ".bin");
  const output = path.join(
    destination,
    `${artifact.name.replaceAll(/[^a-zA-Z0-9.-]/g, "_")}${versionSuffix}${packageSuffix}${extension}`,
  );

  const response = await fetch(artifact.url);
  if (!response.ok) {
    throw new Error(`Failed ${artifact.url}: ${response.status} ${response.statusText}`);
  }
  const content = Buffer.from(await response.arrayBuffer());
  const actual = createHash("sha256").update(content).digest("hex");
  if (actual !== artifact.sha256) {
    throw new Error(
      `Checksum mismatch for ${artifact.name}: expected ${artifact.sha256}, received ${actual}`,
    );
  }
  await writeFile(output, content, { flag: "w" });
  console.log(`${artifact.name}: ${actual} -> ${path.relative(process.cwd(), output)}`);
}
