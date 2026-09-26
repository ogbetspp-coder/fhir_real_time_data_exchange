// Types for scripts/ci/renderer-pins.mjs, which stays plain JavaScript so it runs with node alone.
// Kept beside it; the tests that import it are what type-check it.

export const RENDERER_DOCKERFILE: string;
export const FONTCONFIG: string;

export type RendererArtefact = { file: string; url: string; sha256: string };

export type RendererPins = {
  base: string;
  debianSnapshot: string;
  chromeVersion: string;
  googleFontsCommit: string;
  chrome: RendererArtefact;
  liberation: RendererArtefact;
  fonts: RendererArtefact[];
  artefacts: RendererArtefact[];
};

export function readRendererPins(dockerfile?: string): RendererPins;
