import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import { loadEmaMapping } from "../../src/fhir/mapping.js";
import { importPublication } from "../../src/authority/import.js";
import { syntheticPublication } from "../../src/authority/synthetic.js";
import {
  hasValidationErrors,
  validateCanonicalPreflight,
  validateEmaPreflight,
} from "../../src/fhir/preflight.js";
import { transformType2ToEma } from "../../src/fhir/transform.js";
import type { FhirBundle, FhirResource } from "../../src/fhir/types.js";
import { SMOKE_PRODUCT_ID } from "../../src/fixtures/synthetic-products.js";
import { createSyntheticType2Bundle } from "../../src/fixtures/synthetic.js";

// Emits the four resources the deployed worker sends to the official HL7 validator for a
// `{"source":"fixture"}` run, each paired with the profiles the worker validates it against, so
// scripts/ci/official-validate.mjs can run the pinned validator_cli.jar over exactly that set.
//
// The set is built the way src/app.ts and src/pipeline.ts build it: the fixture is
// `createSyntheticType2Bundle(mapping, { product: SMOKE_PRODUCT_ID })` — the smoke product, which
// is what a `fixture` run actually sends, and not the published-Type-2 variant that the removed
// hand-run exporter scripts/fhir/export-validation-set.ts used; this script supersedes it), the
// target is `transformType2ToEma`, and the Composition is `documentBundle.entry[0]`. The two
// structural preflights run first for the same reason they run first in the worker: a fixture
// that fails them never reaches official validation there, so an emitted set would validate
// something the worker would never send.
//
// A second case is an authority import's Type 1 record (docs/design/authority-import-contract.md,
// D12): the synthetic publication as the importer makes it, and its EMA output.
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

type SetEntry = { file: string; resource: FhirResource; profiles: string[] };

// The resources a run of `source` sends to the validator, after the preflights the worker runs.
function caseOf(source: FhirBundle, graphType: "type1" | "type2", suffix: string): SetEntry[] {
  const sourcePreflight = validateCanonicalPreflight(source, graphType);
  if (hasValidationErrors(sourcePreflight)) {
    throw new Error(
      `Canonical ${graphType} preflight failed (${sourcePreflight.issue.length} issues); the worker would refuse this source before official validation`,
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
  return [
    { file: `source-${graphType}.json`, resource: source, profiles: [GLOBAL_TYPE2_PROFILE] },
    { file: `ema-list${suffix}.json`, resource: target.list, profiles: [mapping.profiles.list] },
    {
      file: `ema-bundle${suffix}.json`,
      resource: target.documentBundle,
      profiles: [mapping.profiles.bundle],
    },
    {
      file: `ema-composition${suffix}.json`,
      resource: composition,
      profiles: mapping.profiles.composition,
    },
  ];
}

const publication = syntheticPublication(mapping);
const imported = importPublication(publication.request, publication, mapping, {
  submissionId: "00000000-0000-4000-8000-000000000001",
  createdAt: "2026-09-24T12:00:00Z",
  extractionRunId: "00000000-0000-4000-8000-000000000002",
  serviceVersion: "validation-set",
  requestedBy: "urn:requester:validation-set",
  requestedAt: "2026-09-24T12:00:00Z",
  sourceTextUri: "gs://validation-set/import.pages.json",
  fidelityReportUri: "gs://validation-set/import.fidelity-report.json",
});

const set: SetEntry[] = [
  ...caseOf(createSyntheticType2Bundle(mapping, { product: SMOKE_PRODUCT_ID }), "type2", ""),
  ...caseOf(imported.submission.bundle as unknown as FhirBundle, "type1", "-type1"),
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
