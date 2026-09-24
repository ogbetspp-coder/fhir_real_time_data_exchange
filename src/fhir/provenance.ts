import type { CanonicalSubmission } from "../contracts/index.js";
import type { FidelityReport } from "../fidelity/index.js";
import { stableUuid } from "../lib/hash.js";
import type { FhirResource } from "./types.js";

// Pure projection of an approved submission into a FHIR R5 Provenance. Hashes, identifiers,
// and enumerations only: no narrative, no XHTML, no free text (ADR 0002).
//
// The systems, codes, and extension URL below are exported because the query service reads the
// persisted resource back (src/query/tools.ts) and must agree with what was written. ADR 0004:
// a pure library is shared by import, never re-declared.

export const PARTICIPANT_TYPE_SYSTEM =
  "http://terminology.hl7.org/CodeSystem/provenance-participant-type";
export const PARTICIPANT_TYPE_ASSEMBLER = "assembler";
export const PARTICIPANT_TYPE_ATTESTER = "attester";
const ACTIVITY_SYSTEM = "https://khs.dev/fhir/CodeSystem/provenance-activity";
export const MODEL_IDENTIFIER_SYSTEM = "https://khs.dev/fhir/identifier/model";
export const APPROVER_IDENTIFIER_SYSTEM = "https://khs.dev/fhir/identifier/approver";
export const SOURCE_DOCUMENT_IDENTIFIER_SYSTEM =
  "https://khs.dev/fhir/identifier/source-document-sha256";
export const FIDELITY_REPORT_IDENTIFIER_SYSTEM =
  "https://khs.dev/fhir/identifier/fidelity-report-sha256";
export const APPROVAL_CONTENT_EXTENSION_URL =
  "https://khs.dev/fhir/StructureDefinition/ext-approval-content-sha256";
// The approver's regulatory role (contracts ApproverRole), carried on the attester agent's
// `role` so a reader learns it from the resource rather than inferring it from the participant
// type.
export const APPROVER_ROLE_SYSTEM = "https://khs.dev/fhir/CodeSystem/approver-role";

type ProvenanceTarget = { identifier?: unknown; reference?: string };

type AgentWho = { display: string } | { identifier: { system: string; value: string } };

type Coding = { system: string; code: string };

type ProvenanceAgent = {
  type: { coding: Coding[] };
  role?: { coding: Coding[] }[];
  who: AgentWho;
};

function agent(code: string, who: AgentWho, role?: Coding): ProvenanceAgent {
  return {
    type: { coding: [{ system: PARTICIPANT_TYPE_SYSTEM, code }] },
    ...(role === undefined ? {} : { role: [{ coding: [role] }] }),
    who,
  };
}

// The ids of what the run persisted, from the crosswalk's output: the Provenance points only at
// them, never at a fullUrl the submission chose (docs/design/authority-import-contract.md, D7).
export type ProvenanceOutput = { bundleId: string; compositionId: string };

export function toProvenanceResource(
  submission: CanonicalSubmission,
  report: FidelityReport,
  output: ProvenanceOutput,
): FhirResource {
  const { bundle, provenance, approval } = submission;
  const { parser, model } = provenance.extraction;
  const { sourceDocument } = provenance;

  const { system, value } = bundle.identifier;
  if (value === undefined) throw new Error("Canonical submission Bundle requires an identifier");
  return {
    resourceType: "Provenance",
    // One per approval of this record: the identifier value keeps it in the run's namespace, and
    // the submission id keeps two approvals of the same content apart.
    id: stableUuid("ingestion-provenance", `${value}:${submission.submissionId}`),
    recorded: approval.approvedAt,
    target: [
      { identifier: { ...(system === undefined ? {} : { system }), value } },
      { reference: `Composition/${output.compositionId}` },
      { reference: `Bundle/${output.bundleId}` },
    ],
    activity: { coding: [{ system: ACTIVITY_SYSTEM, code: "structuring" }] },
    agent: [
      agent(PARTICIPANT_TYPE_ASSEMBLER, { display: `${parser.name}@${parser.version}` }),
      ...(model === undefined
        ? []
        : [
            agent(PARTICIPANT_TYPE_ASSEMBLER, {
              identifier: { system: MODEL_IDENTIFIER_SYSTEM, value: model.id },
            }),
          ]),
      agent(
        PARTICIPANT_TYPE_ATTESTER,
        { identifier: { system: APPROVER_IDENTIFIER_SYSTEM, value: approval.approverId } },
        { system: APPROVER_ROLE_SYSTEM, code: approval.approverRole },
      ),
    ],
    entity: [
      {
        role: "source",
        what: {
          identifier: { system: SOURCE_DOCUMENT_IDENTIFIER_SYSTEM, value: sourceDocument.sha256 },
        },
      },
      {
        role: "source",
        what: {
          identifier: { system: FIDELITY_REPORT_IDENTIFIER_SYSTEM, value: report.reportHash },
        },
      },
    ],
    extension: [
      { url: APPROVAL_CONTENT_EXTENSION_URL, valueString: approval.approvedContentSha256 },
    ],
  };
}

export function withEmaTarget(provenance: FhirResource, emaBundleId: string): FhirResource {
  const { target } = provenance;
  const existing: ProvenanceTarget[] = Array.isArray(target) ? (target as ProvenanceTarget[]) : [];
  return { ...provenance, target: [...existing, { reference: `Bundle/${emaBundleId}` }] };
}
