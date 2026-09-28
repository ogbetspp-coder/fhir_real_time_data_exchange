import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";

import { launchChrome } from "../../src/render/cdp.js";
import { readElements, readMarkers, readTexts } from "../../src/render/measure.js";
import { openPage, type Mode } from "../../src/render/page.js";
import { MARKED, ROOT } from "../../src/render/sections.js";
import { MODEL_CASES } from "../../test/fixtures/render/model-cases.js";
import { EXECUTABLE, NO_SANDBOX } from "./sections.js";

// Records what Chrome reports for the synthetic model cases, for the judge's unit tests
// (test/render/compare-style.test.ts), so `npm run check` needs no browser (the renderer note's
// Verification). Computed styles do not depend on the fonts; the recording names the browser
// version it was made with, and each case the SHA-256 of the div drawn, so a case changed and not
// recorded again fails the tests. With `--check` (a step of CI's Renderer job) nothing is written:
// the recording must equal the committed one (as JSON: `npm run format` lays the file out).
//
// usage: RENDERER_CHROME=<chrome-headless-shell> node --import tsx scripts/render/record.ts
//   or, in the image: npm run renderer:record (the check)

const RECORDED = "test/fixtures/render/recorded.json";
const RATIO = 1.25;
const check = process.argv.includes("--check");

const browser = launchChrome({ executable: EXECUTABLE, ratio: RATIO, noSandbox: NO_SANDBOX });
const recorded: Record<string, unknown> = {};
try {
  const { product } = (await browser.cdp.send("Browser.getVersion")) as { product: string };
  recorded.browser = product;
  recorded.ratio = RATIO;
  for (const modelCase of MODEL_CASES) {
    const div = `${ROOT}${modelCase.inner}${MARKED}`;
    for (const mode of ["html", "xml"] as Mode[]) {
      const page = await openPage(browser.cdp, { div, mode, width: 813 });
      recorded[`${modelCase.name} ${mode}`] = {
        divSha256: createHash("sha256").update(div, "utf8").digest("hex"),
        elements: await readElements(page),
        markers: [...(await readMarkers(page))],
        texts: await readTexts(page),
      };
      await page.close();
    }
  }
} finally {
  await browser.close();
}
if (!check) {
  writeFileSync(RECORDED, `${JSON.stringify(recorded, null, 2)}\n`);
} else {
  const committed = JSON.parse(readFileSync(RECORDED, "utf8")) as Record<string, unknown>;
  const keys = [...new Set([...Object.keys(committed), ...Object.keys(recorded)])];
  const differing = keys.filter(
    (key) => JSON.stringify(committed[key]) !== JSON.stringify(recorded[key]),
  );
  if (differing.length > 0) {
    console.error(`${RECORDED} is not this browser's recording: ${differing.join(", ")}`);
    process.exit(1);
  }
  console.log(`${RECORDED}: ${keys.length - 2} recordings, each this browser's, of the div named`);
}
