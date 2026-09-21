// Types for scripts/ci/validator-pins.mjs, which stays plain JavaScript so the CI gate runs with
// node alone. Kept beside it; the tests that import it are what type-check it.

export const PACKAGE_LOCK: string;
export const SHA256_HEX: RegExp;
export const HERMETIC_PROPERTIES: readonly string[];

export type SidecarArtefact = { file: string; url: string; sha256: string };

export type SidecarPins = {
  version: string;
  artefacts: SidecarArtefact[];
  flags: string[];
  packages: string[];
  jvmProperties: string[];
};

export type LockedPackage = {
  id: string;
  version: string;
  sha256: string;
  key: string;
  url: string;
};

export function instructions(text: string): string[];
export function readSidecarPins(dockerfile: string): SidecarPins;
export function readPackageLock(file: string): LockedPackage[];
export function downloadAttempts(lines: readonly string[]): string[];
