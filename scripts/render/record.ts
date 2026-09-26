import { writeFileSync } from "node:fs";

import { launchChrome } from "../../src/render/cdp.js";
import { readElements, readMarkers, readTexts } from "../../src/render/measure.js";
import { openPage, type Mode } from "../../src/render/page.js";
import { MODEL_CASES } from "../../test/fixtures/render/model-cases.js";

// Records what Chrome reports for the synthetic model cases, for the judge's unit tests
// (test/render/compare-style.test.ts), so `npm run check` needs no browser (the renderer note's
// Verification). Computed styles do not depend on the fonts; the recording names the browser
// version it was made with.
//
// usage: RENDERER_CHROME=<chrome-headless-shell> node --import tsx scripts/render/record.ts

const EXECUTABLE =
  process.env.RENDERER_CHROME ??
  "/opt/renderer/chrome-headless-shell-linux64/chrome-headless-shell";
const ROOT = '<div xmlns="http://www.w3.org/1999/xhtml">';
const MARKED = "<p>not for clinical use</p></div>";
const RATIO = 1.25;

const browser = launchChrome({
  executable: EXECUTABLE,
  ratio: RATIO,
  noSandbox: process.env.RENDERER_NO_SANDBOX === "1",
});
const recorded: Record<string, unknown> = {};
try {
  const { product } = (await browser.cdp.send("Browser.getVersion")) as { product: string };
  recorded.browser = product;
  recorded.ratio = RATIO;
  for (const modelCase of MODEL_CASES) {
    for (const mode of ["html", "xml"] as Mode[]) {
      const page = await openPage(browser.cdp, {
        div: `${ROOT}${modelCase.inner}${MARKED}`,
        mode,
        width: 813,
      });
      recorded[`${modelCase.name} ${mode}`] = {
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
writeFileSync("test/fixtures/render/recorded.json", `${JSON.stringify(recorded, null, 1)}\n`);
