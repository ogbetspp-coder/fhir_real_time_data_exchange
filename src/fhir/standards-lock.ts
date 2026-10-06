import { readFileSync } from "node:fs";
import path from "node:path";

import { z } from "zod";

import { PackageRef, Sha256Hex } from "../contracts/common.js";

// The FHIR packages a run is validated against, read from the two locks the worker image ships
// (audit B07, S-4, and its review round 1, Low-1):
//
// - fhir/standards.lock.json: the packages the official validator sidecar loads with -ig
//   (Dockerfile.validator, held equal to it by test/ci/validator-pins.test.ts): the four the FHIR
//   store's profile import (scripts/gcp/bootstrap.sh) also imports, and the repository's own
//   (dev.khs.fhir.epi), which it does not;
// - fhir/validator-packages.lock: the packages the validator resolves on its own and the sidecar
//   installs into its package cache.
//
// Together they are every package the validator loads (thirteen on 2026-10-05, the count its
// Package Summary reports), and the run manifest names each by id#version with the SHA-256 its lock
// records, so a signed manifest names the standards that ran rather than a literal. Anything the
// reader does not recognise fails the run rather than being half-read.

export const STANDARDS_LOCK = "fhir/standards.lock.json";
export const VALIDATOR_PACKAGE_LOCK = "fhir/validator-packages.lock";

// Unknown keys are allowed on an artifact (a consumer list, a status); the ones read are checked.
const LockSchema = z.strictObject({
  schemaVersion: z.literal("1.0.0"),
  artifacts: z
    .array(
      z
        .looseObject({
          name: z.string().min(1),
          package: PackageRef.optional(),
          // Where it is downloaded from, or, for the repository's own package, where it is
          // committed: one or the other.
          url: z.url({ protocol: /^https$/ }).optional(),
          path: z.string().min(1).optional(),
          sha256: Sha256Hex,
        })
        .refine(({ url, path: file }) => (url === undefined) !== (file === undefined), {
          message: "an artifact has a url or a path, not both",
        }),
    )
    .min(1),
});

export type PinnedPackage = { package: string; sha256: string };

// fhir/validator-packages.lock: `<id> <version> <sha256>` per line, `#` comments and blank lines
// ignored (the format scripts/ci/validator-pins.mjs reads for the image and the CI gate).
function validatorLockPackages(file: string): PinnedPackage[] {
  const packages: PinnedPackage[] = [];
  readFileSync(file, "utf8")
    .split(/\r?\n/)
    .forEach((raw, index) => {
      const line = raw.trim();
      if (line === "" || line.startsWith("#")) return;
      const [id, version, sha256, ...rest] = line.split(/\s+/);
      const ref = `${id}#${version}`;
      if (
        rest.length > 0 ||
        !PackageRef.safeParse(ref).success ||
        !Sha256Hex.safeParse(sha256).success
      ) {
        throw new Error(`${VALIDATOR_PACKAGE_LOCK}:${index + 1} is not <id> <version> <sha256>`);
      }
      packages.push({ package: ref, sha256: sha256 ?? "" });
    });
  if (packages.length === 0) throw new Error(`${VALIDATOR_PACKAGE_LOCK} pins no package`);
  return packages;
}

const cache = new Map<string, PinnedPackage[]>();

// Every FHIR package of the two locks: the standards lock's in its order, then the validator
// lock's. A package in both is named once when both record the same SHA-256, and refused when
// they differ; a package is keyed by id#version, so one id at several versions is several
// packages (hl7.terminology.r5 is at four). Cached per pair of paths: the locks are part of the
// image and do not change while the process runs.
export function pinnedPackages(
  lockPath = path.resolve(STANDARDS_LOCK),
  validatorLockPath = path.resolve(VALIDATOR_PACKAGE_LOCK),
): PinnedPackage[] {
  const key = `${lockPath}\0${validatorLockPath}`;
  const cached = cache.get(key);
  if (cached !== undefined) return cached;
  const lock = LockSchema.parse(JSON.parse(readFileSync(lockPath, "utf8")));
  const standards = lock.artifacts.flatMap((artifact) =>
    artifact.package === undefined ? [] : [{ package: artifact.package, sha256: artifact.sha256 }],
  );
  if (standards.length === 0) throw new Error(`${STANDARDS_LOCK} pins no FHIR package`);
  const byRef = new Map<string, PinnedPackage>();
  for (const [source, list] of [
    [STANDARDS_LOCK, standards],
    [VALIDATOR_PACKAGE_LOCK, validatorLockPackages(validatorLockPath)],
  ] as const) {
    const seen = new Set<string>();
    for (const pinned of list) {
      if (seen.has(pinned.package)) {
        throw new Error(`${source} pins ${pinned.package} more than once`);
      }
      seen.add(pinned.package);
      const earlier = byRef.get(pinned.package);
      if (earlier === undefined) byRef.set(pinned.package, pinned);
      else if (earlier.sha256 !== pinned.sha256) {
        throw new Error(`${pinned.package} has two different SHA-256s in the locks`);
      }
    }
  }
  const packages = [...byRef.values()];
  cache.set(key, packages);
  return packages;
}

// The one pinned package with this id, as `id#version`.
export function pinnedPackage(packages: PinnedPackage[], id: string): string {
  const found = packages.filter(({ package: ref }) => ref.startsWith(`${id}#`));
  if (found.length !== 1 || found[0] === undefined) {
    throw new Error(`the locks must pin exactly one ${id} package`);
  }
  return found[0].package;
}
