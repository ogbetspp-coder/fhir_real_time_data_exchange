import { readFileSync } from "node:fs";

import { transformDocument } from "../../src/authority/t/document.js";
import { TRefusal, transformSection } from "../../src/authority/t/transform.js";
import { xhtmlToText, XhtmlError } from "../../src/fidelity/xhtml.js";
import { launchChrome, type Browser } from "../../src/render/cdp.js";
import { checkTextNode, loadFaces } from "../../src/render/fonts.js";
import { readHeights, readPage, readRuns, type ChromeRun } from "../../src/render/measure.js";
import { calibrationDiv, checkHeights, checkPage, styleKey } from "../../src/render/page-checks.js";
import { openPage, type Mode } from "../../src/render/page.js";
import { T_CASES } from "../../test/fixtures/authority/t-cases.js";
import { MODEL_CASES } from "../../test/fixtures/render/model-cases.js";
import { REFUSAL_CASES } from "../../test/fixtures/render/refusal-cases.js";

// R2's page, R3's fonts and scripts, R6's coverage and R3's character boxes
// (docs/design/authority-import-renderer.md), in the renderer image:
// - every section of every pinned label, T's or not, and every accepted T and model case, drawn
//   in both modes at 813 px and ratio 1: its page and its fonts checked;
// - every section T and the scanner accept, and every case, at every ratio: its character boxes
//   against a calibration page of the same faces and sizes;
// - every seeded case of test/fixtures/render/refusal-cases.ts refused as stated.
// A carried section or a case with any refusal fails the check; a section T refuses only reports
// its refusals (it is withheld or refuses the import anyway).
//
// usage (inside the image): node --import tsx scripts/render/check-fonts.ts [--ratios 1,2]

const EXECUTABLE =
  process.env.RENDERER_CHROME ??
  "/opt/renderer/chrome-headless-shell-linux64/chrome-headless-shell";
const FONTS = process.env.RENDERER_FONTS ?? "/opt/renderer/fonts";
const NO_SANDBOX = process.env.RENDERER_NO_SANDBOX === "1";
const LABELS = "labels/ema-epi";
const ROOT = '<div xmlns="http://www.w3.org/1999/xhtml">';
const MARKED = "<p>not for clinical use</p></div>";
const WIDTH = 813;
const index = process.argv.indexOf("--ratios");
const RATIOS =
  index < 0
    ? [0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.625, 3]
    : (process.argv[index + 1] ?? "1").split(",").map(Number);

type Section = { name: string; div: string; carried: boolean };

function scanned(output: string): boolean {
  try {
    xhtmlToText(output);
    return true;
  } catch (error) {
    if (error instanceof XhtmlError) return false;
    throw error;
  }
}

function sections(): Section[] {
  const found: Section[] = [];
  for (const testCase of T_CASES) {
    if (!("div" in testCase.expected)) continue;
    const div = `${testCase.root ?? ROOT}${testCase.inner}${MARKED}`;
    try {
      const evidence = testCase.evidence === undefined ? undefined : new Set(testCase.evidence);
      if (scanned(transformSection(div, evidence).div))
        found.push({ name: `t-case ${testCase.name}`, div, carried: true });
    } catch (error) {
      if (!(error instanceof TRefusal)) throw error;
    }
  }
  for (const { name, inner } of MODEL_CASES) {
    found.push({ name: `model-case ${name}`, div: `${ROOT}${inner}${MARKED}`, carried: true });
  }
  const lock = JSON.parse(readFileSync(`${LABELS}/sources.lock.json`, "utf8")) as {
    sources: { file: string }[];
  };
  type Node = { text?: { div?: string }; section?: Node[] };
  for (const { file } of lock.sources) {
    const document = JSON.parse(readFileSync(`${LABELS}/sources/${file}`, "utf8")) as {
      entry?: { resource?: { section?: Node[] } }[];
    };
    const placed: { path: string; div: string | undefined }[] = [];
    const walk = (nodes: Node[], base: string): void => {
      nodes.forEach((node, position) => {
        const path = `${base}[${position}]`;
        placed.push({ path, div: node.text?.div });
        walk(node.section ?? [], `${path}.section`);
      });
    };
    walk(document.entry?.[0]?.resource?.section ?? [], "Composition.section");
    const outcomes = transformDocument(placed.map(({ div }) => div));
    placed.forEach(({ path, div }, at) => {
      if (div === undefined) return;
      const outcome = outcomes[at];
      const carried = outcome !== undefined && "div" in outcome && scanned(outcome.div);
      found.push({ name: `${file} ${path}`, div, carried });
    });
  }
  return found;
}

const faces = loadFaces(FONTS);
const all = sections();
const failures: string[] = [];
const reported = new Map<string, number>();

async function pageAndFonts(
  browser: Browser,
  div: string,
  mode: Mode,
): Promise<{ refusals: string[]; runs: ChromeRun[] }> {
  const page = await openPage(browser.cdp, { div, mode, width: WIDTH });
  try {
    const refusals = checkPage(await readPage(page), WIDTH, mode).map(
      ({ refusal, detail }) => `${refusal}: ${detail}`,
    );
    // A page that did not parse has nothing of the section's to read.
    if (refusals.some((refusal) => refusal.startsWith("parsererror")))
      return { refusals, runs: [] };
    const runs = await readRuns(page);
    for (const run of runs) {
      for (const { refusal, detail } of checkTextNode(run, faces))
        refusals.push(`${refusal}: ${detail}`);
    }
    return { refusals, runs };
  } finally {
    await page.close();
  }
}

const first = launchChrome({ executable: EXECUTABLE, ratio: 1, noSandbox: NO_SANDBOX });
const carriedRuns = new Map<string, ChromeRun[]>();
try {
  for (const section of all) {
    for (const mode of ["html", "xml"] as Mode[]) {
      const { refusals, runs } = await pageAndFonts(first, section.div, mode);
      if (mode === "html") carriedRuns.set(section.name, runs);
      if (section.carried && refusals.length > 0)
        failures.push(`${section.name} (${mode}): ${refusals.join("; ")}`);
      if (!section.carried)
        for (const refusal of refusals)
          reported.set(
            refusal.split(":")[0] ?? "",
            (reported.get(refusal.split(":")[0] ?? "") ?? 0) + 1,
          );
    }
  }
  for (const seeded of REFUSAL_CASES) {
    const refusals = [
      ...(await pageAndFonts(first, seeded.div, "html")).refusals,
      ...(await pageAndFonts(first, seeded.div, "xml")).refusals,
    ];
    if (!refusals.some((refusal) => refusal.startsWith(`${seeded.refusal}:`))) {
      failures.push(
        `seeded ${seeded.name}: expected ${seeded.refusal}, got ${refusals.join("; ") || "nothing"}`,
      );
    }
  }
} finally {
  await first.close();
}

let boxes = 0;
for (const ratio of RATIOS) {
  const browser = launchChrome({ executable: EXECUTABLE, ratio, noSandbox: NO_SANDBOX });
  try {
    for (const section of all.filter(({ carried }) => carried)) {
      const runs = carriedRuns.get(section.name) ?? [];
      const keys = [...new Set(runs.map(styleKey))];
      if (keys.length === 0) continue;
      const calibrationPage = await openPage(browser.cdp, {
        div: calibrationDiv(keys),
        mode: "html",
        width: WIDTH,
      });
      const measured = await calibrationPage.evaluate<number[]>(
        `(${JSON.stringify(keys.length)} > 0) ? Array.from({ length: ${keys.length} }, (_, i) => { const n = document.getElementById("c" + i).firstChild; const r = new Range(); r.setStart(n, 0); r.setEnd(n, 1); return r.getClientRects()[0].height; }) : []`,
      );
      await calibrationPage.close();
      const calibration = new Map(keys.map((key, at) => [key, measured[at] ?? -1]));
      const page = await openPage(browser.cdp, { div: section.div, mode: "html", width: WIDTH });
      try {
        const heights = await readHeights(page);
        boxes += heights.reduce((sum, { heights: list }) => sum + list.length, 0);
        const wrong = checkHeights(runs, heights, calibration);
        if (wrong.length > 0)
          failures.push(
            `${section.name} (ratio ${ratio}): ${wrong
              .map(({ detail }) => detail)
              .slice(0, 3)
              .join("; ")}`,
          );
      } finally {
        await page.close();
      }
    }
  } finally {
    await browser.close();
  }
}

const refusedSummary =
  [...reported].map(([refusal, count]) => `${refusal} ${count}`).join(", ") || "none";
if (failures.length > 0) {
  console.error(failures.slice(0, 60).join("\n"));
  console.error(`fonts and page: ${failures.length} failures`);
  process.exit(1);
}
console.log(
  `fonts and page: ${all.filter(({ carried }) => carried).length} carried sections and cases pass in both modes; ` +
    `${REFUSAL_CASES.length} seeded refusals caught; ${boxes} character boxes equal their calibration at ${RATIOS.length} ratios; ` +
    `sections T refuses report: ${refusedSummary}`,
);
