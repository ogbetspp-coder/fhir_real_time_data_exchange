// Types for scripts/fhir/compile-map.mjs, which stays plain JavaScript so it runs with node alone.
// Kept beside it; the tests that import it are what type-check it.

export const MAP_SOURCE: string;
export const MAP_COMPILED: string;
export const MAP_ID: string;
export const MAP_URL: string;
export const DEFAULT_VALIDATOR_DIR: string;

export type PinnedValidator = {
  version: string;
  jar: string;
  packages: string[];
  flags: string[];
  jvm: string[];
  java: string;
};

export function pinnedValidator(validatorDir?: string): PinnedValidator;
export function compileMap(validator?: PinnedValidator): Record<string, unknown>;
export function serialise(map: unknown): string;
