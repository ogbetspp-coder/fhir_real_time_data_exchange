import type { z } from "zod";

import { CANONICAL_SUBMISSION_VERSION, CanonicalSubmissionSchema } from "./canonical-submission.js";
import { FidelityReportSchema, SourceDocumentTextSchema } from "./fidelity-report.js";
import { IngestionProvenanceSchema } from "./ingestion-provenance.js";
import { RUN_MANIFEST_VERSION, RunManifestSchema } from "./run-manifest.js";

export type ContractDefinition = {
  name: string;
  version: string;
  schema: z.ZodType;
};

// Every root contract that is published as JSON Schema into contracts/generated/. The generator
// and the schema tests iterate this list; add new roots here, never ad hoc.
export const CONTRACTS: readonly ContractDefinition[] = [
  {
    name: "canonical-submission",
    version: CANONICAL_SUBMISSION_VERSION,
    schema: CanonicalSubmissionSchema,
  },
  { name: "ingestion-provenance", version: "1.0.0", schema: IngestionProvenanceSchema },
  { name: "fidelity-report", version: "1.0.0", schema: FidelityReportSchema },
  { name: "source-document-text", version: "1.0.0", schema: SourceDocumentTextSchema },
  { name: "run-manifest", version: RUN_MANIFEST_VERSION, schema: RunManifestSchema },
];

export function contractId(name: string, version: string): string {
  return `https://khs.dev/contracts/${name}/${version}/schema.json`;
}

export * from "./canonical-submission.js";
export * from "./common.js";
export * from "./fidelity-report.js";
export * from "./ingestion-provenance.js";
export * from "./run-manifest.js";
export * from "./type2-bundle.js";
