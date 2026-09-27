// R2's second drawing and pictures (docs/design/authority-import-renderer.md, Delivery 3c-B2b),
// drawn by scripts/render/check-drawings.ts in the renderer image. Not for clinical use.

import type { Resource } from "../../../src/render/page.js";

const ROOT = '<div xmlns="http://www.w3.org/1999/xhtml">';
const MARKED = "<p>not for clinical use</p></div>";
const div = (inner: string): string => `${ROOT}${inner}${MARKED}`;

// A 30 × 20 red PNG and a 12 × 8 blue one.
export const RED_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAB4AAAAUCAIAAAAVyRqTAAAAH0lEQVR42mM4wcBAI8QwavSo0aNGjxo9avSo0UPRaACAGNTQpbL55QAAAABJRU5ErkJggg==";
export const BLUE_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAIAAABChommAAAAEElEQVR42mNgYDhBBBrxigAUrUsBk31nVwAAAABJRU5ErkJggg==";

// Pairs whose second drawing differs, each of which the comparison must refuse for the property
// named: the authority's div, and a stand-in for T(div) that changes what T never may.
export type DrawingCase = { name: string; div: string; output: string; property: string };

export const DRAWING_CASES: DrawingCase[] = [
  {
    name: "text",
    div: div("<p>Take 2 tablets</p>"),
    output: div("<p>Take 20 tablets</p>"),
    property: "text",
  },
  {
    name: "list numbers",
    div: div("<ol><li>a</li><li>b</li></ol>"),
    output: div('<ol start="2"><li>a</li><li>b</li></ol>'),
    property: "markers",
  },
  {
    name: "grid",
    div: div("<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>"),
    output: div('<table><tr><td colspan="2">a b</td></tr><tr><td>c</td><td>d</td></tr></table>'),
    property: "table 0",
  },
  {
    name: "picture",
    div: div(`<p>x <img src="data:image/png;base64,${RED_PNG}"/> y</p>`),
    output: div(`<p>x <img src="data:image/png;base64,${BLUE_PNG}"/> y</p>`),
    property: "pictures",
  },
];

// R2's picture forms: a `data:` URI, a `#id` the document contains, and a reference with no
// pinned bytes, each drawn as stated.
export const PICTURE_FORMS = {
  div: div(
    `<p>a <img src="data:image/png;base64,${RED_PNG}"/> b <img src="#blue"/> c ` +
      '<img src="~/_entity/annotation/00000000-0000-0000-0000-000000000000"/> d</p>',
  ),
  contained: new Map<string, Resource>([
    ["blue", { body: Buffer.from(BLUE_PNG, "base64"), contentType: "image/png" }],
  ]),
  // The boxes each is drawn at: its own size, its own size, and Chrome's broken-image box (its
  // 16 by 16 icon, with no `alt`; measured in the pinned image, 2026-09-27).
  forms: ["data", "contained", "unpinned"],
  boxes: [
    { width: 30, height: 20 },
    { width: 12, height: 8 },
    { width: 16, height: 16 },
  ],
};
