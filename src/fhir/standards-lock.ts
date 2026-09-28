import { readFileSync } from "node:fs";
import path from "node:path";

import { z } from "zod";

import { PackageRef, Sha256Hex } from "../contracts/common.js";

// The FHIR packages a run is validated against, read from fhir/standards.lock.json: the one list
// the FHIR store's profile import (scripts/gcp/bootstrap.sh) and the official validator sidecar
// (Dockerfile.validator, held equal to it by test/ci/validator-pins.test.ts) are pinned from. The
// worker image ships the lock, and the run manifest names each package by id#version and SHA-256
// from it, so a signed manifest names the standard that ran rather than a literal (audit B07,
// S-4). Anything the reader does not recognise fails the run rather than being half-read.

export const STANDARDS_LOCK = "fhir/standards.lock.json";

// Unknown keys are allowed on an artifact (a consumer list, a status); the ones read are checked.
const LockSchema = z.strictObject({
  schemaVersion: z.literal("1.0.0"),
  artifacts: z
    .array(
      z.looseObject({
        name: z.string().min(1),
        package: PackageRef.optional(),
        url: z.url({ protocol: /^https$/ }),
        sha256: Sha256Hex,
      }),
    )
    .min(1),
});

export type PinnedPackage = { package: string; sha256: string };

const cache = new Map<string, PinnedPackage[]>();

// Every artifact of the lock that is a FHIR package, in the lock's order. Cached per path: the
// lock is part of the image and does not change while the process runs.
export function pinnedPackages(lockPath = path.resolve(STANDARDS_LOCK)): PinnedPackage[] {
  const cached = cache.get(lockPath);
  if (cached !== undefined) return cached;
  const lock = LockSchema.parse(JSON.parse(readFileSync(lockPath, "utf8")));
  const packages = lock.artifacts.flatMap((artifact) =>
    artifact.package === undefined ? [] : [{ package: artifact.package, sha256: artifact.sha256 }],
  );
  const seen = new Set<string>();
  for (const { package: ref } of packages) {
    const id = ref.slice(0, ref.indexOf("#"));
    if (seen.has(id)) throw new Error(`${STANDARDS_LOCK} pins ${id} more than once`);
    seen.add(id);
  }
  if (packages.length === 0) throw new Error(`${STANDARDS_LOCK} pins no FHIR package`);
  cache.set(lockPath, packages);
  return packages;
}

// The one pinned package with this id, as `id#version`.
export function pinnedPackage(packages: PinnedPackage[], id: string): string {
  const found = packages.filter(({ package: ref }) => ref.startsWith(`${id}#`));
  if (found.length !== 1 || found[0] === undefined) {
    throw new Error(`${STANDARDS_LOCK} must pin exactly one ${id} package`);
  }
  return found[0].package;
}
