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
// undefined for a family it does not bind. Chrome draws a weight of 600 or more in the bold face
// and `oblique` in the italic one (measured in the image).
export function boundFace(family: string, weight: number, style: string): string | undefined {
  const pinned = BINDINGS.get(namedFamily(family).toLowerCase());
  if (pinned === undefined) return undefined;
  const bold = weight >= 600;
  const italic = style === "italic" || style.startsWith("oblique");
  const suffix = bold ? (italic ? "BoldItalic" : "Bold") : italic ? "Italic" : "Regular";
  // Liberation's regular faces carry no suffix in their PostScript names.
  if (suffix === "Regular" && pinned.startsWith("Liberation")) return pinned;
  return `${pinned}-${suffix}`;
}

// The scripts R3 bounds.
const SCRIPT = /^[\p{Script=Latin}\p{Script=Greek}\p{Script=Common}\p{Script=Inherited}]$/u;

// Code points Chrome does not draw as a glyph of their own: ASCII whitespace controls (a line
// break or a space), the soft hyphen (a hyphen only at a line break, drawn with U+002D), and
// the default-ignorable code points.
const NOT_DRAWN = /^[\t\n\f\r­\p{Default_Ignorable_Code_Point}]$/u;

// The closed list of substitutions (R6): the code point drawn with another's glyph where the
// face lacks its own, each asserted by a pixel test of the image (scripts/render/smoke.ts).
const SUBSTITUTIONS: ReadonlyMap<number, number> = new Map([
  [0x00a0, 0x0020], // no-break space, the space's glyph
  [0x2011, 0x002d], // non-breaking hyphen, the hyphen's
]);

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
  // Only whitespace and code points Chrome does not draw: nothing is drawn, no face to judge (the
  // whitespace between table parts and list items, a node of only a soft hyphen).
  if (Array.from(node.text).every((character) => character === " " || NOT_DRAWN.test(character))) {
    return found;
  }
  for (const character of node.text) {
    if (!SCRIPT.test(character)) {
      const codePoint = character.codePointAt(0) ?? 0;
      found.push({ refusal: "script", codePoint, detail: `U+${hex(codePoint)}` });
    }
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
    if (NOT_DRAWN.test(character)) continue;
    const codePoint = character.codePointAt(0) ?? 0;
    if (face.codePoints.has(codePoint)) continue;
    const substitute = SUBSTITUTIONS.get(codePoint);
    if (substitute !== undefined && face.codePoints.has(substitute)) continue;
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
