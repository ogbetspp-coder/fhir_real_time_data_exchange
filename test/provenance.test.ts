import { beforeAll, describe, expect, it } from "vitest";

import type { CanonicalSubmission } from "../src/contracts/index.js";
import type { EmaMapping } from "../src/fhir/mapping.js";
import { loadEmaMapping } from "../src/fhir/mapping.js";
import {
  APPROVER_IDENTIFIER_SYSTEM,
  APPROVER_ROLE_SYSTEM,
  PARTICIPANT_TYPE_ATTESTER,
  PARTICIPANT_TYPE_SYSTEM,
  toProvenanceResource,
  withEmaTarget,
} from "../src/fhir/provenance.js";
import type { FhirResource } from "../src/fhir/types.js";
import { createSyntheticSubmission } from "../src/fixtures/synthetic-submission.js";
import { stableUuid } from "../src/lib/hash.js";

let mapping: EmaMapping;
const OUTPUT = { bundleId: "ema-document-bundle-1", compositionId: "ema-composition-1" };

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

function arrayField(resource: FhirResource, key: string): unknown[] {
  const value = resource[key];
  return Array.isArray(value) ? (value as unknown[]) : [];
}

function withModel(submission: CanonicalSubmission): CanonicalSubmission {
  return {
    ...submission,
    provenance: {
      ...submission.provenance,
      extraction: {
        ...submission.provenance.extraction,
        model: { provider: "synthetic-provider", id: "synthetic-model-1" },
      },
    },
  };
}

describe("ingestion Provenance projection", () => {
  it("is deterministic and stably identified", () => {
    const { submission, fidelityReport } = createSyntheticSubmission(mapping);

    const first = toProvenanceResource(submission, fidelityReport, OUTPUT);
    const second = toProvenanceResource(submission, fidelityReport, OUTPUT);

    expect(JSON.stringify(first)).toBe(JSON.stringify(second));
    expect(first.id).toBe(
      stableUuid(
        "ingestion-provenance",
        `${submission.bundle.identifier.value ?? ""}:${submission.submissionId}`,
      ),
    );
    expect(first.resourceType).toBe("Provenance");
    expect(first.recorded).toBe(submission.approval.approvedAt);
  });

  it("records one agent per declared actor and two source entities", () => {
    const { submission, fidelityReport } = createSyntheticSubmission(mapping);

    const withoutModel = toProvenanceResource(submission, fidelityReport, OUTPUT);
    const modelled = toProvenanceResource(withModel(submission), fidelityReport, OUTPUT);

    expect(arrayField(withoutModel, "agent")).toHaveLength(2);
    expect(arrayField(modelled, "agent")).toHaveLength(3);
    expect(arrayField(withoutModel, "entity")).toHaveLength(2);
    expect(arrayField(withoutModel, "extension")).toHaveLength(1);
  });

  it("names the approver and the approver's role on the attester agent", () => {
    const { submission, fidelityReport } = createSyntheticSubmission(mapping);

    const resource = toProvenanceResource(submission, fidelityReport, OUTPUT);
    const attesters = arrayField(resource, "agent").filter((agent) => {
      const typed = agent as { type?: { coding?: { system?: string; code?: string }[] } };
      return typed.type?.coding?.some(
        ({ system, code }) =>
          system === PARTICIPANT_TYPE_SYSTEM && code === PARTICIPANT_TYPE_ATTESTER,
      );
    });

    // Exactly one attester, carrying the identity and the role of the approval record — the
    // role as a coding under its own system, never inferred from the participant type.
    expect(attesters).toEqual([
      {
        type: {
          coding: [{ system: PARTICIPANT_TYPE_SYSTEM, code: PARTICIPANT_TYPE_ATTESTER }],
        },
        role: [
          {
            coding: [{ system: APPROVER_ROLE_SYSTEM, code: submission.approval.approverRole }],
          },
        ],
        who: {
          identifier: { system: APPROVER_IDENTIFIER_SYSTEM, value: submission.approval.approverId },
        },
      },
    ]);
  });

  it("carries no narrative, XHTML, or clinical text", () => {
    const { submission, fidelityReport } = createSyntheticSubmission(mapping);

    const serialized = JSON.stringify(toProvenanceResource(submission, fidelityReport, OUTPUT));

    expect(serialized.includes("<div")).toBe(false);
    expect(serialized.includes("Synthetic demonstration content")).toBe(false);
    expect(serialized.includes(submission.approval.approvedContentSha256)).toBe(true);
    expect(serialized.includes(fidelityReport.reportHash)).toBe(true);
  });

  it("targets only the record's identifier and the ids the run persisted", () => {
    const { submission, fidelityReport } = createSyntheticSubmission(mapping);
    const resource = toProvenanceResource(submission, fidelityReport, OUTPUT);

    // Never a fullUrl the submission chose: a run's Provenance can point only into its own
    // namespace (docs/design/authority-import-contract.md, D7).
    expect(arrayField(resource, "target")).toEqual([
      { identifier: submission.bundle.identifier },
      { reference: `Composition/${OUTPUT.compositionId}` },
      { reference: `Bundle/${OUTPUT.bundleId}` },
    ]);
  });

  it("keeps two approvals of the same content apart", () => {
    const { submission, fidelityReport } = createSyntheticSubmission(mapping);
    const again = { ...submission, submissionId: "00000000-0000-4000-8000-000000000002" };

    expect(toProvenanceResource(again, fidelityReport, OUTPUT).id).not.toBe(
      toProvenanceResource(submission, fidelityReport, OUTPUT).id,
    );
  });

  it("appends a further EMA Bundle target without disturbing the others", () => {
    const { submission, fidelityReport } = createSyntheticSubmission(mapping);
    const base = toProvenanceResource(submission, fidelityReport, OUTPUT);

    const linked = withEmaTarget(base, "ema-document-bundle-2");

    expect(arrayField(linked, "target")).toHaveLength(4);
    expect(arrayField(linked, "target")[3]).toEqual({ reference: "Bundle/ema-document-bundle-2" });
    expect(linked.id).toBe(base.id);
  });
});
