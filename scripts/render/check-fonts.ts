import { launchChrome } from "../../src/render/cdp.js";
import { checkTextNode, loadFaces } from "../../src/render/fonts.js";
import { readHeights, readPage, readRuns } from "../../src/render/measure.js";
import { checkHeights, checkPage } from "../../src/render/page-checks.js";
import { openPage, type Mode } from "../../src/render/page.js";
import { transformSection } from "../../src/authority/t/transform.js";
import { REFUSAL_CASES } from "../../test/fixtures/render/refusal-cases.js";
import { EXECUTABLE, FONTS, NO_SANDBOX } from "./sections.js";

// The seeded refusals of R2's page, R3's fonts and scripts, R6's coverage and R3's character
// boxes (docs/design/authority-import-renderer.md, Delivery 3c-B2a), in the renderer image: every
// case of test/fixtures/render/refusal-cases.ts drawn at 813 px and ratio 1, and refused as stated
// in each mode it names (both, unless it names one), each mode on its own. The sections
// themselves are scripts/render/check.ts's.
//
// usage (inside the image): node --import tsx scripts/render/check-fonts.ts

const WIDTH = 813;
const MODES: readonly Mode[] = ["html", "xml"];

const faces = loadFaces(FONTS);
const failures: string[] = [];
let judged = 0;
const browser = launchChrome({ executable: EXECUTABLE, ratio: 1, noSandbox: NO_SANDBOX });
try {
  for (const seeded of REFUSAL_CASES) {
    const drawn = seeded.drawing === "t" ? transformSection(seeded.div).div : seeded.div;
    for (const mode of seeded.modes ?? MODES) {
      const refusals: string[] = [];
      const page = await openPage(browser.cdp, { div: drawn, mode, width: WIDTH });
      try {
        for (const { refusal, detail } of checkPage(await readPage(page), WIDTH, mode)) {
          refusals.push(`${refusal}: ${detail}`);
        }
        // A page that did not parse has nothing of the section's to read.
        if (!refusals.some((refusal) => refusal.startsWith("parsererror"))) {
          const runs = await readRuns(page);
          for (const run of runs) {
            for (const { refusal, detail } of checkTextNode(run, faces)) {
              refusals.push(`${refusal}: ${detail}`);
            }
          }
          // The box check runs on carried sections at every ratio; a seed of it runs here at 1.
          if (seeded.refusal === "char-height") {
            for (const { refusal, detail } of checkHeights(
              runs,
              await readHeights(page),
              faces,
              1,
            )) {
              refusals.push(`${refusal}: ${detail}`);
            }
          }
        }
      } finally {
        await page.close();
      }
      judged += 1;
      // A box seed must be refused for its height, not for having no face.
      const expected = (refusal: string): boolean =>
        refusal.startsWith(`${seeded.refusal}:`) &&
        (seeded.refusal !== "char-height" || refusal.includes("not the bound face's"));
      if (!refusals.some(expected)) {
        failures.push(
          `seeded ${seeded.name} (${mode}): expected ${seeded.refusal}, got ${refusals.join("; ") || "nothing"}`,
        );
      }
    }
  }
} finally {
  await browser.close();
}

const expectedSeeds = REFUSAL_CASES.reduce(
  (sum, seeded) => sum + (seeded.modes ?? MODES).length,
  0,
);
if (judged !== expectedSeeds) failures.push(`judged ${judged} seeds, not ${expectedSeeds}`);
// A seed judged in no mode would pass undrawn (the type allows none; a cast could).
for (const seeded of REFUSAL_CASES) {
  if ((seeded.modes ?? MODES).length === 0) failures.push(`seeded ${seeded.name}: no mode`);
}
if (failures.length > 0) {
  console.error(failures.join("\n"));
  console.error(`seeded refusals: ${failures.length} failures`);
  process.exit(1);
}
console.log(
  `seeded refusals: ${REFUSAL_CASES.length} cases, ${judged} drawings, each refused as stated in every mode it names`,
);
