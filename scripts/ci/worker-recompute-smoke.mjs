// Run inside a built worker image (scripts/ci/worker-recompute-smoke.sh): the certified Word gate's
// own runner (docs/design/certified-word-import.md, D2), with the image's Python and files, makes
// again what `python -m zone_a.recompute` wrote for each committed synthetic label, byte for byte,
// or the same refusal. Any difference exits 1.
//
//   node worker-recompute-smoke.mjs <the folder holding cases.json and each label's .docx and .json>
import { readFileSync } from "node:fs";
import { join } from "node:path";

const { pythonRecompute } = await import("/app/dist/certified-word/recompute.js");
const fixtures = process.argv[2];
const run = pythonRecompute({
  python: process.env.RECOMPUTE_PYTHON,
  root: process.env.ZONE_A_ROOT,
});
const cases = JSON.parse(readFileSync(join(fixtures, "cases.json"), "utf8"));
if (cases.length === 0) {
  console.error("no committed labels to recompute");
  process.exit(1);
}
for (const { name, request } of cases) {
  const outcome = await run(readFileSync(join(fixtures, `${name}.docx`)), request);
  const committed = readFileSync(join(fixtures, `${name}.json`));
  const same =
    "made" in outcome
      ? Buffer.compare(Buffer.from(outcome.made), committed) === 0
      : JSON.parse(committed.toString("utf8")).refusal?.code === outcome.refused;
  if (!same) {
    console.error(`${name}: the worker image recomputes otherwise than committed`);
    process.exit(1);
  }
}
console.log(
  `worker image: zone_a.recompute made ${cases.length} committed labels again, byte for byte`,
);

// The Word drawing's pins, read from the image's working directory as the gate's step 5 reads them
// (src/certified-word/drawing.ts): each environment's lock entry and keys parse, and some key is
// there, so an image without them fails here rather than at a run.
const { drawingPins } = await import("/app/dist/certified-word/drawing.js");
const pins = ["dev", "validation", "prod"].map((environment) => drawingPins(environment));
if (pins.every(({ keys }) => keys.length === 0)) {
  console.error("the worker image carries no Word drawing key");
  process.exit(1);
}
console.log(
  `worker image: the Word drawing pins parse (${pins.map(({ environment, keys }) => `${environment} ${keys.length}`).join(", ")} keys)`,
);
