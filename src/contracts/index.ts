import { AGENT_TURN_VERSION, AgentTurnRecordSchema } from "./agent-turn.js";
import { CANONICAL_SUBMISSION_VERSION, CanonicalSubmissionSchema } from "./canonical-submission.js";
import {
  FIDELITY_REPORT_VERSION,
  FidelityReportSchema,
  SOURCE_DOCUMENT_TEXT_VERSION,
  SourceDocumentTextSchema,
} from "./fidelity-report.js";
import { QUERY_TOOLS_VERSION, QueryToolsSchema } from "./query-tools.js";
import { RUN_REQUEST_VERSION, RunRequestSchema } from "./run-request.js";
import { INGESTION_PROVENANCE_VERSION, IngestionProvenanceSchema } from "./ingestion-provenance.js";
import { RUN_MANIFEST_VERSION, RunManifestSchema } from "./run-manifest.js";
import type { ContractDefinition } from "./json-schema.js";

// Every root contract that is published as JSON Schema into contracts/generated/. The generator
// and the schema tests iterate this list; add new roots here, never ad hoc.
export const CONTRACTS: readonly ContractDefinition[] = [
  {
    name: "canonical-submission",
    version: CANONICAL_SUBMISSION_VERSION,
    schema: CanonicalSubmissionSchema,
  },
  {
    name: "ingestion-provenance",
    version: INGESTION_PROVENANCE_VERSION,
    schema: IngestionProvenanceSchema,
  },
  { name: "fidelity-report", version: FIDELITY_REPORT_VERSION, schema: FidelityReportSchema },
  {
    name: "source-document-text",
    version: SOURCE_DOCUMENT_TEXT_VERSION,
    schema: SourceDocumentTextSchema,
  },
  { name: "run-request", version: RUN_REQUEST_VERSION, schema: RunRequestSchema },
  { name: "query-tools", version: QUERY_TOOLS_VERSION, schema: QueryToolsSchema },
  { name: "agent-turn", version: AGENT_TURN_VERSION, schema: AgentTurnRecordSchema },
  { name: "run-manifest", version: RUN_MANIFEST_VERSION, schema: RunManifestSchema },
];

export { contractId, type ContractDefinition } from "./json-schema.js";

export * from "./agent-turn.js";
export * from "./canonical-submission.js";
export * from "./common.js";
export * from "./fidelity-report.js";
export * from "./ingestion-provenance.js";
export * from "./query-tools.js";
export * from "./run-manifest.js";
export * from "./run-request.js";
export * from "./canonical-bundle.js";
