import { readFileSync } from "node:fs";

import { transformDocument } from "../../src/authority/t/document.js";
import { TRefusal, transformSection } from "../../src/authority/t/transform.js";
import { xhtmlToText, XhtmlError } from "../../src/fidelity/xhtml.js";
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
import { openPage, type Mode, type Resource } from "../../src/render/page.js";
import {
  PictureError,
  preparePictures,
  UNPINNED_URL,
  type Contained,
} from "../../src/render/pictures.js";
import { T_CASES } from "../../test/fixtures/authority/t-cases.js";
import { DRAWING_CASES, PICTURE_FORMS } from "../../test/fixtures/render/drawing-cases.js";
import { MODEL_CASES } from "../../test/fixtures/render/model-cases.js";

// R2's second drawing (docs/design/authority-import-renderer.md, Delivery 3c-B2b), in the renderer
// image: for every section T and the scanner accept, and every accepted T and model case, the
// authority's drawing and T(div)'s, in both modes at R2's named widths, each with its pictures
// drawn as the import carries them: the text, list numbers, table grids and pictures must be
// the same, and each page's content box must be its width. The seeded pairs of
// test/fixtures/render/drawing-cases.ts must each be refused for exactly the properties named, and the
// picture forms drawn as stated.
//
// usage (inside the image): node --import tsx scripts/render/check-drawings.ts

const EXECUTABLE =
  process.env.RENDERER_CHROME ??
  "/opt/renderer/chrome-headless-shell-linux64/chrome-headless-shell";
const NO_SANDBOX = process.env.RENDERER_NO_SANDBOX === "1";
const LABELS = "labels/ema-epi";
const ROOT = '<div xmlns="http://www.w3.org/1999/xhtml">';
const MARKED = "<p>not for clinical use</p></div>";
const WIDTHS = [813, 360, 1240];
const MODES: readonly Mode[] = ["html", "xml"];

type Section = { name: string; div: string; output: string; contained: Contained };

function scanned(output: string): boolean {
  try {
    xhtmlToText(output);
    return true;
  } catch (error) {
    if (error instanceof XhtmlError) return false;
    throw error;
  }
}

// The pictures a label's document contains: its Composition's contained Binaries, by id.
function containedOf(document: {
  entry?: {
    resource?: {
      contained?: { resourceType?: string; id?: string; contentType?: string; data?: string }[];
    };
  }[];
}): Contained {
  const found = new Map<string, Resource>();
  for (const resource of document.entry?.[0]?.resource?.contained ?? []) {
    if (resource.resourceType !== "Binary" || resource.id === undefined) continue;
    found.set(resource.id, {
      body: Buffer.from(resource.data ?? "", "base64"),
      contentType: resource.contentType ?? "application/octet-stream",
    });
  }
  return found;
}

function sections(): Section[] {
  const found: Section[] = [];
  for (const testCase of T_CASES) {
    if (!("div" in testCase.expected)) continue;
    const div = `${testCase.root ?? ROOT}${testCase.inner}${MARKED}`;
    try {
      const evidence = testCase.evidence === undefined ? undefined : new Set(testCase.evidence);
      const output = transformSection(div, evidence).div;
      if (scanned(output)) {
        found.push({ name: `t-case ${testCase.name}`, div, output, contained: new Map() });
      }
    } catch (error) {
      if (!(error instanceof TRefusal)) throw error;
    }
  }
  for (const { name, inner } of MODEL_CASES) {
    const div = `${ROOT}${inner}${MARKED}`;
    found.push({
      name: `model-case ${name}`,
      div,
      output: transformSection(div).div,
      contained: new Map(),
    });
  }
  const lock = JSON.parse(readFileSync(`${LABELS}/sources.lock.json`, "utf8")) as {
    sources: { file: string }[];
  };
  type Node = { text?: { div?: string }; section?: Node[] };
  for (const { file } of lock.sources) {
    const document = JSON.parse(readFileSync(`${LABELS}/sources/${file}`, "utf8")) as Parameters<
      typeof containedOf
    >[0] & { entry?: { resource?: { section?: Node[] } }[] };
    const contained = containedOf(document);
    const placed: { path: string; div: string | undefined }[] = [];
    const walk = (nodes: Node[], base: string): void => {
      nodes.forEach((node, position) => {
        const path = `${base}[${position}]`;
        placed.push({ path, div: node.text?.div });
        walk(node.section ?? [], `${path}.section`);
      });
    };
    walk(
      (document.entry?.[0]?.resource as { section?: Node[] } | undefined)?.section ?? [],
      "Composition.section",
    );
    const outcomes = transformDocument(placed.map(({ div }) => div));
    placed.forEach(({ path, div }, at) => {
      const outcome = outcomes[at];
      if (div === undefined || outcome === undefined || !("div" in outcome)) return;
      if (!scanned(outcome.div)) return;
      found.push({ name: `${file} ${path}`, div, output: outcome.div, contained });
    });
  }
  return found;
}

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

const all = sections();
let drawings = 0;
const browser = launchChrome({ executable: EXECUTABLE, ratio: 1, noSandbox: NO_SANDBOX });
try {
  for (const section of all) {
    for (const mode of MODES) {
      for (const width of WIDTHS) {
        const authority = await draw(browser, section.div, section.contained, mode, width);
        const t = await draw(browser, section.output, section.contained, mode, width);
        drawings += 2;
        const problems = [
          ...authority.page,
          ...t.page.map((problem) => `T(div) ${problem}`),
          ...compare(authority, t).map(
            ({ property, authority: a, t: b }) =>
              `second-drawing ${property}: ${a.slice(0, 120)} / ${b.slice(0, 120)}`,
          ),
        ];
        if (problems.length > 0)
          failures.push(`${section.name} (${mode}, ${width} px): ${problems.join("; ")}`);
      }
    }
  }
  // Each seed in both modes, refused for exactly its properties and drawn with no refusal of its page.
  for (const seeded of DRAWING_CASES) {
    for (const mode of MODES) {
      const authority = await draw(browser, seeded.div, new Map(), mode, 813);
      const t = await draw(browser, seeded.output, new Map(), mode, 813);
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
  console.error(`second drawing: ${failures.length} failures`);
  process.exit(1);
}
console.log(
  `second drawing: ${all.length} carried sections and cases, ${drawings} drawings at ${WIDTHS.length} widths in both modes, ` +
    `the same text, list numbers, grids and pictures as T(div)'s; ${DRAWING_CASES.length} seeded differences caught; picture forms drawn as stated`,
);
