import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
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
import { officialValidationTargets } from "../../src/pipeline.js";
import { SMOKE_PRODUCT_ID } from "../../src/fixtures/synthetic-products.js";
import { createSyntheticType2Bundle } from "../../src/fixtures/synthetic.js";

// Emits the resources the deployed worker sends to the official HL7 validator for a
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
// A third is the published interoperability artifacts, fhir/generated/ (the ConceptMap and the
// StructureMap), as committed, against the base R5 definitions only: no profile applies to them.
//
// Each case's resources and profiles are the pipeline's own (officialValidationTargets in
// src/pipeline.ts), so a resource or a profile the worker adds is validated here too. The set is
// ten files: four per case, and the two artifacts.
//
// usage: tsx scripts/ci/emit-validation-set.ts OUTPUT_DIR

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
  return officialValidationTargets(source, target, mapping).map(({ name, resource, profiles }) => ({
    file: name === "source" ? `source-${graphType}.json` : `${name}${suffix}.json`,
    resource,
    profiles,
  }));
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

// Every committed file, not a fresh generation: `npm run artifacts:check` holds them to the
// generator, and these are the bytes the repository publishes.
const generated = path.resolve("fhir/generated");
const artifactFiles = (await readdir(generated)).filter((file) => file.endsWith(".json")).sort();
if (artifactFiles.length === 0) throw new Error(`${generated} holds no artifacts`);
const artifacts: SetEntry[] = await Promise.all(
  artifactFiles.map(async (file) => ({
    file,
    resource: JSON.parse(await readFile(path.join(generated, file), "utf8")) as FhirResource,
    profiles: [],
  })),
);

const set: SetEntry[] = [
  ...caseOf(createSyntheticType2Bundle(mapping, { product: SMOKE_PRODUCT_ID }), "type2", ""),
  ...caseOf(imported.submission.bundle as unknown as FhirBundle, "type1", "-type1"),
  ...artifacts,
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
