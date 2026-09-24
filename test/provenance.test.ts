import { beforeAll, describe, expect, it } from "vitest";

import type { CanonicalSubmission } from "../src/contracts/index.js";
import type { EmaMapping } from "../src/fhir/mapping.js";
import { loadEmaMapping } from "../src/fhir/mapping.js";
import {
  APPROVER_IDENTIFIER_SYSTEM,
  APPROVER_ROLE_SYSTEM,
  AUTHORITY_FILE_IDENTIFIER_SYSTEM,
  IMPORT_REQUESTER_IDENTIFIER_SYSTEM,
  PARTICIPANT_TYPE_ATTESTER,
  PARTICIPANT_TYPE_SYSTEM,
  toProvenanceResource,
  withEmaTarget,
} from "../src/fhir/provenance.js";
import type { FhirResource } from "../src/fhir/types.js";
import { createSyntheticSubmission } from "../src/fixtures/synthetic-submission.js";
import { stableUuid } from "../src/lib/hash.js";
import { DOCUMENT_ID, INDEX_ID, asImport, attested } from "./support/submission.js";

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
        `${submission.bundle.identifier.value}:${submission.submissionId}`,
      ),
    );
    expect(first.resourceType).toBe("Provenance");
    expect(first.recorded).toBe(attested(submission).approvedAt);
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
            coding: [{ system: APPROVER_ROLE_SYSTEM, code: attested(submission).approverRole }],
          },
        ],
        who: {
          identifier: {
            system: APPROVER_IDENTIFIER_SYSTEM,
            value: attested(submission).approverId,
          },
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

describe("an authority import's Provenance", () => {
  it("names the authority as the source files' agent and the requester as the enterer", () => {
    const { submission, fidelityReport } = createSyntheticSubmission(mapping);
    const imported = asImport(submission);
    const resource = toProvenanceResource(imported, fidelityReport, {
      ...OUTPUT,
      fetchedAt: "2026-09-24T12:30:00Z",
    });

    expect(resource.recorded).toBe("2026-09-24T12:30:00Z");
    expect(resource.activity).toEqual({
      coding: [
        { system: "https://khs.dev/fhir/CodeSystem/provenance-activity", code: "authority-import" },
      ],
    });
    const agents = arrayField(resource, "agent") as {
      type: { coding: { code: string }[] };
      who: unknown;
    }[];
    expect(agents.map(({ type }) => type.coding[0]?.code)).toEqual(["assembler", "enterer"]);
    expect(agents[1]?.who).toEqual({
      identifier: {
        system: IMPORT_REQUESTER_IDENTIFIER_SYSTEM,
        value: "urn:requester:synthetic-01",
      },
    });
    const entities = arrayField(resource, "entity") as { what: unknown; agent?: unknown }[];
    expect(entities).toHaveLength(5);
    expect(entities[0]?.what).toEqual({
      identifier: {
        system: AUTHORITY_FILE_IDENTIFIER_SYSTEM,
        value: `synthetic:Bundle/${DOCUMENT_ID}`,
      },
    });
    expect(entities[2]?.what).toEqual({
      identifier: { system: AUTHORITY_FILE_IDENTIFIER_SYSTEM, value: `synthetic:List/${INDEX_ID}` },
    });
    // The authority attested its publication, so it is the agent of its files, not of the record.
    expect(entities.slice(0, 4).every(({ agent }) => agent !== undefined)).toBe(true);
    expect(entities[4]?.agent).toBeUndefined();
  });

  it("refuses an import without the time Zone B fetched the authority's files", () => {
    const { submission, fidelityReport } = createSyntheticSubmission(mapping);

    expect(() => toProvenanceResource(asImport(submission), fidelityReport, OUTPUT)).toThrow(
      /needs its source and fetch time/,
    );
  });

  it("refuses an attested approval of an authority's publication", () => {
    const { submission, fidelityReport } = createSyntheticSubmission(mapping);
    const mixed = { ...asImport(submission), approval: submission.approval };

    expect(() => toProvenanceResource(mixed, fidelityReport, OUTPUT)).toThrow(
      /An attested approval has a drawn source/,
    );
  });
});
