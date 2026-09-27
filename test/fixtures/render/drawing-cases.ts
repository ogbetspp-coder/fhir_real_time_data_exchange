// R2's second drawing and pictures (docs/design/authority-import-renderer.md, Delivery 3c-B2b),
// drawn by scripts/render/check-drawings.ts in the renderer image. Not for clinical use.

import type { Resource } from "../../../src/render/page.js";

const ROOT = '<div xmlns="http://www.w3.org/1999/xhtml">';
const MARKED = "<p>not for clinical use</p></div>";
const div = (inner: string): string => `${ROOT}${inner}${MARKED}`;

// A 30 × 20 red PNG, a 12 × 8 blue one, and a 30 × 20 green one (the red one's size, other bytes).
export const RED_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAB4AAAAUCAIAAAAVyRqTAAAAH0lEQVR42mM4wcBAI8QwavSo0aNGjxo9avSo0UPRaACAGNTQpbL55QAAAABJRU5ErkJggg==";
export const BLUE_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAIAAABChommAAAAEElEQVR42mNgYDhBBBrxigAUrUsBk31nVwAAAABJRU5ErkJggg==";
export const GREEN_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAB4AAAAUCAIAAAAVyRqTAAAAHklEQVR42mNgaGCgFRo1etToUaNHjR41etTooWg0AK2SLBCEg9ZVAAAAAElFTkSuQmCC";

// Pairs whose second drawing differs, each of which the comparison must refuse for exactly the
// properties named: the authority's div, and a stand-in for T(div) that changes what T never may.
export type DrawingCase = { name: string; div: string; output: string; properties: string[] };

export const DRAWING_CASES: DrawingCase[] = [
  {
    name: "text",
    div: div("<p>Take 2 tablets</p>"),
    output: div("<p>Take 20 tablets</p>"),
    properties: ["text"],
  },
  {
    name: "list numbers",
    div: div("<ol><li>a</li><li>b</li></ol>"),
    output: div('<ol start="2"><li>a</li><li>b</li></ol>'),
    properties: ["markers"],
  },
  {
    name: "grid",
    div: div("<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>"),
    output: div('<table><tr><td colspan="2">a b</td></tr><tr><td>c</td><td>d</td></tr></table>'),
    // A cell fewer: its row's text is read without a tab.
    properties: ["text", "table 0"],
  },
  // A span moved: the same text (`innerText` reads a row's cells with tabs) and the same cells.
  {
    name: "grid, a column span moved",
    div: div(
      '<table><tr><td colspan="2">a</td><td>b</td></tr><tr><td>c</td><td>d</td><td>e</td></tr></table>',
    ),
    output: div(
      '<table><tr><td>a</td><td colspan="2">b</td></tr><tr><td>c</td><td>d</td><td>e</td></tr></table>',
    ),
    properties: ["table 0"],
  },
  {
    name: "grid, a row span moved",
    div: div('<table><tr><td rowspan="2">a</td><td>b</td></tr><tr><td>c</td></tr></table>'),
    output: div('<table><tr><td>a</td><td rowspan="2">b</td></tr><tr><td>c</td></tr></table>'),
    properties: ["table 0"],
  },
  {
    name: "picture",
    div: div(`<p>x <img src="data:image/png;base64,${RED_PNG}"/> y</p>`),
    output: div(`<p>x <img src="data:image/png;base64,${BLUE_PNG}"/> y</p>`),
    // Another box from other bytes: the drawing's boxes and the list's hashes and boxes.
    properties: ["pictures", "picture list"],
  },
  // The same box from other bytes: refused by the picture list's hashes alone.
  {
    name: "picture bytes",
    div: div(`<p>x <img src="data:image/png;base64,${RED_PNG}"/> y</p>`),
    output: div(`<p>x <img src="data:image/png;base64,${GREEN_PNG}"/> y</p>`),
    properties: ["picture list"],
  },
];

// R2's picture forms: a `data:` URI, a `#id` the document contains, and a reference with no
// pinned bytes, each drawn as stated.
export const PICTURE_FORMS = {
  div: div(
    `<p>a <img src="data:image/png;base64,${RED_PNG}"/> b <img src="#blue"/> c ` +
      '<img src="~/_entity/annotation/00000000-0000-0000-0000-000000000000"/> d ' +
      '<img alt="Figure 1" src="~/_entity/annotation/00000000-0000-0000-0000-000000000001"/> e ' +
      '<img style="width:40px;height:30px" src="~/_entity/annotation/00000000-0000-0000-0000-000000000002"/> f</p>',
  ),
  contained: new Map<string, Resource>([
    ["blue", { body: Buffer.from(BLUE_PNG, "base64"), contentType: "image/png" }],
  ]),
  // The boxes each is drawn at: its own size, its own size, and Chrome's broken-image box: its
  // 16 by 16 icon with no `alt`, its `alt` box, and its declared size (measured in the pinned
  // image, 2026-09-27).
  forms: ["data", "contained", "unpinned", "unpinned", "unpinned"],
  boxes: [
    { width: 30, height: 20 },
    { width: 12, height: 8 },
    { width: 16, height: 16 },
    { width: 69.78125, height: 18 },
    { width: 40, height: 30 },
  ],
};
