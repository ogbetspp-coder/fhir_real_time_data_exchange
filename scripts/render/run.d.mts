// Types for scripts/render/run.mjs, which stays plain JavaScript so it runs with node alone. Kept
// beside it; the tests that import it are what type-check it.

export const IMAGE: string;
export const HARDENING: readonly string[];
export const ISOLATION: readonly string[];
export const MOUNTS: readonly string[];
export function dockerArgs(root: string, script: string, args?: readonly string[]): string[];
