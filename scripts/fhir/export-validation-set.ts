import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

import { loadEmaMapping } from "../../src/fhir/mapping.js";
import { transformType2ToEma } from "../../src/fhir/transform.js";
import type { FhirBundle } from "../../src/fhir/types.js";
import { createSyntheticSmpcFromPublishedType2 } from "../../src/fixtures/synthetic.js";

const [baseArg, destinationArg] = process.argv.slice(2);
if (!baseArg || !destinationArg) {
  throw new Error(
    "Usage: tsx scripts/fhir/export-validation-set.ts PUBLISHED_TYPE2_JSON OUTPUT_DIR",
  );
}

const mapping = await loadEmaMapping();
const base = JSON.parse(await readFile(path.resolve(baseArg), "utf8")) as FhirBundle;
const source = createSyntheticSmpcFromPublishedType2(base, mapping);
const target = transformType2ToEma(source, mapping);
const destination = path.resolve(destinationArg);
const composition = target.documentBundle.entry[0]?.resource;
if (composition === undefined) throw new Error("Target Composition is missing");

await mkdir(destination, { recursive: true });
await Promise.all(
  Object.entries({
    "source-type2.json": source,
    "ema-list.json": target.list,
    "ema-bundle.json": target.documentBundle,
    "ema-composition.json": composition,
  }).map(async ([name, resource]) => {
    await writeFile(path.join(destination, name), `${JSON.stringify(resource, null, 2)}\n`);
  }),
);

console.log(destination);
