import { parsePixels, tolerance } from "./compare-style.js";
import type { Font, Metrics } from "./font.js";
import { boundFace } from "./fonts.js";
import type { ChromeHeights, ChromePage, ChromeRun } from "./measure.js";

// R2's page and R3's character boxes (docs/design/authority-import-renderer.md): a section whose
// XML does not parse, whose div has padding or a border of its own, or whose div's content box is
// not the width drawn, is a refusal of ours; so is a character whose box is not within a device
// pixel of its bound face's ascent and descent at its size and ratio.

export type PageRefusal = "parsererror" | "div-box" | "div-width" | "char-height";

export type PageCheck = { refusal: PageRefusal; detail: string };

export function checkPage(page: ChromePage, width: number, mode: "html" | "xml"): PageCheck[] {
  const found: PageCheck[] = [];
  if (mode === "xml" && page.parserError) found.push({ refusal: "parsererror", detail: "XML" });
  const zero = (values: string): boolean =>
    values.split(" ").every((value) => parsePixels(value) === 0);
  if (!zero(page.divPadding) || !zero(page.divBorder)) {
    found.push({
      refusal: "div-box",
      detail: `padding ${page.divPadding}, border ${page.divBorder}`,
    });
  } else if (Math.abs(page.divWidth - width) > tolerance(width)) {
    found.push({ refusal: "div-width", detail: `${page.divWidth} px, not ${width}` });
  }
  return found;
}

// R3's character box, as a measured tolerance, not an exact model (the second review of 3c-B2a):
// the face's hhea ascent and descent at the computed size and ratio, each rounded at the device
// size, after Chrome's single-precision size quantised down to 1/64 of a device pixel as
// FreeType does. That fits every size in points first measured (448 of 448) but misses by one
// device pixel at some sizes in em, %, `smaller` and at fractional ratios (about 1 to 3 % of a
// sweep), by a path not yet found. So a box is held to within one device pixel of it, plus the
// 1/64 CSS pixel Chrome reports heights in: a box of the bound face's metrics passes, and a box
// another face would draw or a stretched box is refused where it differs by more than that (at
// 5 to 7 pt the pinned faces' boxes, and stretches of several per cent, are within it; a
// fallback is refused as `font-face`, and T refuses what could stretch a box). The exact box
// model is 3c-C's, before R4's baseline, which needs the exact ascent.
export function boxHeight(metrics: Metrics, pixels: number, ratio: number): number {
  const size = Math.floor(Math.fround(Math.fround(pixels) * ratio) * 64) / 64;
  const ascent = Math.round((metrics.hheaAscender / metrics.unitsPerEm) * size);
  const descent = Math.round((-metrics.hheaDescender / metrics.unitsPerEm) * size);
  return (ascent + descent) / ratio;
}

// Whether a reported box height fits the formula's box within the tolerance, in device pixels.
export function boxFits(height: number, box: number, ratio: number): boolean {
  return Math.abs(height * ratio - box * ratio) <= 1 + ratio / 64 + 1e-9;
}

// Every character's box against R3's, from its bound face's metrics.
export function checkHeights(
  runs: readonly ChromeRun[],
  heights: ChromeHeights,
  faces: ReadonlyMap<string, Font>,
  ratio: number,
): PageCheck[] {
  const expected = new Map<number, number>();
  for (const run of runs) {
    if (expected.has(run.element)) continue;
    const face = faces.get(boundFace(run.family, run.weight, run.style) ?? "");
    const pixels = parsePixels(run.size);
    if (face !== undefined && pixels !== undefined) {
      expected.set(run.element, boxHeight(face.metrics, pixels, ratio));
    }
  }
  const found: PageCheck[] = [];
  for (const { element, heights: boxes } of heights) {
    const box = expected.get(element);
    if (box === undefined) {
      found.push({ refusal: "char-height", detail: `element ${element} has no bound face` });
      continue;
    }
    const wrong = boxes.find((height) => !boxFits(height, box, ratio));
    if (wrong !== undefined) {
      found.push({
        refusal: "char-height",
        detail: `element ${element}: a box ${wrong} px high, not within a device pixel of ${box}`,
      });
    }
  }
  return found;
}
