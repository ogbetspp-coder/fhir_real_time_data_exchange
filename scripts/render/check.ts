import { readFileSync, writeFileSync } from "node:fs";

import { transformDocument } from "../../src/authority/t/document.js";
import { modelDocument, modelSection, type Model } from "../../src/authority/t/model.js";
import { TRefusal, transformSection } from "../../src/authority/t/transform.js";
import { xhtmlToText, XhtmlError } from "../../src/fidelity/xhtml.js";
import { launchChrome } from "../../src/render/cdp.js";
import { compareModel, compareText, type Mismatch } from "../../src/render/compare-style.js";
import { readElements, readMarkers, readTexts } from "../../src/render/measure.js";
import { openPage, type Mode } from "../../src/render/page.js";
import { T_CASES } from "../../test/fixtures/authority/t-cases.js";
import { MODEL_CASES } from "../../test/fixtures/render/model-cases.js";

// R3 as a check (docs/design/authority-import-renderer.md, and its model addendum): for every
// accepted T case and every section of every pinned label that T and the scanner both accept, T's
// model against Chrome's computed style, in both modes, at the named widths and every ratio. Any
// mismatch fails. Run inside the renderer image in CI (the Renderer job); locally with
// RENDERER_CHROME set to a chrome-headless-shell of the pinned version.
//
// usage: node --import tsx scripts/render/check.ts [--ratios 1,2] [--modes html,xml]
//          [--widths 813,360,1240] [--out report.json]

const EXECUTABLE =
  process.env.RENDERER_CHROME ??
  "/opt/renderer/chrome-headless-shell-linux64/chrome-headless-shell";
// Chrome's sandbox is off only inside the render container, which has no network, no credentials
// and a read-only workspace (R1, R6); the container's run sets RENDERER_NO_SANDBOX=1.
const NO_SANDBOX = process.env.RENDERER_NO_SANDBOX === "1";
const RATIOS = [0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.625, 3];
const WIDTHS = [813, 360, 1240];
const LABELS = "labels/ema-epi";
const ROOT = '<div xmlns="http://www.w3.org/1999/xhtml">';
const MARKED = "<p>not for clinical use</p></div>";

function option(name: string): string | undefined {
  const index = process.argv.indexOf(`--${name}`);
  return index < 0 ? undefined : process.argv[index + 1];
}
const numbers = (value: string | undefined, fallback: number[]): number[] =>
  value === undefined ? fallback : value.split(",").map(Number);
const ratios = numbers(option("ratios"), RATIOS);
const widths = numbers(option("widths"), WIDTHS);
// A computed style does not depend on the width (M4), so the named widths other than the EMA
// viewer's 813 px are drawn at ratio 1 only, unless --widths names them.
const widthsAt = (ratio: number): number[] =>
  option("widths") !== undefined || ratio === 1 ? widths : widths.filter((width) => width === 813);
const modes = (option("modes")?.split(",") ?? ["html", "xml"]) as Mode[];

type Case = { name: string; div: string; model: Model };

// Whether the scanner accepts a T(div); only its own refusal counts as no.
function scannerAccepts(output: string): boolean {
  try {
    xhtmlToText(output);
    return true;
  } catch (error) {
    if (error instanceof XhtmlError) return false;
    throw error;
  }
}

// Every section R3 draws, and nothing silently skipped: an accepted T case T or the scanner now
// refuses, a model case refused, or a label section T and the scanner accept without a model, all
// fail the check (the first code review).
function cases(): Case[] {
  const found: Case[] = [];
  const broken: string[] = [];
  for (const testCase of T_CASES) {
    if (!("div" in testCase.expected)) continue;
    const div = `${testCase.root ?? ROOT}${testCase.inner}${MARKED}`;
    const evidence = testCase.evidence === undefined ? undefined : new Set(testCase.evidence);
    try {
      if (!scannerAccepts(transformSection(div, evidence).div)) continue;
      found.push({
        name: `t-case ${testCase.name}`,
        div,
        model: JSON.parse(modelSection(div, evidence)) as Model,
      });
    } catch (error) {
      if (!(error instanceof TRefusal)) throw error;
      broken.push(`t-case ${testCase.name}: accepted in t-cases.ts, refused (${error.reason})`);
    }
  }
  for (const modelCase of MODEL_CASES) {
    const div = `${ROOT}${modelCase.inner}${MARKED}`;
    try {
      if (!scannerAccepts(transformSection(div).div)) {
        broken.push(`model-case ${modelCase.name}: the scanner refuses it`);
        continue;
      }
      found.push({
        name: `model-case ${modelCase.name}`,
        div,
        model: JSON.parse(modelSection(div)) as Model,
      });
    } catch (error) {
      if (!(error instanceof TRefusal)) throw error;
      broken.push(`model-case ${modelCase.name}: T refuses it (${error.reason})`);
    }
  }
  const lock = JSON.parse(readFileSync(`${LABELS}/sources.lock.json`, "utf8")) as {
    sources: { file: string }[];
  };
  if (lock.sources.length === 0) broken.push(`${LABELS}/sources.lock.json pins no label`);
  type Section = { text?: { div?: string }; section?: Section[] };
  for (const { file } of lock.sources) {
    const document = JSON.parse(readFileSync(`${LABELS}/sources/${file}`, "utf8")) as {
      entry?: { resource?: { section?: Section[] } }[];
    };
    const placed: { path: string; div: string | undefined }[] = [];
    const walk = (sections: Section[], base: string): void => {
      sections.forEach((section, position) => {
        const path = `${base}[${position}]`;
        placed.push({ path, div: section.text?.div });
        walk(section.section ?? [], `${path}.section`);
      });
    };
    walk(document.entry?.[0]?.resource?.section ?? [], "Composition.section");
    if (placed.length === 0) broken.push(`${file}: no sections`);
    const models = modelDocument(placed.map(({ div }) => div));
    const outcomes = transformDocument(placed.map(({ div }) => div));
    placed.forEach(({ path, div }, index) => {
      const outcome = outcomes[index];
      if (div === undefined || outcome === undefined || !("div" in outcome)) return;
      if (!scannerAccepts(outcome.div)) return;
      const model = models[index];
      if (model === undefined) {
        broken.push(`${file} ${path}: accepted by T and the scanner, but has no model`);
        return;
      }
      found.push({ name: `${file} ${path}`, div, model: JSON.parse(model) as Model });
    });
  }
  if (broken.length > 0) {
    console.error(broken.join("\n"));
    process.exit(1);
  }
  return found;
}

const all = cases();
const failures: {
  name: string;
  mode: Mode;
  ratio: number;
  width: number;
  mismatches: Mismatch[];
}[] = [];
let drawings = 0;
for (const ratio of ratios) {
  const browser = launchChrome({ executable: EXECUTABLE, ratio, noSandbox: NO_SANDBOX });
  try {
    for (const { name, div, model } of all) {
      for (const mode of modes) {
        for (const width of widthsAt(ratio)) {
          const page = await openPage(browser.cdp, { div, mode, width });
          try {
            const elements = await readElements(page);
            const markers =
              model.markers.length > 0 ? await readMarkers(page) : new Map<number, string>();
            const mismatches = compareModel(model, elements, markers, ratio, mode);
            if (mode === "xml") mismatches.push(...compareText(model, await readTexts(page)));
            drawings += 1;
            if (mismatches.length > 0) failures.push({ name, mode, ratio, width, mismatches });
          } finally {
            await page.close();
          }
        }
      }
    }
  } finally {
    await browser.close();
  }
}

const out = option("out");
if (out !== undefined)
  writeFileSync(out, `${JSON.stringify({ drawings, cases: all.length, failures }, null, 2)}\n`);
if (failures.length > 0) {
  for (const failure of failures.slice(0, 40)) {
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
  console.error(`R3: ${failures.length} of ${drawings} drawings mismatch`);
  process.exit(1);
}
console.log(
  `R3: ${all.length} sections, ${drawings} drawings, T's model equals Chrome's computed style in every one`,
);
