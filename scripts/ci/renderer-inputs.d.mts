// Types for scripts/ci/renderer-inputs.mjs, which stays plain JavaScript so it runs with node
// alone. Kept beside it; the tests that import it are what type-check it.

export const NOT_INPUTS: readonly RegExp[];
export function rendererInputsChanged(files: readonly string[]): boolean;
