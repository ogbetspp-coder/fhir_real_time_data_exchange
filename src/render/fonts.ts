import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { readFont, type Font } from "./font.js";

// R3's fonts and scripts, and R6's coverage (docs/design/authority-import-renderer.md): every
// text node of every section drawn, T's or not, is drawn in exactly one pinned face, the one R6
// binds to the family and face the node names; only Latin, Greek, Common and Inherited text; and
// every code point it draws is in that face's character map, but for the substitutions Blink and
// HarfBuzz make, which the image's smoke check asserts by pixels. Each failure is a refusal of
// ours.

export type FontRefusal =
  | "font-unpinned" // a family R6 binds to no pinned face
  | "font-face" // drawn in another face than the one bound, or in more than one (a fallback)
  | "font-coverage" // a code point the bound face lacks
  | "script"; // a code point outside Latin, Greek, Common and Inherited

// R6's bindings, the fontconfig's strong aliases (src/render/image/fonts.conf), by family name
// compared ASCII case-insensitively: the pinned family each names.
const BINDINGS: ReadonlyMap<string, "LiberationSerif" | "LiberationSans" | "Carlito" | "Caladea"> =
  new Map([
    ["times new roman", "LiberationSerif"],
    ["times", "LiberationSerif"],
    ["serif", "LiberationSerif"],
    ["arial", "LiberationSans"],
    ["helvetica", "LiberationSans"],
    ["sans-serif", "LiberationSans"],
    ["calibri", "Carlito"],
    ["cambria", "Caladea"],
  ]);

// The first family of a computed `font-family`, unquoted: the family the node names. Chrome
// silently draws an unbound family in the default face (measured in the image: Verdana, Segoe
// UI, Symbol, Courier New and any unknown name draw in Liberation Serif), so the name, not the
// drawn face, decides `font-unpinned`.
export function namedFamily(computed: string): string {
  const first = computed.split(",")[0]?.trim() ?? "";
  return first.replace(/^(["'])(.*)\1$/u, "$2");
}

// The PostScript name of the pinned face R6 binds to a computed family, weight and style, or
// undefined for a family it does not bind. With a 400 and a 700 face, CSS font matching draws a
// weight above 500 in the bold face (measured: 550 and 600 bold, 500 regular), and `italic` or a
// bare `oblique` in the italic one; an oblique with an angle is expected upright, so any angle
// Chrome draws otherwise is a `font-face` refusal, never a pass.
export function boundFace(family: string, weight: number, style: string): string | undefined {
  const pinned = BINDINGS.get(namedFamily(family).toLowerCase());
  if (pinned === undefined) return undefined;
  const bold = weight > 500;
  const italic = style === "italic" || style === "oblique";
  const suffix = bold ? (italic ? "BoldItalic" : "Bold") : italic ? "Italic" : "Regular";
  // Liberation's regular faces carry no suffix in their PostScript names.
  if (suffix === "Regular" && pinned.startsWith("Liberation")) return pinned;
  return `${pinned}-${suffix}`;
}

// The scripts R3 bounds.
const SCRIPT = /^[\p{Script=Latin}\p{Script=Greek}\p{Script=Common}\p{Script=Inherited}]$/u;

// Code points Chrome draws as nothing of their own, a closed list, each range asserted by a pixel
// test of the image (scripts/render/smoke.ts: drawn between two letters, the same pixels as the
// letters alone): ASCII whitespace controls (a line break or a space), the soft hyphen (a
// hyphen only at a line break), and the default-ignorable code points HarfBuzz hides. The
// default-ignorables HarfBuzz draws as glyphs (the Hangul fillers U+115F, U+1160, U+3164, U+FFA0
// and U+1BCA0 to U+1BCA3, drawn as .notdef: the first code review, measured) are not on it, so
// the character map judges them; nor are the bidirectional marks, embeddings and isolates, which
// can reorder what is drawn (T refuses them).
export const NOT_DRAWN_RANGES: readonly (readonly [number, number])[] = [
  [0x0009, 0x0009],
  [0x000a, 0x000a],
  [0x000c, 0x000d],
  [0x00ad, 0x00ad],
  [0x034f, 0x034f],
  [0x061c, 0x061c],
  [0x17b4, 0x17b5],
  // U+180F, the fourth Mongolian variation selector, is drawn as something (CI's pixel test).
  [0x180b, 0x180e],
  [0x200b, 0x200d],
  [0x2060, 0x2065],
  [0x206a, 0x206f],
  [0xfe00, 0xfe0f],
  [0xfeff, 0xfeff],
  [0xfff0, 0xfff8],
  [0x1d173, 0x1d17a],
  [0xe0000, 0xe0fff],
];
function notDrawn(character: string): boolean {
  const codePoint = character.codePointAt(0) ?? 0;
  return NOT_DRAWN_RANGES.some(([first, last]) => codePoint >= first && codePoint <= last);
}

// The closed list of substitutions (R6): a code point drawn with another's glyph where the face
// lacks its own, in the faces where a pixel test of the image asserts it (scripts/render/smoke.ts).
// The non-breaking hyphen is drawn as the hyphen only in Liberation's faces; Chrome draws it in
// Carlito and Caladea from another pinned face (measured), which `font-face` refuses.
const SUBSTITUTIONS: readonly { codePoint: number; glyphOf: number; faces: RegExp }[] = [
  // HarfBuzz draws U+2011 with U+2010's glyph (the first code review), which Liberation has.
  { codePoint: 0x2011, glyphOf: 0x2010, faces: /^Liberation(?:Serif|Sans)/u },
];

export type FontCheck = {
  refusal: FontRefusal;
  // The code point, where the refusal is about one.
  codePoint?: number;
  detail: string;
};

// The checks of one text node: its computed family, weight and style, the faces Chrome reports
// drawing it in, and its text. `faces` maps a PostScript name to the pinned font.
export function checkTextNode(
  node: { family: string; weight: number; style: string; drawnIn: readonly string[]; text: string },
  faces: ReadonlyMap<string, Font>,
): FontCheck[] {
  const found: FontCheck[] = [];
  // The script bound holds for every node, whatever is drawn of it.
  for (const character of node.text) {
    if (!SCRIPT.test(character)) {
      const codePoint = character.codePointAt(0) ?? 0;
      found.push({ refusal: "script", codePoint, detail: `U+${hex(codePoint)}` });
    }
  }
  // Only spaces and code points Chrome draws as nothing: no face to judge (the whitespace between
  // table parts and list items, a node of only a soft hyphen).
  if (Array.from(node.text).every((character) => character === " " || notDrawn(character))) {
    return found;
  }
  const expected = boundFace(node.family, node.weight, node.style);
  if (expected === undefined) {
    return [...found, { refusal: "font-unpinned", detail: namedFamily(node.family) }];
  }
  const drawn = [...new Set(node.drawnIn)];
  if (drawn.length !== 1 || drawn[0] !== expected) {
    found.push({ refusal: "font-face", detail: `${drawn.join(", ") || "none"}, not ${expected}` });
  }
  const face = faces.get(expected);
  if (face === undefined) {
    found.push({ refusal: "font-unpinned", detail: `${expected} is not in the image` });
    return found;
  }
  for (const character of node.text) {
    if (notDrawn(character)) continue;
    const codePoint = character.codePointAt(0) ?? 0;
    if (face.codePoints.has(codePoint)) continue;
    const substitute = SUBSTITUTIONS.find(
      (entry) => entry.codePoint === codePoint && entry.faces.test(expected),
    );
    if (substitute !== undefined && face.codePoints.has(substitute.glyphOf)) continue;
    found.push({
      refusal: "font-coverage",
      codePoint,
      detail: `U+${hex(codePoint)} is not in ${expected}`,
    });
  }
  return found;
}

function hex(codePoint: number): string {
  return codePoint.toString(16).toUpperCase().padStart(4, "0");
}

// The pinned faces of a directory, by PostScript name.
export function loadFaces(directory: string): Map<string, Font> {
  const faces = new Map<string, Font>();
  for (const file of readdirSync(directory).sort()) {
    if (!file.endsWith(".ttf")) continue;
    const font = readFont(readFileSync(path.join(directory, file)));
    faces.set(font.postScriptName, font);
  }
  return faces;
}
