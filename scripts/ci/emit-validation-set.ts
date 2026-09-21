import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import { loadEmaMapping } from "../../src/fhir/mapping.js";
import {
  hasValidationErrors,
  validateEmaPreflight,
  validateType2Preflight,
} from "../../src/fhir/preflight.js";
import { transformType2ToEma } from "../../src/fhir/transform.js";
import type { FhirResource } from "../../src/fhir/types.js";
import { createSyntheticType2Bundle } from "../../src/fixtures/synthetic.js";

// Emits the four resources the deployed worker sends to the official HL7 validator for a
// `{"source":"fixture"}` run, each paired with the profiles the worker validates it against, so
// scripts/ci/official-validate.mjs can run the pinned validator_cli.jar over exactly that set.
//
// The set is built the way src/app.ts and src/pipeline.ts build it: the fixture is
// `createSyntheticType2Bundle(mapping)` (not the published-Type-2 variant that
// scripts/fhir/export-validation-set.ts uses), the target is `transformType2ToEma`, and the
// Composition is `documentBundle.entry[0]`. The two structural preflights run first for the
// same reason they run first in the worker: a fixture that fails them never reaches official
// validation there, so an emitted set would validate something the worker would never send.
//
// usage: tsx scripts/ci/emit-validation-set.ts OUTPUT_DIR

// The literal in src/pipeline.ts (GLOBAL_TYPE2_PROFILE), which is not exported.
const GLOBAL_TYPE2_PROFILE =
  "http://hl7.org/fhir/uv/emedicinal-product-info/StructureDefinition/Bundle-uv-epi";

export type ValidationSetEntry = {
  file: string;
  resourceType: string;
  profiles: string[];
};

const [destinationArg] = process.argv.slice(2);
if (!destinationArg) {
  throw new Error("Usage: tsx scripts/ci/emit-validation-set.ts OUTPUT_DIR");
}

const mapping = await loadEmaMapping();
const source = createSyntheticType2Bundle(mapping);

const sourcePreflight = validateType2Preflight(source);
if (hasValidationErrors(sourcePreflight)) {
  throw new Error(
    `Canonical Type 2 preflight failed (${sourcePreflight.issue.length} issues); the worker would refuse this fixture before official validation`,
  );
}
const target = transformType2ToEma(source, mapping);
const emaPreflight = validateEmaPreflight(target.list, target.documentBundle, mapping);
if (hasValidationErrors(emaPreflight)) {
  throw new Error(
    `EMA structural preflight failed (${emaPreflight.issue.length} issues); the worker would refuse this transform before official validation`,
  );
}
const composition = target.documentBundle.entry[0]?.resource;
if (composition === undefined) throw new Error("Transformed Composition is missing");

const set: { file: string; resource: FhirResource; profiles: string[] }[] = [
  { file: "source-type2.json", resource: source, profiles: [GLOBAL_TYPE2_PROFILE] },
  { file: "ema-list.json", resource: target.list, profiles: [mapping.profiles.list] },
  { file: "ema-bundle.json", resource: target.documentBundle, profiles: [mapping.profiles.bundle] },
  { file: "ema-composition.json", resource: composition, profiles: mapping.profiles.composition },
];

const destination = path.resolve(destinationArg);
await mkdir(destination, { recursive: true });
await Promise.all(
  set.map(async ({ file, resource }) => {
    await writeFile(path.join(destination, file), `${JSON.stringify(resource, null, 2)}\n`);
  }),
);
const entries: ValidationSetEntry[] = set.map(({ file, resource, profiles }) => ({
  file,
  resourceType: resource.resourceType,
  profiles,
}));
await writeFile(
  path.join(destination, "validation-set.json"),
  `${JSON.stringify(entries, null, 2)}\n`,
);

console.log(destination);
