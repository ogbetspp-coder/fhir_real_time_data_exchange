import { beforeAll, describe, expect, it } from "vitest";

import type { CanonicalSubmission } from "../src/contracts/index.js";
import type { EmaMapping } from "../src/fhir/mapping.js";
import { loadEmaMapping } from "../src/fhir/mapping.js";
import { toProvenanceResource, withEmaTarget } from "../src/fhir/provenance.js";
import type { FhirResource } from "../src/fhir/types.js";
import { createSyntheticSubmission } from "../src/fixtures/synthetic-submission.js";
import { stableUuid } from "../src/lib/hash.js";

let mapping: EmaMapping;

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

    const first = toProvenanceResource(submission, fidelityReport);
    const second = toProvenanceResource(submission, fidelityReport);

    expect(JSON.stringify(first)).toBe(JSON.stringify(second));
    expect(first.id).toBe(stableUuid("ingestion-provenance", submission.submissionId));
    expect(first.resourceType).toBe("Provenance");
    expect(first.recorded).toBe(submission.approval.approvedAt);
  });

  it("records one agent per declared actor and two source entities", () => {
    const { submission, fidelityReport } = createSyntheticSubmission(mapping);

    const withoutModel = toProvenanceResource(submission, fidelityReport);
    const modelled = toProvenanceResource(withModel(submission), fidelityReport);

    expect(arrayField(withoutModel, "agent")).toHaveLength(2);
    expect(arrayField(modelled, "agent")).toHaveLength(3);
    expect(arrayField(withoutModel, "entity")).toHaveLength(2);
    expect(arrayField(withoutModel, "extension")).toHaveLength(1);
  });

  it("carries no narrative, XHTML, or clinical text", () => {
    const { submission, fidelityReport } = createSyntheticSubmission(mapping);

    const serialized = JSON.stringify(toProvenanceResource(submission, fidelityReport));

    expect(serialized.includes("<div")).toBe(false);
    expect(serialized.includes("Synthetic demonstration content")).toBe(false);
    expect(serialized.includes(submission.approval.approvedContentSha256)).toBe(true);
    expect(serialized.includes(fidelityReport.reportHash)).toBe(true);
  });

  it("appends the EMA Bundle target without disturbing the ingestion targets", () => {
    const { submission, fidelityReport } = createSyntheticSubmission(mapping);
    const base = toProvenanceResource(submission, fidelityReport);

    const linked = withEmaTarget(base, "ema-document-bundle-1");

    expect(arrayField(base, "target")).toHaveLength(2);
    expect(arrayField(linked, "target")).toHaveLength(3);
    expect(arrayField(linked, "target")[2]).toEqual({
      reference: "Bundle/ema-document-bundle-1",
    });
    expect(linked.id).toBe(base.id);
  });
});
