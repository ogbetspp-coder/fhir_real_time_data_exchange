import { readFileSync } from "node:fs";

import type { EmaMapping } from "../fhir/mapping.js";
import { sha256 } from "../lib/hash.js";
import { ImportRefusedError, importPublication, sha256Bytes, type ImportRun } from "./import.js";
import { syntheticPublication } from "./synthetic.js";
import { transformDocument } from "./t/document.js";

// The importer's golden vectors (docs/design/authority-import-contract.md, D10): what it makes of
// the synthetic publication, and where it refuses each pinned EMA label. Regenerated and
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

export function importerVectors(mapping: EmaMapping): Vector[] {
  const synthetic = syntheticPublication(mapping);
  const vectors: Vector[] = [
    {
      name: "synthetic",
      documentSha256: sha256Bytes(synthetic.document),
      indexSha256: sha256Bytes(synthetic.index),
      outcome: outcome(synthetic.request, synthetic.document, synthetic.index, mapping),
    },
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
