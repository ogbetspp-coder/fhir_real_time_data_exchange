// Types for scripts/render/word-drawing.mjs, which stays plain JavaScript so it runs with node
// alone. Kept beside it; the tests that import it are what type-check it.

export const IMAGE: string;
export const MOUNTS: readonly string[];
export function dockerArgs(root: string, args: readonly string[]): string[];
