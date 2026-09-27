// Sections each of which the renderer gate's page and font checks must refuse
// (docs/design/authority-import-renderer.md, R2, R3 and R6), drawn by scripts/render/check-fonts.ts
// in the renderer image. `div` is the whole section; the refusal is the one it must produce.
// Not for clinical use.

import type { FontRefusal } from "../../../src/render/fonts.js";
import type { PageRefusal } from "../../../src/render/page-checks.js";

export type RefusalCase = { name: string; div: string; refusal: FontRefusal | PageRefusal };

const div = (inner: string, root = '<div xmlns="http://www.w3.org/1999/xhtml">'): string =>
  `${root}${inner}<p>not for clinical use</p></div>`;

export const REFUSAL_CASES: RefusalCase[] = [
  // A family R6 binds to no face: Chrome draws it in Liberation Serif, silently.
  {
    name: "verdana",
    div: div('<p style="font-family:Verdana">Dose</p>'),
    refusal: "font-unpinned",
  },
  {
    name: "monospace",
    div: div('<p style="font-family:monospace">Dose</p>'),
    refusal: "font-unpinned",
  },
  // U+2070, which Liberation Serif lacks (R6's seed): Chrome draws it from Carlito.
  { name: "superscript-zero", div: div("<p>10⁰</p>"), refusal: "font-coverage" },
  { name: "superscript-zero-face", div: div("<p>10⁰</p>"), refusal: "font-face" },
  // Greek in Cambria, whose Caladea has none: drawn from Liberation Serif.
  {
    name: "greek-in-cambria",
    div: div('<p style="font-family:Cambria">μ</p>'),
    refusal: "font-face",
  },
  // A script R3 does not bound.
  { name: "cyrillic", div: div("<p>Ж</p>"), refusal: "script" },
  // An entity XML does not define: the page does not parse in XML mode.
  { name: "parsererror", div: div("<p>a&nbsp;b</p>"), refusal: "parsererror" },
  // A div with padding of its own.
  {
    name: "div-padding",
    div: div("<p>x</p>", '<div xmlns="http://www.w3.org/1999/xhtml" style="padding:4px">'),
    refusal: "div-box",
  },
];
