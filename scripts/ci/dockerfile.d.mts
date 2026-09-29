// Types for scripts/ci/dockerfile.mjs, which stays plain JavaScript so it runs with node alone.
// Kept beside it; the tests that import it are what type-check it.

export const SHA256_HEX: RegExp;
export type Instruction = { line: number; text: string };
export function instructions(text: string): Instruction[];
export function args(lines: readonly string[], name: string): Map<string, string>;
export type Download = { file: string; url: string; sha256: string };
export function checksummedDownloads(
  lines: readonly string[],
  declared: Map<string, string>,
  name: string,
): Download[];
