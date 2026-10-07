import { readFileSync } from "node:fs";

import { sha256Bytes } from "../authority/import.js";
import type { EmaMapping } from "../fhir/mapping.js";
import { sha256 } from "../lib/hash.js";
import { CertifiedWordRefusedError, importCertifiedWord, type CertifiedWordRun } from "./import.js";
import type { CertifiedWordRequest } from "./shape.js";

// The certified Word importer's golden vectors (docs/design/certified-word-import.md, D1): what it
// makes of each result `zone_a.recompute` wrote for the synthetic Word labels committed by
// zone-a/scripts/certified_word_fixtures.py, and where it refuses changed copies of the first.
// Regenerated and compared under `npm run contracts:check`.

export const RECOMPUTED = "test/fixtures/certified-word/recompute";

export const RUN: CertifiedWordRun = {
  submissionId: "00000000-0000-4000-8000-000000000001",
  createdAt: "2026-10-06T12:00:00Z",
  extractionRunId: "00000000-0000-4000-8000-000000000002",
  serviceVersion: "vectors",
  sourceTextUri: "gs://vectors/certified-word.pages.json",
  fidelityReportUri: "gs://vectors/certified-word.fidelity-report.json",
};

// One synthetic Word label: its recompute request and what a person confirmed for it, as
// zone-a/scripts/certified_word_fixtures.py writes them.
export type RecomputedCase = {
  name: string;
  about: string;
  request: CertifiedWordRequest["recompute"];
  documentId: string;
  product: CertifiedWordRequest["product"];
};

export function recomputedCases(): RecomputedCase[] {
  return JSON.parse(readFileSync(`${RECOMPUTED}/cases.json`, "utf8")) as RecomputedCase[];
}

// The recompute's bytes for a case, as the command wrote them.
export function recomputed(name: string): Uint8Array {
  return readFileSync(`${RECOMPUTED}/${name}.json`);
}

// What a person confirmed for a case, with the upload and the approval placeholder.
export function caseRequest(found: RecomputedCase): CertifiedWordRequest {
  return {
    upload: {
      filename: `${found.name}.docx`,
      storageUri: `gs://synthetic-submissions/${found.name}.docx`,
    },
    recompute: found.request,
    documentId: found.documentId,
    product: found.product,
    approval: {
      approverId: "urn:approver:synthetic-01",
      approverRole: "content-reviewer",
      approvedAt: "2026-10-06T11:59:00Z",
      method: "api-attestation",
    },
  };
}

type Vector = {
  name: string;
  resultSha256: string;
  outcome:
    | { imported: { submissionSha256: string; sourceTextSha256: string; reportSha256: string } }
    | { refused: string };
};

function outcome(result: Uint8Array, request: unknown, mapping: EmaMapping): Vector["outcome"] {
  try {
    const made = importCertifiedWord(result, request, mapping, RUN);
    return {
      imported: {
        submissionSha256: sha256(made.submission),
        sourceTextSha256: sha256(made.sourceText),
        reportSha256: sha256(made.fidelityReport),
      },
    };
  } catch (error) {
    if (error instanceof CertifiedWordRefusedError) {
      return { refused: `${error.stage}: ${error.reason}` };
    }
    throw error;
  }
}

type Json = Record<string, unknown>;

export function importerVectors(mapping: EmaMapping): Vector[] {
  const cases = recomputedCases();
  const vector = (name: string, result: Uint8Array, request: unknown): Vector => ({
    name,
    resultSha256: sha256Bytes(result),
    outcome: outcome(result, request, mapping),
  });
  const vectors = cases.map((found) =>
    vector(found.name, recomputed(found.name), caseRequest(found)),
  );
  const [first] = cases;
  if (first === undefined) return vectors;
  const request = caseRequest(first);
  const result = JSON.parse(new TextDecoder().decode(recomputed(first.name))) as Json;
  const changed = (change: (json: Json) => void): Uint8Array => {
    const json = structuredClone(result);
    change(json);
    return new TextEncoder().encode(`${JSON.stringify(json)}\n`);
  };
  const sections = (json: Json): Json[] => json.sections as Json[];
  const section1 = (json: Json): Json => {
    const found = sections(json).find(({ key }) => key === "smpc.1");
    if (found === undefined) throw new Error("the recomputed SmPC has no section 1");
    return found;
  };
  const bytes = recomputed(first.name);
  vectors.push(
    vector(
      `${first.name}-other-versions`,
      changed((json) => ((json.versions as Json).builder = "word-epi/0.0.0")),
      request,
    ),
    vector(
      `${first.name}-sections-swapped`,
      changed((json) => sections(json).reverse()),
      request,
    ),
    vector(
      `${first.name}-title-of-two-lines`,
      changed((json) => (section1(json).title = "1. NAME OF THE\nMEDICINAL PRODUCT")),
      request,
    ),
    vector(
      `${first.name}-narrative-not-the-page`,
      changed((json) => (section1(json).page = "\nAnother text.\n")),
      request,
    ),
    vector(`${first.name}-name-retyped`, bytes, {
      ...request,
      product: { ...request.product, name: "SYNTHETIC EXAMPLINE" },
    }),
    vector(`${first.name}-a-number-left-out`, bytes, {
      ...request,
      product: { ...request.product, euAuthorisationNumbers: ["EU/1/24/9999/001"] },
    }),
  );
  return vectors;
}
