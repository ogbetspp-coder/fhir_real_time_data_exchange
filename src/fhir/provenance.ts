import type { CanonicalSubmission } from "../contracts/index.js";
import type { FidelityReport } from "../fidelity/index.js";
import { stableUuid } from "../lib/hash.js";
import type { FhirResource } from "./types.js";

// Pure projection of an approved submission into a FHIR R5 Provenance. Hashes, identifiers,
// and enumerations only: no narrative, no XHTML, no free text (ADR 0002).

const PARTICIPANT_TYPE = "http://terminology.hl7.org/CodeSystem/provenance-participant-type";
const ACTIVITY_SYSTEM = "https://khs.dev/fhir/CodeSystem/provenance-activity";
const MODEL_IDENTIFIER = "https://khs.dev/fhir/identifier/model";
const APPROVER_IDENTIFIER = "https://khs.dev/fhir/identifier/approver";
const SOURCE_DOCUMENT_IDENTIFIER = "https://khs.dev/fhir/identifier/source-document-sha256";
const FIDELITY_REPORT_IDENTIFIER = "https://khs.dev/fhir/identifier/fidelity-report-sha256";
const APPROVAL_CONTENT_EXTENSION =
  "https://khs.dev/fhir/StructureDefinition/ext-approval-content-sha256";

type ProvenanceTarget = { identifier?: unknown; reference?: string };

type AgentWho = { display: string } | { identifier: { system: string; value: string } };

type ProvenanceAgent = {
  type: { coding: { system: string; code: string }[] };
  who: AgentWho;
};

function agent(code: string, who: AgentWho): ProvenanceAgent {
  return { type: { coding: [{ system: PARTICIPANT_TYPE, code }] }, who };
}

export function toProvenanceResource(
  submission: CanonicalSubmission,
  report: FidelityReport,
): FhirResource {
  const { bundle, provenance, approval } = submission;
  const first = bundle.entry[0];
  if (first === undefined) throw new Error("Canonical submission Bundle requires an entry");
  const { parser, model } = provenance.extraction;
  const { sourceDocument } = provenance;

  const { system, value } = bundle.identifier;
  return {
    resourceType: "Provenance",
    id: stableUuid("ingestion-provenance", submission.submissionId),
    recorded: approval.approvedAt,
    target: [
      {
        identifier: {
          ...(system === undefined ? {} : { system }),
          ...(value === undefined ? {} : { value }),
        },
      },
      { reference: first.fullUrl },
    ],
    activity: { coding: [{ system: ACTIVITY_SYSTEM, code: "structuring" }] },
    agent: [
      agent("assembler", { display: `${parser.name}@${parser.version}` }),
      ...(model === undefined
        ? []
        : [agent("assembler", { identifier: { system: MODEL_IDENTIFIER, value: model.id } })]),
      agent("attester", {
        identifier: { system: APPROVER_IDENTIFIER, value: approval.approverId },
      }),
    ],
    entity: [
      {
        role: "source",
        what: {
          identifier: { system: SOURCE_DOCUMENT_IDENTIFIER, value: sourceDocument.sha256 },
        },
      },
      {
        role: "source",
        what: {
          identifier: { system: FIDELITY_REPORT_IDENTIFIER, value: report.reportHash },
        },
      },
    ],
    extension: [{ url: APPROVAL_CONTENT_EXTENSION, valueString: approval.approvedContentSha256 }],
  };
}

export function withEmaTarget(provenance: FhirResource, emaBundleId: string): FhirResource {
  const { target } = provenance;
  const existing: ProvenanceTarget[] = Array.isArray(target) ? (target as ProvenanceTarget[]) : [];
  return { ...provenance, target: [...existing, { reference: `Bundle/${emaBundleId}` }] };
}
