import { beforeAll, describe, expect, it } from "vitest";

import { CanonicalSubmissionSchema } from "../src/contracts/index.js";
import type { EmaMapping } from "../src/fhir/mapping.js";
import { loadEmaMapping } from "../src/fhir/mapping.js";
import { createSyntheticSubmission } from "../src/fixtures/synthetic-submission.js";
import { canonicalJson } from "../src/lib/hash.js";

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

describe("synthetic canonical submission fixture", () => {
  it("is byte-identical across calls", () => {
    const first = createSyntheticSubmission(mapping);
    const second = createSyntheticSubmission(mapping);

    expect(canonicalJson(first)).toBe(canonicalJson(second));
    expect(first.submission.bundleSha256).toBe(second.submission.bundleSha256);
    expect(first.fidelityReport.reportHash).toBe(second.fidelityReport.reportHash);
  });

  it("satisfies the canonical submission contract and its fidelity gate", () => {
    const { submission, fidelityReport, sourceText } = createSyntheticSubmission(mapping);

    expect(() => CanonicalSubmissionSchema.parse(submission)).not.toThrow();
    expect(fidelityReport.status).toBe("passed");
    expect(fidelityReport.summary.total).toBe(32);
    expect(fidelityReport.summary.verified).toBe(32);
    expect(submission.provenance.sections).toHaveLength(32);
    expect(submission.provenance.decisions).toHaveLength(33);
    expect(sourceText.pages).toHaveLength(3);
    expect(submission.provenance.sections.some(({ spans }) => spans[0]?.page !== 1)).toBe(true);
  });
});
