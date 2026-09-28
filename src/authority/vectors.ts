import { readFileSync } from "node:fs";

import type { EmaMapping } from "../fhir/mapping.js";
import { sha256 } from "../lib/hash.js";
import { ImportRefusedError, importPublication, sha256Bytes, type ImportRun } from "./import.js";
import { emaShapedPublication, syntheticPublication } from "./synthetic.js";
import { transformDocument } from "./t/document.js";

// The importer's golden vectors (docs/design/authority-import-contract.md, D10): what it makes of
// the synthetic publication, where it refuses changed copies of it (the EMA-shaped one at the last
// stage, `rendering`), and where it refuses each pinned EMA label. Regenerated and
// compared under `npm run contracts:check`; importer.lock.json ties them to the version.

const RUN: ImportRun = {
  submissionId: "00000000-0000-4000-8000-000000000001",
  createdAt: "2026-09-24T12:00:00Z",
  extractionRunId: "00000000-0000-4000-8000-000000000002",
  serviceVersion: "vectors",
  requestedBy: "urn:requester:vectors",
  requestedAt: "2026-09-24T12:00:00Z",
  sourceTextUri: "gs://vectors/import.pages.json",
  fidelityReportUri: "gs://vectors/import.fidelity-report.json",
};

type Vector = {
  name: string;
  documentSha256: string;
  indexSha256: string;
  outcome:
    | { imported: { submissionSha256: string; sourceTextSha256: string; reportSha256: string } }
    | { refused: string };
};

function outcome(
  request: Parameters<typeof importPublication>[0],
  document: Uint8Array,
  index: Uint8Array,
  mapping: EmaMapping,
): Vector["outcome"] {
  try {
    const result = importPublication(request, { document, index }, mapping, RUN);
    return {
      imported: {
        submissionSha256: sha256(result.submission),
        sourceTextSha256: sha256(result.sourceText),
        reportSha256: sha256(result.fidelityReport),
      },
    };
  } catch (error) {
    if (error instanceof ImportRefusedError) return { refused: `${error.stage}: ${error.reason}` };
    throw error;
  }
}

const LABELS = "labels/ema-epi";

type Json = Record<string, unknown>;

// The synthetic document's bytes with `change` made to its JSON.
function changed(document: Uint8Array, change: (json: Json) => void): Uint8Array {
  const json = JSON.parse(new TextDecoder().decode(document)) as Json;
  change(json);
  return new TextEncoder().encode(JSON.stringify(json, null, 2));
}

function composition(document: Json): Json {
  return (document.entry as Json[])[0]?.resource as Json;
}

// The Composition's first leaf section, whose div a case replaces.
function firstLeaf(document: Json): Json {
  let [section] = composition(document).section as [Json];
  while (Array.isArray(section.section)) [section] = section.section as [Json];
  return section;
}

export function importerVectors(mapping: EmaMapping): Vector[] {
  const synthetic = syntheticPublication(mapping);
  const vector = (
    name: string,
    publication: Pick<typeof synthetic, "request" | "document" | "index">,
  ): Vector => ({
    name,
    documentSha256: sha256Bytes(publication.document),
    indexSha256: sha256Bytes(publication.index),
    outcome: outcome(publication.request, publication.document, publication.index, mapping),
  });
  const withDocument = (change: (document: Json) => void) => ({
    ...synthetic,
    document: changed(synthetic.document, change),
  });
  const vectors: Vector[] = [
    vector("synthetic", synthetic),
    // Every stage but the last passes a publication in the EMA's form; the renderer gate's
    // evidence is missing (A-1 of the 2026-09-28 audit).
    vector("ema-shaped", emaShapedPublication(mapping)),
    vector(
      "synthetic-date-free-text",
      withDocument((document) => (composition(document).date = "next tuesday")),
    ),
    vector(
      "synthetic-date-not-on-the-calendar",
      withDocument((document) => (composition(document).date = "2026-02-30")),
    ),
    vector(
      "synthetic-timestamp-without-a-time",
      withDocument((document) => (document.timestamp = "2026-09-24")),
    ),
    vector(
      "synthetic-picture-source-only-in-data-src",
      withDocument((document) => {
        firstLeaf(document).text = {
          status: "generated",
          div: '<div xmlns="http://www.w3.org/1999/xhtml"><p><img data-src="#picture"/> not for clinical use</p></div>',
        };
      }),
    ),
  ];
  const lock = JSON.parse(readFileSync(`${LABELS}/sources.lock.json`, "utf8")) as {
    sources: { file: string; url: string; list: string; listFile: string }[];
  };
  const id = (url: string): string => url.split("/").at(-1) ?? "";
  for (const source of [...lock.sources].sort((a, b) =>
    a.file < b.file ? -1 : a.file > b.file ? 1 : 0,
  )) {
    const document = readFileSync(`${LABELS}/sources/${source.file}`);
    const index = readFileSync(`${LABELS}/lists/${source.listFile}`);
    vectors.push({
      name: source.file,
      documentSha256: sha256Bytes(document),
      indexSha256: sha256Bytes(index),
      outcome: outcome(
        { authority: "EMA", documentId: id(source.url), indexId: id(source.list), language: "en" },
        document,
        index,
        mapping,
      ),
    });
  }
  return vectors;
}

type SectionVector = {
  path: string;
  outcome: { divSha256: string } | { refused: string } | "no-div";
};

// T's outcome for every section of every pinned label, in pre-order (docs/design/authority-import-t.md,
// T7): each section's T(div) hash, or its refusal, so T's output is locked where the import as a
// whole refuses.
export function labelSectionVectors(): { name: string; sections: SectionVector[] }[] {
  const lock = JSON.parse(readFileSync(`${LABELS}/sources.lock.json`, "utf8")) as {
    sources: { file: string }[];
  };
  type Section = { text?: { div?: string }; section?: Section[] };
  return [...lock.sources]
    .map(({ file }) => file)
    .sort()
    .map((file) => {
      const document = JSON.parse(readFileSync(`${LABELS}/sources/${file}`, "utf8")) as {
        entry?: { resource?: { section?: Section[] } }[];
      };
      const placed: { path: string; div: string | undefined }[] = [];
      const walk = (sections: Section[], base: string): void => {
        sections.forEach((section, position) => {
          const path = `${base}[${position}]`;
          placed.push({ path, div: section.text?.div });
          walk(section.section ?? [], `${path}.section`);
        });
      };
      walk(document.entry?.[0]?.resource?.section ?? [], "Composition.section");
      const outcomes = transformDocument(placed.map(({ div }) => div));
      return {
        name: file,
        sections: placed.map(({ path }, index) => {
          const outcome = outcomes[index];
          return {
            path,
            outcome:
              outcome === undefined
                ? ("no-div" as const)
                : "div" in outcome
                  ? { divSha256: sha256(outcome.div) }
                  : { refused: outcome.refused },
          };
        }),
      };
    });
}
