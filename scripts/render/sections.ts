import { enumerateSections, pinnedLabels, type Section } from "../../src/render/sections.js";
import { T_CASES } from "../../test/fixtures/authority/t-cases.js";
import { MODEL_CASES } from "../../test/fixtures/render/model-cases.js";

// The renderer image and the sections its checks draw, one definition for every script under
// scripts/render (docs/design/authority-import-renderer.md, R2, R3 and R6).

export const EXECUTABLE =
  process.env.RENDERER_CHROME ??
  "/opt/renderer/chrome-headless-shell-linux64/chrome-headless-shell";
export const FONTS = process.env.RENDERER_FONTS ?? "/opt/renderer/fonts";
// Chrome's sandbox is off only inside the render container, which has no network, no credentials
// and a read-only workspace (R1, R6); its run (scripts/render/run.mjs) sets RENDERER_NO_SANDBOX=1,
// and the smoke check asserts that isolation before anything is drawn.
export const NO_SANDBOX = process.env.RENDERER_NO_SANDBOX === "1";
export const LABELS = "labels/ema-epi";

// Every accepted T case, every model case and every section of every pinned label, or the check
// fails: nothing broken is skipped (src/render/sections.ts).
export function sections(): Section[] {
  const { sections: found, broken } = enumerateSections({
    tCases: T_CASES.filter((testCase) => "div" in testCase.expected),
    modelCases: MODEL_CASES,
    labels: pinnedLabels(LABELS),
  });
  if (broken.length > 0) {
    console.error(broken.join("\n"));
    process.exit(1);
  }
  return found;
}
