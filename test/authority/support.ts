import type { ImportRun } from "../../src/authority/import.js";
import { syntheticPublication } from "../../src/authority/synthetic.js";
import type { EmaMapping } from "../../src/fhir/mapping.js";

// A synthetic publication as JSON values, to mutate, and back to the bytes the importer reads.

export type Publication = ReturnType<typeof syntheticPublication>;

export const RUN: ImportRun = {
  submissionId: "00000000-0000-4000-8000-0000000000aa",
  createdAt: "2026-09-24T12:00:00Z",
  extractionRunId: "00000000-0000-4000-8000-0000000000bb",
  serviceVersion: "test",
  requestedBy: "urn:requester:synthetic-01",
  requestedAt: "2026-09-24T11:59:00Z",
  sourceTextUri: "gs://synthetic-bucket/import.pages.json",
  fidelityReportUri: "gs://synthetic-bucket/import.fidelity-report.json",
};

type Json = Record<string, unknown>;

export function decode(bytes: Uint8Array): Json {
  return JSON.parse(new TextDecoder().decode(bytes)) as Json;
}

export function encode(value: unknown): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(value));
}

// The synthetic publication with its document and List changed by `change`.
export function mutated(
  mapping: EmaMapping,
  change: (document: Json, list: Json) => void,
): Publication {
  const publication = syntheticPublication(mapping);
  const document = decode(publication.document);
  const list = decode(publication.index);
  change(document, list);
  return { ...publication, document: encode(document), index: encode(list) };
}

// The synthetic Composition's section tree, and its first section's first subsection.
export function sections(document: Json): Json[] {
  const [entry] = document.entry as [Json];
  return (entry.resource as Json).section as Json[];
}

export function firstSubsection(document: Json): Json {
  const [root] = sections(document) as [Json];
  const [first] = root.section as [Json];
  return first;
}
