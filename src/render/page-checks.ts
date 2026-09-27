import { parsePixels, tolerance } from "./compare-style.js";
import type { Font, Metrics } from "./font.js";
import { boundFace } from "./fonts.js";
import type { ChromeHeights, ChromePage, ChromeRun } from "./measure.js";

// R2's page and R3's character boxes (docs/design/authority-import-renderer.md): a section whose
// XML does not parse, whose div has padding or a border of its own, or whose div's content box is
// not the width drawn, is a refusal of ours; so is a character whose box is not its bound face's
// ascent and descent at its size and ratio, as Chrome rounds them.

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

// R3's character box (the first code review of 3c-B2a, 448 of 448 measured boxes): Chrome holds
// the font size in single precision, scales it by the device pixel ratio, and FreeType quantises
// it down to 1/64 of a device pixel; the box is the face's hhea ascent and descent, each rounded
// at that size, in CSS pixels.
export function boxHeight(metrics: Metrics, pixels: number, ratio: number): number {
  const size = Math.floor(Math.fround(Math.fround(pixels) * ratio) * 64) / 64;
  const ascent = Math.round((metrics.hheaAscender / metrics.unitsPerEm) * size);
  const descent = Math.round((-metrics.hheaDescender / metrics.unitsPerEm) * size);
  return (ascent + descent) / ratio;
}

// The boxes a computed size allows: Chrome serialises the size to six significant digits, so the
// exact size is anywhere within one unit of the last of them, and each box the formula gives
// across that interval is allowed (one, or two where a rounding falls inside it).
export function allowedBoxes(metrics: Metrics, computedSize: string, ratio: number): number[] {
  const pixels = parsePixels(computedSize);
  if (pixels === undefined) return [];
  const spread = tolerance(pixels);
  const found = new Set<number>();
  const steps = 64;
  for (let step = 0; step <= steps; step += 1) {
    found.add(boxHeight(metrics, pixels - spread + (2 * spread * step) / steps, ratio));
  }
  return [...found];
}

// Every character's box against R3's, from its bound face's metrics.
export function checkHeights(
  runs: readonly ChromeRun[],
  heights: ChromeHeights,
  faces: ReadonlyMap<string, Font>,
  ratio: number,
): PageCheck[] {
  const allowed = new Map<number, number[]>();
  for (const run of runs) {
    if (allowed.has(run.element)) continue;
    const face = faces.get(boundFace(run.family, run.weight, run.style) ?? "");
    if (face !== undefined) allowed.set(run.element, allowedBoxes(face.metrics, run.size, ratio));
  }
  const found: PageCheck[] = [];
  for (const { element, heights: boxes } of heights) {
    const expected = allowed.get(element);
    if (expected === undefined || expected.length === 0) {
      found.push({ refusal: "char-height", detail: `element ${element} has no bound face` });
      continue;
    }
    const wrong = boxes.find((height) => !expected.some((box) => Math.abs(height - box) <= 1e-3));
    if (wrong !== undefined) {
      found.push({
        refusal: "char-height",
        detail: `element ${element}: a box ${wrong} px high, not ${expected.join(" or ")}`,
      });
    }
  }
  return found;
}
