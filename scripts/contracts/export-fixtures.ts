import { mkdir, readFile, readdir, unlink, writeFile } from "node:fs/promises";
import path from "node:path";

import { format, resolveConfig } from "prettier";

import { importPublication } from "../../src/authority/import.js";
import { syntheticPublication } from "../../src/authority/synthetic.js";
import { RUN_MANIFEST_VERSION, type RunRequest } from "../../src/contracts/index.js";
import { loadEmaMapping } from "../../src/fhir/mapping.js";
import {
  SYNTHETIC_SUBMISSION_URI,
  createSyntheticSubmission,
} from "../../src/fixtures/synthetic-submission.js";
import { sha256 } from "../../src/lib/hash.js";
import { RUN as IMPORT_RUN } from "../../test/authority/support.js";
import {
  arabicIndic,
  documentVerdicts,
  fullwidth,
  primitiveVerdicts,
  withNewline,
  type DocumentBase,
} from "./contract-verdicts.js";

// Exports the contract instances a Zone A re-implementation needs as its oracle. They are
// synthetic by construction (src/fixtures/synthetic-submission.ts builds them from the synthetic
// Type 2 fixture, src/authority/synthetic.ts the synthetic publication), so committing them leaks
// nothing:
//
// - the canonical submission, its fidelity report, its extracted page text, and the run request
//   that names it (the default product, whose bytes are frozen);
// - the same three for an authority import of the synthetic publication, a Type 1 record with an
//   `authority-publication` source and approval (audit C-8);
// - the smoke product's submission, whose strength is a decimal, so that a non-integer number is
//   hashed by both languages (audit C-10);
// - the contract verdict corpus (./contract-verdicts.ts, audit C-7);
// - the canonical JSON of doubles across every range JavaScript formats differently (audit C-10).
//
// Run manifests are not exported here: each version's are the manifests its own code emitted,
// kept in test/fixtures/run-manifest/ and never regenerated.
//
// Deterministic like scripts/contracts/generate-schemas.ts: no clock, no randomness beyond a
// fixed seed, Prettier formatting resolved from the repository config so `format:check` and
// `contracts:check` agree byte-for-byte. Every file is rewritten on every run, which is what
// scripts/ci/check-generated.mjs requires of a generator.

const output = path.resolve("test/fixtures/contracts");
await mkdir(output, { recursive: true });
const prettierOptions = { ...(await resolveConfig(output)), parser: "json" as const };

const mapping = await loadEmaMapping();
const { submission, fidelityReport, sourceText } = createSyntheticSubmission(mapping);

// A `document` run request naming the submission by location and hash, exactly as ADR 0002
// requires: by reference, never inline.
const runRequest: RunRequest = {
  source: "document",
  submissionRef: { uri: SYNTHETIC_SUBMISSION_URI, sha256: sha256(submission) },
};

const publication = syntheticPublication(mapping);
const imported = importPublication(publication.request, publication, mapping, IMPORT_RUN);

const smoke = createSyntheticSubmission(mapping, { product: "synthetic-smoketest" });

const manifest = async (kind: "fixture" | "document"): Promise<unknown> =>
  JSON.parse(
    await readFile(
      path.resolve("test/fixtures/run-manifest", `${RUN_MANIFEST_VERSION}-${kind}.json`),
      "utf8",
    ),
  ) as unknown;
const fixtureManifest = await manifest("fixture");
const documentManifest = await manifest("document");

const bases: DocumentBase[] = [
  {
    contract: "canonical-submission",
    base: "contracts/canonical-submission.json",
    document: submission,
    mutations: [
      {
        name: "bundleSha256 newline",
        path: ["bundleSha256"],
        value: withNewline(submission.bundleSha256),
      },
      { name: "createdAt newline", path: ["createdAt"], value: withNewline(submission.createdAt) },
      { name: "graphType upper case", path: ["graphType"], value: "TYPE2" },
      { name: "submissionId null", path: ["submissionId"], value: null },
      {
        name: "span page as a string",
        path: ["provenance", "sections", 0, "spans", 0, "page"],
        value: "1",
      },
      {
        name: "span page as a boolean",
        path: ["provenance", "sections", 0, "spans", 0, "page"],
        value: true,
      },
      {
        name: "normalizationVersion fullwidth digits",
        path: ["provenance", "fidelity", "normalizationVersion"],
        value: fullwidth(submission.provenance.fidelity.normalizationVersion),
      },
      {
        name: "sectionsChecked as a float",
        path: ["provenance", "fidelity", "sectionsChecked"],
        value: 1.5,
      },
      { name: "optional model null", path: ["provenance", "extraction", "model"], value: null },
      {
        name: "approvedAt newline",
        path: ["approval", "approvedAt"],
        value: withNewline("2026-09-19T00:00:00Z"),
      },
    ],
  },
  {
    contract: "canonical-submission",
    base: "contracts/canonical-submission-type1.json",
    document: imported.submission,
    mutations: [
      {
        name: "document byteLength as a string",
        path: ["provenance", "sourceDocument", "document", "byteLength"],
        value: "12",
      },
      {
        name: "document id newline",
        path: ["provenance", "sourceDocument", "document", "id"],
        value: withNewline(
          (imported.submission.provenance.sourceDocument as { document: { id: string } }).document
            .id,
        ),
      },
      {
        name: "requestedAt Arabic-Indic digits",
        path: ["approval", "requestedAt"],
        value: arabicIndic("2026-09-24T11:59:00Z"),
      },
      { name: "authority null", path: ["approval", "authority"], value: null },
    ],
  },
  {
    contract: "ingestion-provenance",
    base: "contracts/canonical-submission.json#provenance",
    document: submission.provenance,
    mutations: [
      { name: "sections empty", path: ["sections"], value: [] },
      {
        name: "narrativeDivSha256 newline",
        path: ["sections", 0, "narrativeDivSha256"],
        value: withNewline(submission.provenance.sections[0]?.narrativeDivSha256),
      },
      { name: "decision target null", path: ["decisions", 0, "sourceKey"], value: null },
    ],
  },
  {
    contract: "fidelity-report",
    base: "contracts/fidelity-report.json",
    document: fidelityReport,
    mutations: [
      { name: "summary.total as a boolean", path: ["summary", "total"], value: true },
      { name: "summary.total as a string", path: ["summary", "total"], value: "3" },
      { name: "summary.total as a float", path: ["summary", "total"], value: 3.5 },
      {
        name: "normalizationVersion Arabic-Indic digits",
        path: ["normalizationVersion"],
        value: arabicIndic(fidelityReport.normalizationVersion),
      },
      {
        name: "extractedTextSha256 newline",
        path: ["extractedTextSha256"],
        value: withNewline(fidelityReport.extractedTextSha256),
      },
      { name: "section reason null", path: ["sections", 0, "reason"], value: null },
      {
        name: "section path Arabic-Indic digits",
        path: ["sections", 0, "path"],
        value: arabicIndic(fidelityReport.sections[0]?.path),
      },
      { name: "status upper case", path: ["status"], value: "PASSED" },
    ],
  },
  {
    contract: "source-document-text",
    base: "contracts/source-document-text.json",
    document: sourceText,
    mutations: [
      { name: "page as a string", path: ["pages", 0, "page"], value: "1" },
      { name: "page as a boolean", path: ["pages", 0, "page"], value: true },
      { name: "bodyStart negative", path: ["pages", 0, "bodyStart"], value: -1 },
      { name: "extractorVersion null", path: ["extractorVersion"], value: null },
      {
        name: "extractorVersion newline",
        path: ["extractorVersion"],
        value: withNewline(sourceText.extractorVersion),
      },
    ],
  },
  {
    contract: "run-request",
    base: "contracts/run-request.json",
    document: runRequest,
    mutations: [
      { name: "runId null", path: ["runId"], value: null },
      {
        name: "sha256 newline",
        path: ["submissionRef", "sha256"],
        value: withNewline(runRequest.submissionRef.sha256),
      },
      {
        name: "uri newline",
        path: ["submissionRef", "uri"],
        value: withNewline(SYNTHETIC_SUBMISSION_URI),
      },
    ],
  },
  {
    contract: "run-manifest",
    base: `run-manifest/${RUN_MANIFEST_VERSION}-fixture.json`,
    document: fixtureManifest,
    mutations: [
      { name: "sourceCommit not a commit id", path: ["runtime", "sourceCommit"], value: "local" },
      {
        name: "imageDigest upper case",
        path: ["runtime", "imageDigest"],
        value: `sha256:${"A".repeat(64)}`,
      },
      { name: "dryRun as a string", path: ["dryRun"], value: "true" },
      {
        name: "officialValidationExecuted as a number",
        path: ["validation", "officialValidationExecuted"],
        value: 0,
      },
      {
        name: "preflightErrors as a boolean",
        path: ["validation", "preflightErrors"],
        value: false,
      },
      { name: "qrdTemplate with a space", path: ["standards", "qrdTemplate"], value: "10.4 " },
      {
        name: "a fixture run with an ingestion block",
        path: ["ingestion"],
        value: (documentManifest as { ingestion: unknown }).ingestion,
      },
    ],
  },
  {
    contract: "run-manifest",
    base: `run-manifest/${RUN_MANIFEST_VERSION}-document.json`,
    document: documentManifest,
    mutations: [
      { name: "a document run without its ingestion block", path: ["ingestion"], delete: true },
      {
        name: "a drawn source that records an authority fetch",
        path: ["ingestion", "authority"],
        value: {
          importerVersion: "2.2.0",
          fetched: [
            {
              url: "https://epi.example/a",
              sha256: "0".repeat(64),
              byteLength: 1,
              fetchedAt: "2026-09-28T00:00:00Z",
            },
            {
              url: "https://epi.example/b",
              sha256: "1".repeat(64),
              byteLength: 1,
              fetchedAt: "2026-09-28T00:00:00Z",
            },
          ],
        },
      },
      { name: "modelId null", path: ["ingestion", "modelId"], value: null },
    ],
  },
];

const verdicts = {
  description:
    "Zod's verdicts on values and documents, which every Python reader of contracts/generated/ must reproduce. Generated by scripts/contracts/export-fixtures.ts (scripts/contracts/contract-verdicts.ts).",
  primitives: primitiveVerdicts(),
  documents: documentVerdicts(bases),
};

// Doubles across every range where JavaScript's number formatting (RFC 8785, section 3.2.2.3:
// ECMAScript's Number::toString) switches notation or rounds, and a seeded sweep of bit
// patterns. Each is carried by its IEEE 754 bits, so no JSON parser rounds it on the way.
function bits(value: number): string {
  const view = new DataView(new ArrayBuffer(8));
  view.setFloat64(0, value);
  return [...new Uint8Array(view.buffer)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function fromBits(high: number, low: number): number {
  const view = new DataView(new ArrayBuffer(8));
  view.setUint32(0, high);
  view.setUint32(4, low);
  return view.getFloat64(0);
}

// mulberry32: a fixed seed gives the same sweep on every machine.
function generator(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return (t ^ (t >>> 14)) >>> 0;
  };
}

const edges = [
  0,
  -0,
  1,
  -1,
  2.5,
  0.1,
  0.2,
  0.1 + 0.2,
  1 / 3,
  -2.5e-7,
  1e-6,
  1e-7,
  1.5e-7,
  123e-20,
  1e20,
  1e21,
  1.5e21,
  9007199254740991,
  9007199254740992,
  2 ** 53 + 2,
  2 ** 64,
  4.35,
  0.000001,
  0.0000001,
  5e-324,
  2.2250738585072014e-308,
  Number.MAX_VALUE,
  Number.MIN_VALUE,
  333333333.3333333,
  1e23,
  5e-7,
  1.7976931348623157e308,
  295147905179352830000,
  Number("12345678901234567890"),
  100,
  1e2,
  2.5,
];
const random = generator(20260928);
const sweep: number[] = [];
while (sweep.length < 1000) {
  const value = fromBits(random(), random());
  if (Number.isFinite(value)) sweep.push(value);
}
const numbers = {
  description:
    "Doubles by their IEEE 754 bits (big-endian hex) and the canonical JSON JavaScript writes for each (JSON.stringify, RFC 8785 section 3.2.2.3). Generated by scripts/contracts/export-fixtures.ts; zone-a/tests/test_canonical_json_parity.py reproduces every one.",
  cases: [...edges, ...sweep].map((value) => ({
    bits: bits(value),
    canonical: JSON.stringify(value),
  })),
};

const files: Record<string, unknown> = {
  "canonical-submission.json": submission,
  "fidelity-report.json": fidelityReport,
  "source-document-text.json": sourceText,
  "run-request.json": runRequest,
  "canonical-submission-type1.json": imported.submission,
  "fidelity-report-type1.json": imported.fidelityReport,
  "source-document-text-type1.json": imported.sourceText,
  "canonical-submission-decimal.json": smoke.submission,
  "contract-verdicts.json": verdicts,
  "canonical-json-numbers.json": numbers,
};

for (const [file, value] of Object.entries(files)) {
  await writeFile(
    path.join(output, file),
    await format(JSON.stringify(value, null, 2), prettierOptions),
  );
}

// Written into the same directory by another generator (scripts/contracts/
// export-quote-edge-cases.ts), so not this one's to remove.
const ownedElsewhere = new Set(["quote-edge-cases.json"]);

// A removed fixture must not leave its old file behind for a re-implementation to read.
for (const name of await readdir(output)) {
  if (name.endsWith(".json") && files[name] === undefined && !ownedElsewhere.has(name)) {
    await unlink(path.join(output, name));
  }
}

console.log(`Exported ${Object.keys(files).length} contract fixtures in ${output}`);
