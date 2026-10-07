import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

import { loadEmaMapping, type TitleRule } from "../../src/fhir/mapping.js";
import { importPublication } from "../../src/authority/import.js";
import { importCertifiedWord } from "../../src/certified-word/import.js";
import {
  RUN as CERTIFIED_WORD_RUN,
  caseRequest,
  recomputed,
  recomputedCases,
} from "../../src/certified-word/vectors.js";
import { syntheticPublication } from "../../src/authority/synthetic.js";
import type { CanonicalSubmission } from "../../src/contracts/index.js";
import type { FidelityReport } from "../../src/fidelity/index.js";
import {
  hasValidationErrors,
  validateCanonicalPreflight,
  validateEmaPreflight,
} from "../../src/fhir/preflight.js";
import { toProvenanceResource } from "../../src/fhir/provenance.js";
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
// Each case ends with the Provenance a document run persists with it, built by
// toProvenanceResource from the committed contract fixtures (test/fixtures/contracts/): the drawn,
// attested submission for the first case, the authority import for the second, each pointing at
// its case's EMA Bundle and Composition.
//
// A third is the repository's own definitions, fhir/generated/ (the code systems, value sets,
// extension, ConceptMap and StructureMap the package carries), as committed, against the base R5
// definitions only: no profile applies to them.
//
// Each case's resources and profiles are the pipeline's own (officialValidationTargets in
// src/pipeline.ts), so a resource or a profile the worker adds is validated here too. The set is
// five files per case, and the definitions.
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

const fixture = async <T>(name: string): Promise<T> =>
  JSON.parse(await readFile(path.resolve("test/fixtures/contracts", name), "utf8")) as T;
const provenanceFixtures = {
  type2: {
    submission: await fixture<CanonicalSubmission>("canonical-submission.json"),
    report: await fixture<FidelityReport>("fidelity-report.json"),
  },
  type1: {
    submission: await fixture<CanonicalSubmission>("canonical-submission-type1.json"),
    report: await fixture<FidelityReport>("fidelity-report-type1.json"),
    fetchedAt: "2026-09-24T12:00:00Z",
  },
};

// The resources a document run of `source` sends to the validator, after the preflights the
// worker runs.
type ProvenanceOf = { submission: CanonicalSubmission; report: FidelityReport; fetchedAt?: string };

function caseOf(
  source: FhirBundle,
  graphType: "type1" | "type2",
  files: { source: string; suffix: string },
  provenanceOf: ProvenanceOf,
  titles: TitleRule = "template",
): SetEntry[] {
  const sourcePreflight = validateCanonicalPreflight(source, graphType);
  if (hasValidationErrors(sourcePreflight)) {
    throw new Error(
      `Canonical ${graphType} preflight failed (${sourcePreflight.issue.length} issues); the worker would refuse this source before official validation`,
    );
  }
  const target = transformType2ToEma(source, mapping, undefined, titles);
  const emaPreflight = validateEmaPreflight(target.list, target.documentBundle, mapping, titles);
  if (hasValidationErrors(emaPreflight)) {
    throw new Error(
      `EMA structural preflight failed (${emaPreflight.issue.length} issues); the worker would refuse this transform before official validation`,
    );
  }
  const { submission, report, ...fetched } = provenanceOf;
  const provenance = toProvenanceResource(submission, report, {
    bundleId: target.documentBundle.id ?? "",
    compositionId: target.documentBundle.entry[0]?.resource.id ?? "",
    ...fetched,
  });
  return officialValidationTargets(source, target, mapping, provenance).map(
    ({ name, resource, profiles }) => ({
      file: name === "source" ? files.source : `${name}${files.suffix}.json`,
      resource,
      profiles,
    }),
  );
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
// A certified Word source's Type 1 record (ADR 0006 P4, D1), from the first synthetic label's
// recompute, with its own Provenance; its titles are carried as written, as the pipeline carries
// them.
const [label] = recomputedCases();
if (label === undefined) throw new Error("no recomputed Word label");
const word = importCertifiedWord(
  recomputed(label.name),
  caseRequest(label),
  mapping,
  CERTIFIED_WORD_RUN,
);

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
  ...caseOf(
    createSyntheticType2Bundle(mapping, { product: SMOKE_PRODUCT_ID }),
    "type2",
    { source: "source-type2.json", suffix: "" },
    provenanceFixtures.type2,
  ),
  ...caseOf(
    imported.submission.bundle as unknown as FhirBundle,
    "type1",
    { source: "source-type1.json", suffix: "-type1" },
    provenanceFixtures.type1,
  ),
  ...caseOf(
    word.submission.bundle as unknown as FhirBundle,
    "type1",
    { source: "source-certified-word.json", suffix: "-certified-word" },
    { submission: word.submission, report: word.fidelityReport },
    "as-written",
  ),
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
