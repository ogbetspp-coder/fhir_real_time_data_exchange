import { launchChrome, type Browser } from "../../src/render/cdp.js";
import {
  comparePictureLists,
  compareDrawings,
  listPictures,
  readDrawing,
  type Drawing,
  type DrawingMismatch,
  type ListedPicture,
} from "../../src/render/drawing.js";
import { readPage } from "../../src/render/measure.js";
import { checkPage } from "../../src/render/page-checks.js";
import { openPage, type Mode } from "../../src/render/page.js";
import {
  PictureError,
  preparePictures,
  UNPINNED_URL,
  type Contained,
} from "../../src/render/pictures.js";
import { DRAWING_CASES, PICTURE_FORMS } from "../../test/fixtures/render/drawing-cases.js";
import { EXECUTABLE, NO_SANDBOX } from "./sections.js";

// R2's second drawing's seeds (docs/design/authority-import-renderer.md, Delivery 3c-B2b), in the
// renderer image: the seeded pairs of test/fixtures/render/drawing-cases.ts must each be refused
// for exactly the properties named, in both modes, and the picture forms drawn as stated. Every
// section's second drawing is scripts/render/check.ts's.
//
// usage (inside the image): node --import tsx scripts/render/check-drawings.ts

const MODES: readonly Mode[] = ["html", "xml"];

const failures: string[] = [];

// The second drawing's comparison: the drawings, and the picture lists.
function compare(authority: Drawn, t: Drawn): DrawingMismatch[] {
  return [
    ...compareDrawings(authority.drawing, t.drawing),
    ...comparePictureLists(authority.pictures, t.pictures),
  ];
}

// A drawing, its picture list, and the page's own refusals: R2's page, and a picture not drawn as
// prepared, or a request failed other than the unpinned picture's.
type Drawn = { drawing: Drawing; pictures: ListedPicture[]; page: string[] };

async function draw(
  browser: Browser,
  div: string,
  contained: Contained,
  mode: Mode,
  width: number,
): Promise<Drawn> {
  let prepared: ReturnType<typeof preparePictures>;
  try {
    prepared = preparePictures(div, contained);
  } catch (error) {
    if (!(error instanceof PictureError)) throw error;
    const drawing = { text: "", markers: [], tables: [], pictures: [] };
    return { drawing, pictures: [], page: [`picture-grammar: ${error.message}`] };
  }
  const page = await openPage(browser.cdp, {
    div: prepared.div,
    mode,
    width,
    resources: prepared.resources,
  });
  try {
    const refusals = checkPage(await readPage(page), width, mode).map(
      ({ refusal, detail }) => `${refusal}: ${detail}`,
    );
    const drawing = await readDrawing(page);
    const { list, problems } = listPictures(prepared.pictures, prepared.urls, drawing);
    refusals.push(...problems);
    for (const url of page.failed) {
      if (url !== UNPINNED_URL) refusals.push(`request-failed: ${url.slice(0, 80)}`);
    }
    return { drawing, pictures: list, page: refusals };
  } finally {
    await page.close();
  }
}

let drawings = 0;
const browser = launchChrome({ executable: EXECUTABLE, ratio: 1, noSandbox: NO_SANDBOX });
try {
  // Each seed in both modes, refused for exactly its properties and drawn with no refusal of its page.
  for (const seeded of DRAWING_CASES) {
    for (const mode of MODES) {
      const authority = await draw(browser, seeded.div, new Map(), mode, 813);
      const t = await draw(browser, seeded.output, new Map(), mode, 813);
      drawings += 2;
      const found = compare(authority, t).map(({ property }) => property);
      const page = [...authority.page, ...t.page];
      if (found.join(", ") !== seeded.properties.join(", ") || page.length > 0) {
        failures.push(
          `seeded ${seeded.name} (${mode}): expected ${seeded.properties.join(", ")}, got ${found.join(", ") || "nothing"}` +
            (page.length > 0 ? `; page: ${page.join("; ")}` : ""),
        );
      }
    }
  }
  for (const mode of MODES) {
    const forms = await draw(browser, PICTURE_FORMS.div, PICTURE_FORMS.contained, mode, 813);
    drawings += 1;
    if (forms.page.length > 0) failures.push(`picture forms (${mode}): ${forms.page.join("; ")}`);
    const got = forms.pictures.map(({ form }) => form);
    if (JSON.stringify(got) !== JSON.stringify(PICTURE_FORMS.forms)) {
      failures.push(`picture forms (${mode}): ${got.join(", ")}`);
    }
    for (const [index, expected] of PICTURE_FORMS.boxes.entries()) {
      const box = forms.pictures[index]?.box;
      if (JSON.stringify(box) !== JSON.stringify(expected)) {
        failures.push(
          `picture ${index} (${mode}) drawn at ${JSON.stringify(box)}, not ${JSON.stringify(expected)}`,
        );
      }
    }
  }
} finally {
  await browser.close();
}

if (failures.length > 0) {
  console.error(failures.slice(0, 60).join("\n"));
  console.error(`second drawing's seeds: ${failures.length} failures`);
  process.exit(1);
}
const expected = (DRAWING_CASES.length * 2 + 1) * MODES.length;
if (drawings !== expected) {
  console.error(`second drawing's seeds: ${drawings} drawings, not ${expected}`);
  process.exit(1);
}
console.log(
  `second drawing's seeds: ${DRAWING_CASES.length} seeded differences caught in both modes; picture forms drawn as stated (${drawings} drawings)`,
);
