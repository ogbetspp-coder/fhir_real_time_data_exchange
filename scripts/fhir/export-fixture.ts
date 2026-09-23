import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

import { loadEmaMapping } from "../../src/fhir/mapping.js";
import {
  createSyntheticSmpcFromPublishedType2,
  createSyntheticType2Bundle,
} from "../../src/fixtures/synthetic.js";
import type { FhirBundle } from "../../src/fhir/types.js";

const destination = process.argv[2];
if (destination === undefined) {
  throw new Error("Usage: tsx scripts/fhir/export-fixture.ts OUTPUT_PATH [PUBLISHED_TYPE2_JSON]");
}

const mapping = await loadEmaMapping();
const basePath = process.argv[3];
const fixture =
  basePath === undefined
    ? createSyntheticType2Bundle(mapping)
    : createSyntheticSmpcFromPublishedType2(
        JSON.parse(await readFile(path.resolve(basePath), "utf8")) as FhirBundle,
        mapping,
      );
await writeFile(path.resolve(destination), `${JSON.stringify(fixture, null, 2)}\n`);
console.log(path.resolve(destination));
