import { parsePixels, tolerance } from "./compare-style.js";
import type { ChromeHeights, ChromePage, ChromeRun } from "./measure.js";

// R2's page and R3's character boxes (docs/design/authority-import-renderer.md): a section whose
// XML does not parse, whose div has padding or a border of its own, or whose div's content box is
// not the width drawn, is a refusal of ours; so is a character whose box is not the box of its
// face at its size and ratio.

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

// A run's face and size, the key of its calibration.
export function styleKey(run: Pick<ChromeRun, "family" | "weight" | "style" | "size">): string {
  return JSON.stringify([run.family, run.weight, run.style, run.size]);
}

// A page of one character per face and size the section uses, each alone on its own line, whose
// box heights are the calibration: Chrome's own box for that face, size and ratio.
export function calibrationDiv(keys: readonly string[]): string {
  const spans = keys.map((key, index) => {
    const [family, weight, style, size] = JSON.parse(key) as [string, number, string, string];
    const css = `font-family:${family.replace(/"/gu, "'")};font-weight:${weight};font-style:${style};font-size:${size}`;
    return `<p style="margin:0"><span id="c${index}" style="${css}">x</span></p>`;
  });
  return `<div xmlns="http://www.w3.org/1999/xhtml">${spans.join("")}</div>`;
}

// Every character's box against its calibration.
export function checkHeights(
  runs: readonly ChromeRun[],
  heights: ChromeHeights,
  calibration: ReadonlyMap<string, number>,
): PageCheck[] {
  const keyOf = new Map(runs.map((run) => [run.element, styleKey(run)]));
  const found: PageCheck[] = [];
  for (const { element, heights: boxes } of heights) {
    const key = keyOf.get(element);
    const expected = key === undefined ? undefined : calibration.get(key);
    if (expected === undefined) {
      found.push({ refusal: "char-height", detail: `element ${element} has no calibration` });
      continue;
    }
    const wrong = boxes.find((height) => Math.abs(height - expected) > 1e-3);
    if (wrong !== undefined) {
      found.push({
        refusal: "char-height",
        detail: `element ${element}: a box ${wrong} px high, not ${expected}`,
      });
    }
  }
  return found;
}
