import { parsePixels, tolerance } from "./compare-style.js";
import type { Font, Metrics } from "./font.js";
import { boundFace } from "./fonts.js";
import type { ChromeHeights, ChromePage, ChromeRun } from "./measure.js";

// R2's page and R3's character boxes (docs/design/authority-import-renderer.md): a section whose
// XML does not parse, whose div has padding or a border of its own, or whose div's content box is
// not the width drawn, is a refusal of ours; so is a character whose box is not its bound face's
// ascent plus descent at its size and ratio, exactly.

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

// R3's character box, exact (3c-C1, measured in the image over 135 840 boxes: 16 faces, sizes in
// pt, px, em, %, keywords and nested, at every ratio). Chrome's font size in device pixels is the
// computed size times the ratio, in single precision, floored to 1/100 px (Blink's
// `FontDescription::EffectiveFontSize`); its ascent and descent are the face's hhea ascent and
// descent at that size, each rounded half up; and on Linux a descent rounded down takes a pixel
// from the ascent (Blink's subpixel-positioning borrow, which `--disable-font-subpixel-positioning`
// does not turn off, measured with a baseline marker). Blink's font cache keys a face by that size
// times 100, in single precision, truncated, so two neighbouring sizes can share a key (9.97 and
// 9.98 both 997; 9.99 is 999) and both are drawn at whichever the process drew first (measured:
// a 9.97333 px letter drawn after a 9.984 px one takes its box). And the computed size DevTools
// reports has six significant digits. So the size is known only within an interval: from the
// nominal size less a relative error of 2e-5, less the 1/100 floor and one more 1/100 step, to the
// nominal plus that error plus one 1/100 step. Over it the ascent and descent only step up, so
// their sum, the box, names exactly one pair of them; a box on no pair is a refusal. The borrow is
// decided unless the exact descent crosses a whole pixel inside the interval, and there the
// ascent is known to within one device pixel only (`ascent` a range).
export type Box = {
  // In device pixels, after the borrow: `low` equals `high` where it is decided.
  ascent: { low: number; high: number };
  descent: { low: number; high: number };
};

const f = Math.fround;
const RELATIVE = 2e-5;
const STEP = 0.01;

function sizeInterval(pixels: number, ratio: number): [number, number] {
  const nominal = f(pixels) * f(ratio);
  return [nominal * (1 - RELATIVE) - 2 * STEP, nominal * (1 + RELATIVE) + STEP];
}

// The (ascent, descent) pairs, before the borrow, the face takes over the size interval.
export function boxPairs(metrics: Metrics, pixels: number, ratio: number): [number, number][] {
  const [low, high] = sizeInterval(pixels, ratio);
  const ascent = metrics.hheaAscender / metrics.unitsPerEm;
  const descent = -metrics.hheaDescender / metrics.unitsPerEm;
  const sizes = [low, high];
  for (const unit of [ascent, descent]) {
    for (let step = Math.ceil(unit * low - 0.5); (step + 0.5) / unit <= high; step += 1) {
      const at = (step + 0.5) / unit;
      sizes.push(at - 1e-9, at + 1e-9);
    }
  }
  const pairs = new Map<string, [number, number]>();
  for (const size of sizes.filter((value) => value >= low && value <= high).sort((a, b) => a - b)) {
    const pair: [number, number] = [
      Math.floor(ascent * size + 0.5),
      Math.floor(descent * size + 0.5),
    ];
    pairs.set(pair.join(), pair);
  }
  return [...pairs.values()];
}

// The box a height in CSS pixels names, or undefined if it is on no pair of the face (or the face
// is too small for Blink's rounding: an ascent under 3 device pixels, which T's 5 pt floor never
// reaches at 0.8).
export function resolveBox(
  metrics: Metrics,
  pixels: number,
  ratio: number,
  height: number,
): Box | undefined {
  const device = height * ratio;
  const whole = Math.round(device);
  // Chrome reports heights in 1/64 CSS px.
  if (Math.abs(device - whole) > ratio / 64 + 1e-9) return undefined;
  const [low, high] = sizeInterval(pixels, ratio);
  if ((metrics.hheaAscender / metrics.unitsPerEm) * low < 3) return undefined;
  const pair = boxPairs(metrics, pixels, ratio).find(([a, d]) => a + d === whole);
  if (pair === undefined) return undefined;
  const [ascent, descent] = pair;
  const exact = [low, high].map((size) => (-metrics.hheaDescender / metrics.unitsPerEm) * size);
  if (exact.every((value) => descent < value)) {
    return {
      ascent: { low: ascent - 1, high: ascent - 1 },
      descent: { low: descent + 1, high: descent + 1 },
    };
  }
  if (exact.every((value) => descent >= value)) {
    return { ascent: { low: ascent, high: ascent }, descent: { low: descent, high: descent } };
  }
  return {
    ascent: { low: ascent - 1, high: ascent },
    descent: { low: descent, high: descent + 1 },
  };
}

// Every character's box against R3's, from its bound face's metrics.
export function checkHeights(
  runs: readonly ChromeRun[],
  heights: ChromeHeights,
  faces: ReadonlyMap<string, Font>,
  ratio: number,
): PageCheck[] {
  const bound = new Map<number, { metrics: Metrics; pixels: number }>();
  for (const run of runs) {
    if (bound.has(run.element)) continue;
    const face = faces.get(boundFace(run.family, run.weight, run.style) ?? "");
    const pixels = parsePixels(run.size);
    if (face !== undefined && pixels !== undefined) {
      bound.set(run.element, { metrics: face.metrics, pixels });
    }
  }
  const found: PageCheck[] = [];
  for (const { element, heights: boxes } of heights) {
    const face = bound.get(element);
    if (face === undefined) {
      found.push({ refusal: "char-height", detail: `element ${element} has no bound face` });
      continue;
    }
    const wrong = boxes.find(
      (height) => resolveBox(face.metrics, face.pixels, ratio, height) === undefined,
    );
    if (wrong !== undefined) {
      const sums = boxPairs(face.metrics, face.pixels, ratio).map(([a, d]) => (a + d) / ratio);
      found.push({
        refusal: "char-height",
        detail: `element ${element}: a box ${wrong} px high, not the bound face's (${sums.join(" or ")})`,
      });
    }
  }
  return found;
}
