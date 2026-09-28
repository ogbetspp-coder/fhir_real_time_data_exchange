import { writeFileSync } from "node:fs";

import { launchChrome, type Browser } from "../../src/render/cdp.js";
import { compareModel, compareText, type Mismatch } from "../../src/render/compare-style.js";
import {
  comparePictureLists,
  compareDrawings,
  listPictures,
  readDrawing,
  type Drawing,
  type ListedPicture,
} from "../../src/render/drawing.js";
import { checkTextNode, loadFaces } from "../../src/render/fonts.js";
import {
  readElements,
  readHeights,
  readMarkers,
  readPage,
  readRuns,
  readTexts,
  type ChromeHeights,
  type ChromeRun,
} from "../../src/render/measure.js";
import { checkHeights, checkPage } from "../../src/render/page-checks.js";
import { openPage, type Mode, type Page } from "../../src/render/page.js";
import {
  PictureError,
  preparePictures,
  UNPINNED_URL,
  type Contained,
} from "../../src/render/pictures.js";
import { carriedMismatch, RATIOS, WIDTHS, type Section } from "../../src/render/sections.js";
import { EXECUTABLE, FONTS, NO_SANDBOX, sections } from "./sections.js";

// The renderer gate's sweep of every section (docs/design/authority-import-renderer.md, R2, R3
// and R6; the model addendum's M4), in the renderer image. Each page is drawn once per ratio and
// every check reads it, each keeping its own verdict:
// - R3: T's model against Chrome's computed style, list markers and (XML mode) text ranges, for
//   every carried section (src/render/sections.ts) in both modes, at R2's named widths at ratio 1
//   and at 813 px at every other ratio (a computed style does not depend on the width, M4);
// - fonts and page (3c-B2a): at ratio 1 and 813 px, each carried section's page and fonts, and its
//   T(div)'s (T drops the styles, so its drawing can fall back where the authority's did not);
//   and every section T or the scanner refuses, only reported (it is withheld or refuses the
//   import anyway);
// - boxes (3c-C1): every character box of each carried section at 813 px in both modes and at
//   every ratio, exactly R3's, from the face bound at ratio 1 (a face does not depend on the width
//   or the ratio, a stated residual);
// - second drawing (3c-B2b): at ratio 1, each carried section's drawing against T(div)'s at the
//   named widths in both modes, with its pictures drawn as the import carries them.
// Each check counts the carried sections it judged, and fails if that is not every one. The
// seeded refusals are scripts/render/check-fonts.ts's and check-drawings.ts's. The ratios are
// drawn by separate browsers, `--parallel` at a time (ratio 1 first, the longest).
//
// usage (inside the image): node --import tsx scripts/render/check.ts [--ratios 1,2]
//          [--parallel 2] [--out report.json]

function option(name: string): string | undefined {
  const index = process.argv.indexOf(`--${name}`);
  return index < 0 ? undefined : process.argv[index + 1];
}
// Ratio 1 is always drawn: the fonts and the second drawing are judged there.
const ratios = [
  ...new Set([1, ...(option("ratios")?.split(",").map(Number) ?? RATIOS)]),
] as number[];
if (!ratios.every((ratio) => Number.isFinite(ratio) && ratio > 0)) {
  throw new Error("--ratios takes positive numbers");
}
const parallel = Number(option("parallel") ?? "2");
if (!Number.isInteger(parallel) || parallel < 1) throw new Error("--parallel takes a whole number");
const widthsAt = (ratio: number): readonly number[] => (ratio === 1 ? WIDTHS : [813]);
const MODES: readonly Mode[] = ["html", "xml"];
const FONT_WIDTH = 813;

const faces = loadFaces(FONTS);
const all = sections();

type R3Failure = { name: string; mode: Mode; ratio: number; width: number; mismatches: Mismatch[] };
const r3: R3Failure[] = [];
const fonts: string[] = [];
const drawingFailures: string[] = [];
const boxFailures: string[] = [];
// A carried section whose pictures cannot be prepared is not drawn: a failure of every check.
const notDrawn: string[] = [];
// Per check, the carried sections it judged (R3 and the boxes by ratio).
const judgedR3 = new Map<number, string[]>();
const judgedBoxes = new Map<number, string[]>();
const judgedFonts: string[] = [];
const judgedDrawings: string[] = [];
// What the sections T or the scanner refuse report, by refusal.
const reported = new Map<string, number>();
// The faces bound at ratio 1, and every ratio's character boxes, judged once all are drawn.
const runsOf = new Map<string, ChromeRun[]>();
const heightsOf = new Map<string, ChromeHeights>();
let pages = 0;
let r3Drawings = 0;
let secondDrawings = 0;

const push = <T>(map: Map<number, T[]>, key: number, value: T): void => {
  map.set(key, [...(map.get(key) ?? []), value]);
};

// A page's R2 refusals and, unless it did not parse (nothing of the section's to read), its text
// nodes with their faces and their font refusals.
async function pageAndFonts(
  page: Page,
  mode: Mode,
  width: number,
): Promise<{ refusals: string[]; runs: ChromeRun[] }> {
  const refusals = checkPage(await readPage(page), width, mode).map(
    ({ refusal, detail }) => `${refusal}: ${detail}`,
  );
  if (refusals.some((refusal) => refusal.startsWith("parsererror"))) return { refusals, runs: [] };
  const runs = await readRuns(page);
  for (const run of runs) {
    for (const { refusal, detail } of checkTextNode(run, faces)) {
      refusals.push(`${refusal}: ${detail}`);
    }
  }
  return { refusals, runs };
}

// A drawing, its picture list, and its page's own refusals: R2's page, and a picture not drawn as
// prepared, or a request failed other than the unpinned picture's.
type Drawn = { drawing: Drawing; pictures: ListedPicture[]; page: string[] };

async function drawing(
  page: Page,
  prepared: ReturnType<typeof preparePictures>,
  mode: Mode,
  width: number,
): Promise<Drawn> {
  const refusals = checkPage(await readPage(page), width, mode).map(
    ({ refusal, detail }) => `${refusal}: ${detail}`,
  );
  const drawn = await readDrawing(page);
  const { list, problems } = listPictures(prepared.pictures, prepared.urls, drawn);
  refusals.push(...problems);
  for (const url of page.failed) {
    if (url !== UNPINNED_URL) refusals.push(`request-failed: ${url.slice(0, 80)}`);
  }
  return { drawing: drawn, pictures: list, page: refusals };
}

async function open(
  browser: Browser,
  prepared: ReturnType<typeof preparePictures>,
  mode: Mode,
  width: number,
): Promise<Page> {
  pages += 1;
  return openPage(browser.cdp, {
    div: prepared.div,
    mode,
    width,
    resources: prepared.resources,
  });
}

function prepare(div: string, contained: Contained): ReturnType<typeof preparePictures> | string {
  try {
    return preparePictures(div, contained);
  } catch (error) {
    if (!(error instanceof PictureError)) throw error;
    return `picture-grammar: ${error.message}`;
  }
}

// One carried section at one ratio: every page it is drawn on, read by every check.
async function carried(
  browser: Browser,
  section: Extract<Section, { carried: true }>,
  ratio: number,
): Promise<void> {
  const authority = prepare(section.div, section.contained);
  const t = ratio === 1 ? prepare(section.output, section.contained) : undefined;
  if (typeof authority === "string") {
    notDrawn.push(`${section.name} (ratio ${ratio}): not drawn, ${authority}`);
    return;
  }
  if (typeof t === "string") {
    notDrawn.push(`${section.name}: T(div) not drawn, ${t}`);
    return;
  }
  for (const mode of MODES) {
    const fontRefusals: string[] = [];
    for (const width of widthsAt(ratio)) {
      const page = await open(browser, authority, mode, width);
      let drawn: Drawn | undefined;
      try {
        const mismatches = compareModel(
          section.model,
          await readElements(page),
          await readMarkers(page),
          ratio,
          mode,
        );
        if (mode === "xml") mismatches.push(...compareText(section.model, await readTexts(page)));
        r3Drawings += 1;
        if (mismatches.length > 0) r3.push({ name: section.name, mode, ratio, width, mismatches });
        if (width === FONT_WIDTH) {
          heightsOf.set(`${section.name} ${mode} ${ratio}`, await readHeights(page));
        }
        if (ratio === 1) {
          // The drawing, with the requests the page made, before the fonts: reading the faces
          // through DevTools asks for the page again (measured), a request of the judge's, not the
          // page's, which the drawing's check of failed requests must not count.
          drawn = await drawing(page, authority, mode, width);
          if (width === FONT_WIDTH) {
            const { refusals, runs } = await pageAndFonts(page, mode, width);
            fontRefusals.push(...refusals);
            runsOf.set(`${section.name} ${mode}`, runs);
          }
        }
      } finally {
        await page.close();
      }
      if (drawn === undefined || t === undefined) continue;
      // T(div)'s drawing at the same width, for the second drawing and (at 813 px) its fonts.
      const tPage = await open(browser, t, mode, width);
      try {
        const tDrawn = await drawing(tPage, t, mode, width);
        if (width === FONT_WIDTH) {
          for (const refusal of (await pageAndFonts(tPage, mode, width)).refusals) {
            fontRefusals.push(`T(div) ${refusal}`);
          }
        }
        secondDrawings += 2;
        const problems = [
          ...drawn.page,
          ...tDrawn.page.map((problem) => `T(div) ${problem}`),
          ...[
            ...compareDrawings(drawn.drawing, tDrawn.drawing),
            ...comparePictureLists(drawn.pictures, tDrawn.pictures),
          ].map(
            ({ property, authority: a, t: b }) =>
              `second-drawing ${property}: ${a.slice(0, 120)} / ${b.slice(0, 120)}`,
          ),
        ];
        if (problems.length > 0) {
          drawingFailures.push(`${section.name} (${mode}, ${width} px): ${problems.join("; ")}`);
        }
      } finally {
        await tPage.close();
      }
    }
    if (fontRefusals.length > 0)
      fonts.push(`${section.name} (${mode}): ${fontRefusals.join("; ")}`);
  }
  push(judgedR3, ratio, section.name);
  push(judgedBoxes, ratio, section.name);
  if (ratio === 1) {
    judgedFonts.push(section.name);
    judgedDrawings.push(section.name);
  }
}

// A section T or the scanner refuses, at ratio 1: its page and fonts, reported by refusal.
async function refused(browser: Browser, section: Section): Promise<void> {
  for (const mode of MODES) {
    pages += 1;
    const page = await openPage(browser.cdp, { div: section.div, mode, width: FONT_WIDTH });
    try {
      for (const refusal of (await pageAndFonts(page, mode, FONT_WIDTH)).refusals) {
        const code = refusal.split(":")[0] ?? "";
        reported.set(code, (reported.get(code) ?? 0) + 1);
      }
    } finally {
      await page.close();
    }
  }
}

async function sweep(ratio: number): Promise<void> {
  const browser = launchChrome({ executable: EXECUTABLE, ratio, noSandbox: NO_SANDBOX });
  try {
    for (const section of all) {
      if (section.carried) await carried(browser, section, ratio);
      else if (ratio === 1) await refused(browser, section);
    }
  } finally {
    await browser.close();
  }
}

// The ratios, `parallel` browsers at a time; every one is waited for before a failure is thrown.
const queue = [...ratios];
const errors: unknown[] = [];
await Promise.all(
  Array.from({ length: Math.min(parallel, queue.length) }, async () => {
    for (let ratio = queue.shift(); ratio !== undefined; ratio = queue.shift()) {
      try {
        await sweep(ratio);
      } catch (error) {
        errors.push(error);
        queue.length = 0;
      }
    }
  }),
);
if (errors.length > 0) throw errors[0];

// The boxes, from the faces bound at ratio 1.
let boxes = 0;
for (const ratio of ratios) {
  for (const section of all) {
    if (!section.carried) continue;
    for (const mode of MODES) {
      const heights = heightsOf.get(`${section.name} ${mode} ${ratio}`);
      if (heights === undefined) continue;
      boxes += heights.reduce((sum, { heights: list }) => sum + list.length, 0);
      const wrong = checkHeights(
        runsOf.get(`${section.name} ${mode}`) ?? [],
        heights,
        faces,
        ratio,
      );
      if (wrong.length > 0) {
        boxFailures.push(
          `${section.name} (${mode}, ratio ${ratio}): ${wrong
            .map(({ detail }) => detail)
            .slice(0, 3)
            .join("; ")}`,
        );
      }
    }
  }
}

// Each check judged every carried section (at every ratio it draws).
const counts = [
  ...ratios.map((ratio) => carriedMismatch(`R3 at ${ratio}`, judgedR3.get(ratio) ?? [], all)),
  ...ratios.map((ratio) => carriedMismatch(`boxes at ${ratio}`, judgedBoxes.get(ratio) ?? [], all)),
  carriedMismatch("fonts and page", judgedFonts, all),
  carriedMismatch("second drawing", judgedDrawings, all),
].filter((mismatch): mismatch is string => mismatch !== undefined);

const out = option("out");
if (out !== undefined) {
  writeFileSync(
    out,
    `${JSON.stringify({ pages, sections: all.length, notDrawn, r3, fonts, boxes: boxFailures, drawings: drawingFailures, counts }, null, 2)}\n`,
  );
}

const carriedCount = all.filter((section) => section.carried).length;
let failed = counts.length > 0 || notDrawn.length > 0;
if (failed) console.error([...notDrawn, ...counts].join("\n"));
if (r3.length > 0) {
  failed = true;
  for (const failure of r3.slice(0, 40)) {
    console.error(
      `${failure.name} (${failure.mode}, ${failure.width} px, ratio ${failure.ratio}): ` +
        failure.mismatches
          .slice(0, 6)
          .map(
            ({ key, property, model, chrome }) =>
              `#${key} ${property} model ${model} chrome ${chrome}`,
          )
          .join("; "),
    );
  }
  console.error(`R3: ${r3.length} of ${r3Drawings} drawings mismatch`);
} else {
  console.log(
    `R3: ${carriedCount} carried sections and cases, ${r3Drawings} drawings, T's model equals Chrome's computed style in every one`,
  );
}
for (const [check, failures] of [
  ["fonts and page", fonts],
  ["boxes", boxFailures],
  ["second drawing", drawingFailures],
] as const) {
  if (failures.length === 0) continue;
  failed = true;
  console.error(failures.slice(0, 60).join("\n"));
  console.error(`${check}: ${failures.length} failures`);
}
if (fonts.length === 0) {
  const summary = [...reported].map(([refusal, count]) => `${refusal} ${count}`).join(", ");
  console.log(
    `fonts and page: ${carriedCount} carried sections and cases pass in both modes, T(div) too; ` +
      `the ${all.length - carriedCount} sections T or the scanner refuse report: ${summary || "none"}`,
  );
}
if (boxFailures.length === 0) {
  console.log(
    `boxes: ${boxes} character boxes of ${carriedCount} carried sections and cases exactly R3's in both modes at ${ratios.length} ratios`,
  );
}
if (drawingFailures.length === 0) {
  console.log(
    `second drawing: ${carriedCount} carried sections and cases, ${secondDrawings} drawings at ${WIDTHS.length} widths in both modes, ` +
      `the same text, list numbers, grids and pictures as T(div)'s`,
  );
}
console.log(`pages: ${pages} drawn at ${ratios.length} ratios, each read by every check`);
if (failed) process.exit(1);
