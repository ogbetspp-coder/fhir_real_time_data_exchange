import { launchChrome } from "../../src/render/cdp.js";
import { boundFace, loadFaces } from "../../src/render/fonts.js";
import { boxPairs, resolveBox } from "../../src/render/page-checks.js";
import { openPage } from "../../src/render/page.js";

// R3's exact character box (docs/design/authority-import-renderer.md, Delivery 3c-C1), in the
// renderer image: an "H" in each of the sixteen pinned faces at sizes in pt, px, em, %, keywords
// and nested chains, at every ratio of R2. Each box must be the bound face's ascent plus descent
// exactly (`resolveBox`), and the baseline, read from a zero-size inline block beside the letter
// on a page of its own, must lie the ascent the model names below the box's top: exactly where
// the borrow is decided, within the model's one-pixel range where it is not. A seeded box one
// device pixel taller than the face's tallest must be refused.
//
// usage (inside the image): node --import tsx scripts/render/check-boxes.ts

const EXECUTABLE =
  process.env.RENDERER_CHROME ??
  "/opt/renderer/chrome-headless-shell-linux64/chrome-headless-shell";
const FONTS = process.env.RENDERER_FONTS ?? "/opt/renderer/fonts";
const NO_SANDBOX = process.env.RENDERER_NO_SANDBOX === "1";
const RATIOS = [0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.625, 3];
const FAMILIES = ["'Times New Roman'", "Arial", "Calibri", "Cambria"];
const STYLES: [number, string][] = [
  [400, "normal"],
  [700, "normal"],
  [400, "italic"],
  [700, "italic"],
];

// The size chains: each is a list of nested `font-size` values, the last the letter's.
function chains(): string[][] {
  const found: string[][] = [];
  for (let pt = 5; pt <= 30; pt += 0.25) found.push([`${pt}pt`]);
  for (let tenth = 70; tenth <= 400; tenth += 3) found.push([`${(tenth / 10).toFixed(1)}px`]);
  const parents = ["8pt", "9pt", "10pt", "11pt", "12pt", "16px", "13px"];
  for (const parent of parents) {
    for (let hundredth = 50; hundredth <= 150; hundredth += 3) {
      found.push([parent, `${(hundredth / 100).toFixed(2)}em`], [parent, `${hundredth}%`]);
    }
    found.push([parent, "smaller"], [parent, "larger"]);
    for (const first of ["0.83em", "80%", "smaller", "0.67em", "83%"]) {
      for (const second of ["0.83em", "smaller", "75%", "1.17em"]) {
        found.push([parent, first, second]);
      }
    }
  }
  for (const keyword of [
    "xx-small",
    "x-small",
    "small",
    "medium",
    "large",
    "x-large",
    "xx-large",
  ]) {
    found.push([keyword]);
  }
  return found;
}

const faces = loadFaces(FONTS);
const keys: string[] = [];
let body = "";
for (const family of FAMILIES) {
  for (const [weight, style] of STYLES) {
    const face = boundFace(family, weight, style) ?? "";
    body += `<p style="font-family:${family};font-weight:${weight};font-style:${style}">`;
    for (const chain of chains()) {
      keys.push(face);
      const letter = `<b data-i="${keys.length - 1}" style="font-weight:inherit">H<i style="display:inline-block;width:0;height:0"></i></b>`;
      body += `${chain.reduceRight((inner, size) => `<span style="font-size:${size}">${inner}</span>`, letter)} `;
    }
    body += "</p>";
  }
}

// Per letter: its computed size, its box's height and the baseline's distance below its top.
const READ = `(() => {
  const out = [];
  for (const element of document.querySelectorAll("b[data-i]")) {
    const range = new Range();
    range.setStart(element.firstChild, 0);
    range.setEnd(element.firstChild, 1);
    const box = range.getClientRects()[0];
    const baseline = element.lastElementChild.getBoundingClientRect().bottom;
    out.push([getComputedStyle(element).fontSize, box.height, baseline - box.top]);
  }
  return out;
})()`;

const failures: string[] = [];
let boxes = 0;
let undecided = 0;
for (const ratio of RATIOS) {
  const browser = launchChrome({ executable: EXECUTABLE, ratio, noSandbox: NO_SANDBOX });
  try {
    const page = await openPage(browser.cdp, {
      div: `<div xmlns="http://www.w3.org/1999/xhtml">${body}</div>`,
      mode: "html",
      width: 813,
    });
    try {
      const read = await page.evaluate<[string, number, number][]>(READ);
      read.forEach(([size, height, above], index) => {
        const face = faces.get(keys[index] ?? "");
        const pixels = Number.parseFloat(size);
        if (face === undefined) {
          failures.push(`letter ${index}: no pinned face`);
          return;
        }
        boxes += 1;
        const box = resolveBox(face.metrics, pixels, ratio, height);
        const ascent = above * ratio;
        if (box === undefined) {
          failures.push(`${face.postScriptName} ${size} at ${ratio}: a box ${height} px high`);
        } else if (ascent < box.ascent.low - 0.02 || ascent > box.ascent.high + 0.02) {
          failures.push(
            `${face.postScriptName} ${size} at ${ratio}: baseline ${ascent} below the top, not ${JSON.stringify(box.ascent)}`,
          );
        } else {
          if (box.ascent.low !== box.ascent.high) undecided += 1;
          // The seed: a box one device pixel taller than the face's tallest is refused.
          const tallest = Math.max(...boxPairs(face.metrics, pixels, ratio).map(([a, d]) => a + d));
          if (resolveBox(face.metrics, pixels, ratio, (tallest + 1) / ratio) !== undefined) {
            failures.push(`${face.postScriptName} ${size} at ${ratio}: a taller box passed`);
          }
        }
      });
    } finally {
      await page.close();
    }
  } finally {
    await browser.close();
  }
}

if (failures.length > 0) {
  console.error(failures.slice(0, 60).join("\n"));
  console.error(`boxes: ${failures.length} failures`);
  process.exit(1);
}
console.log(
  `boxes: ${boxes} character boxes in 16 faces at ${RATIOS.length} ratios, each the bound face's ascent plus descent exactly, ` +
    `each baseline where the model puts it (${undecided} within the model's one-pixel range, the rest exactly); a box one pixel taller refused`,
);
