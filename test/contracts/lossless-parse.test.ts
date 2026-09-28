import { beforeAll, describe, expect, it, vi } from "vitest";

import type { AuthorityFetcher } from "../../src/authority/fetch.js";
import { verifyAuthorityImport } from "../../src/authority/gate.js";
import { importPublication } from "../../src/authority/import.js";
import { syntheticPublication } from "../../src/authority/synthetic.js";
import {
  SubmissionRejectedError,
  verifyDocumentSubmission,
  type DocumentSubmissionInput,
} from "../../src/contracts/index.js";
import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import { createSyntheticSubmission } from "../../src/fixtures/synthetic-submission.js";
import { RUN } from "../authority/support.js";
import { SYNTHETIC } from "../support/submission.js";

// The shape bound refuses an own `__proto__` member first (test/json-shape.test.ts). Behind it,
// each gate also requires the parsed value to be the input, so a member any parser drops is
// refused even where no named rule knows it. This file lifts the shape bound to reach that check.

vi.mock("../../src/lib/json-shape.js", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/lib/json-shape.js")>()),
  jsonShapeIssues: (): string[] => [],
}));

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

// The value with an own `__proto__` member in its Bundle, as JSON.parse makes one.
function withProtoMember<T>(value: T): T {
  const text = JSON.stringify(value).replace(
    '"bundle":{',
    '"bundle":{"__proto__":{"note":"Take one tablet twice daily with food."},',
  );
  return JSON.parse(text) as T;
}

function issues(run: () => unknown): Promise<string[]> {
  return Promise.resolve()
    .then(run)
    .then(
      () => [],
      (error: unknown) => {
        if (error instanceof SubmissionRejectedError) return error.issues;
        throw error;
      },
    );
}

describe("a parse the gate does not trust unless it is the input", () => {
  it("refuses a document submission whose parse drops a member", async () => {
    const { submission, fidelityReport, sourceText } = createSyntheticSubmission(mapping);
    const input: DocumentSubmissionInput = {
      submission: withProtoMember(submission),
      fidelityReport,
      sourceText,
    };

    expect(
      await issues(() => verifyDocumentSubmission(input, mapping.sourceCodeSystem, SYNTHETIC)),
    ).toEqual(["submission does not parse to the value it is"]);
  });

  it("refuses an authority import whose parse drops a member, before it fetches", async () => {
    const publication = syntheticPublication(mapping);
    const { submission, fidelityReport, sourceText } = importPublication(
      publication.request,
      publication,
      mapping,
      RUN,
    );
    const unreachable: AuthorityFetcher = {
      fetch: () => Promise.reject(new Error("the gate fetched")),
    };

    expect(
      await issues(() =>
        verifyAuthorityImport(
          { submission: withProtoMember(submission), fidelityReport, sourceText },
          mapping,
          { allowSyntheticSources: true, dryRun: true },
          unreachable,
        ),
      ),
    ).toEqual(["submission does not parse to the value it is"]);
  });
});
