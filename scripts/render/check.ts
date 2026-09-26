import { readFileSync, writeFileSync } from "node:fs";

import { transformDocument } from "../../src/authority/t/document.js";
import { modelDocument, modelSection, type Model } from "../../src/authority/t/model.js";
import { TRefusal, transformSection } from "../../src/authority/t/transform.js";
import { xhtmlToText } from "../../src/fidelity/xhtml.js";
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

function scannerAccepts(div: string, evidence?: ReadonlySet<string>): boolean {
  try {
    xhtmlToText(transformSection(div, evidence).div);
    return true;
  } catch {
    return false;
  }
}

function cases(): Case[] {
  const found: Case[] = [];
  for (const testCase of T_CASES) {
    if (!("div" in testCase.expected)) continue;
    const div = `${testCase.root ?? ROOT}${testCase.inner}${MARKED}`;
    try {
      const evidence = testCase.evidence === undefined ? undefined : new Set(testCase.evidence);
      const model = JSON.parse(modelSection(div, evidence)) as Model;
      if (scannerAccepts(div, evidence))
        found.push({ name: `t-case ${testCase.name}`, div, model });
    } catch (error) {
      if (!(error instanceof TRefusal)) throw error;
    }
  }
  for (const modelCase of MODEL_CASES) {
    const div = `${ROOT}${modelCase.inner}${MARKED}`;
    // Every one is accepted by T and the scanner (test/render/model.test.ts); a refusal here is a
    // broken fixture, which fails loudly.
    found.push({
      name: `model-case ${modelCase.name}`,
      div,
      model: JSON.parse(modelSection(div)) as Model,
    });
  }
  const lock = JSON.parse(readFileSync(`${LABELS}/sources.lock.json`, "utf8")) as {
    sources: { file: string }[];
  };
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
    const models = modelDocument(placed.map(({ div }) => div));
    const outcomes = transformDocument(placed.map(({ div }) => div));
    placed.forEach(({ path, div }, index) => {
      const model = models[index];
      if (div === undefined || model === undefined) return;
      const outcome = outcomes[index];
      if (outcome === undefined || !("div" in outcome)) return;
      try {
        xhtmlToText(outcome.div);
      } catch {
        return;
      }
      found.push({ name: `${file} ${path}`, div, model: JSON.parse(model) as Model });
    });
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
  const browser = launchChrome({ executable: EXECUTABLE, ratio, noSandbox: true });
  try {
    for (const { name, div, model } of all) {
      for (const mode of modes) {
        for (const width of widthsAt(ratio)) {
          const page = await openPage(browser.cdp, { div, mode, width });
          try {
            const elements = await readElements(page);
            const markers =
              model.markers.length > 0 ? await readMarkers(page) : new Map<number, string>();
            const mismatches = compareModel(model, elements, markers, ratio);
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
