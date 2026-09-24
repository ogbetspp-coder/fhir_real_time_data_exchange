import { beforeAll, describe, expect, it } from "vitest";

import {
  SubmissionRejectedError,
  verifyDocumentSubmission,
  type CanonicalSubmission,
  type GateOptions,
} from "../../src/contracts/index.js";
import type { SourceDocumentText } from "../../src/fidelity/index.js";
import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import {
  createSyntheticSubmission,
  type SyntheticSubmission,
} from "../../src/fixtures/synthetic-submission.js";
import { sha256 } from "../../src/lib/hash.js";
import {
  DOCUMENT_ID,
  INDEX_ID,
  SYNTHETIC,
  asImport as recast,
  drawn,
  seal,
} from "../support/submission.js";

// The 2.0.0 rules that fit a source, its graph, its approval and its extractor together, and
// keep synthetic content out of a deployment that accepts none
// (docs/design/authority-import-contract.md, D3, D7, D8).

let mapping: EmaMapping;
let fixture: SyntheticSubmission;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  fixture = createSyntheticSubmission(mapping);
});

function issues(
  submission: CanonicalSubmission,
  options: GateOptions = SYNTHETIC,
  sourceText: SourceDocumentText = fixture.sourceText,
): string[] {
  try {
    verifyDocumentSubmission(
      { submission, fidelityReport: fixture.fidelityReport, sourceText },
      mapping.sourceCodeSystem,
      options,
    );
  } catch (error) {
    if (error instanceof SubmissionRejectedError) return error.issues;
    throw error;
  }
  return [];
}

function renameExtractor(submission: CanonicalSubmission, name: string): SourceDocumentText {
  const version = `${name}/1.0.0`;
  submission.provenance.extraction.parser = { name, version: "1.0.0" };
  const sourceText = { ...structuredClone(fixture.sourceText), extractorVersion: version };
  drawn(submission).extractedText = {
    ...drawn(submission).extractedText,
    extractorVersion: version,
    sha256: sha256(sourceText),
  };
  seal(submission);
  return sourceText;
}

describe("synthetic sources", () => {
  it("refuses a drawn submission where the deployment accepts no synthetic content", () => {
    expect(issues(structuredClone(fixture.submission), { allowSyntheticSources: false })).toEqual([
      "No drawn-document extractor is qualified (fidelity §7)",
    ]);
  });

  it("refuses a drawn submission from an extractor that is not synthetic, wherever", () => {
    const submission = structuredClone(fixture.submission);
    const sourceText = renameExtractor(submission, "acme-pdf");

    expect(issues(submission, SYNTHETIC, sourceText)).toContain(
      "No drawn-document extractor is qualified (fidelity §7)",
    );
  });

  it("requires a synthetic submission to carry a synthetic identifier", () => {
    const submission = structuredClone(fixture.submission);
    submission.bundle.identifier.value = "real-looking-document";

    expect(issues(seal(submission))).toContain(
      "A synthetic submission's Bundle identifier is synthetic",
    );
  });

  it("requires a synthetic terminology service when there is one", () => {
    const submission = structuredClone(fixture.submission);
    const terminology = submission.provenance.extraction.terminologyService;
    if (terminology === undefined) throw new Error("fixture declares a terminology service");
    terminology.name = "real-terminology";

    expect(issues(seal(submission))).toContain(
      "A synthetic submission's terminology service is synthetic",
    );
  });

  it("refuses the authority-import namespace on a drawn submission", () => {
    const submission = structuredClone(fixture.submission);
    submission.bundle.identifier.value = `authority-import:ema:${DOCUMENT_ID}`;

    expect(issues(seal(submission))).toContain(
      "The authority-import namespace is written only by the importer",
    );
  });
});

describe("the extractor, named once", () => {
  it("refuses an extracted-text reference written by another extractor", () => {
    const submission = structuredClone(fixture.submission);
    drawn(submission).extractedText.extractorVersion = "synthetic-extractor/9.9.9";

    expect(issues(seal(submission))).toContain(
      "sourceDocument.extractedText.extractorVersion must be extraction.parser's name/version",
    );
  });

  it("refuses an extractor name that cannot be split from its version", () => {
    const submission = structuredClone(fixture.submission);
    const sourceText = renameExtractor(submission, "synthetic-extractor@2");

    expect(issues(submission, SYNTHETIC, sourceText)).toContain(
      "extraction.parser.name must not contain / or @",
    );
  });

  it("refuses page text another extractor wrote", () => {
    const submission = structuredClone(fixture.submission);
    const sourceText = { ...structuredClone(fixture.sourceText), extractorVersion: "other/1.0.0" };
    drawn(submission).extractedText.sha256 = sha256(sourceText);

    expect(issues(seal(submission), SYNTHETIC, sourceText)).toContain(
      "Extracted source text was written by another extractor",
    );
  });
});

describe("source, graph and approval fit together", () => {
  it("refuses a type1 graph from a drawn source", () => {
    const submission = structuredClone(fixture.submission);
    submission.graphType = "type1";

    expect(issues(seal(submission))).toContain("A drawn source carries a type2 graph");
  });

  it("refuses an authority-publication approval on a drawn source", () => {
    const imported = recast(fixture.submission);
    const submission = structuredClone(fixture.submission);
    submission.approval = imported.approval;

    expect(issues(seal(submission))).toContain(
      "An authority-publication approval requires an authority-publication source",
    );
  });

  it("refuses an approval that names another publication than the source", () => {
    const submission = recast(fixture.submission);
    if (submission.approval.method !== "authority-publication") throw new Error("an import");
    submission.approval.publication.documentId = INDEX_ID;
    submission.approval.publication.versionNumber = "2";

    const found = issues(seal(submission));
    expect(found).toContain("The approval names another document");
    expect(found).toContain("The approval names another version of the ePI");
  });

  it("refuses a request that names another document than the source", () => {
    const submission = recast(fixture.submission);
    const source = submission.provenance.sourceDocument;
    if (source.kind !== "authority-publication") throw new Error("an import");
    source.request.documentId = INDEX_ID;

    expect(issues(seal(submission))).toContain("The request names another document");
  });

  it("refuses an import that is not the importer's, or used a model", () => {
    const submission = recast(fixture.submission);
    submission.provenance.extraction.model = { provider: "p", id: "m" };

    expect(issues(seal(submission))).toContain(
      "An authority import uses no model and no prompt template",
    );
  });

  it("refuses an import whose parts disagree with each other", () => {
    const submission = recast(fixture.submission);
    const source = submission.provenance.sourceDocument;
    if (
      source.kind !== "authority-publication" ||
      submission.approval.method !== "authority-publication"
    ) {
      throw new Error("an import");
    }
    submission.graphType = "type2";
    submission.provenance.extraction.parser = { name: "other", version: "1.0.0" };
    source.request.authority = "EMA";
    source.request.indexId = DOCUMENT_ID;
    source.sectionPages = [
      { page: 2, path: "Composition.section[0]", code: "a" },
      { page: 3, path: "Composition.section[0]", code: "b" },
    ];
    submission.approval.authority = "EMA";
    submission.approval.publication.indexId = DOCUMENT_ID;
    submission.approval.publication.epiId = "SYNTHETIC-EPI-2";

    const found = issues(seal(submission));
    for (const expected of [
      "An authority publication carries a type1 graph",
      "An authority publication's extractor is authority-import",
      "The request names another authority",
      "The request names another index",
      "sourceDocument.sectionPages must number pages 1..n",
      "sourceDocument.sectionPages repeats a section path",
      "The approval names another authority",
      "The approval names another index",
      "The approval names another ePI",
    ]) {
      expect(found).toContain(expected);
    }
  });

  it("requires an import's Bundle identifier to be its authority-import value", () => {
    const submission = recast(fixture.submission);
    submission.bundle.identifier.value = `authority-import:synthetic:${INDEX_ID}`;

    expect(issues(seal(submission))).toContain(
      "An authority import's Bundle identifier is its authority-import value",
    );
  });

  it("refuses an authority publication with an attested approval", () => {
    const submission = recast(fixture.submission);
    submission.approval = structuredClone(fixture.submission.approval);

    expect(issues(seal(submission))).toContain(
      "An authority publication's approval is its authority-publication",
    );
  });

  it("refuses a synthetic import where the deployment accepts none", () => {
    expect(issues(recast(fixture.submission), { allowSyntheticSources: false })).toContain(
      "Synthetic content where the deployment accepts none",
    );
  });

  it("refuses a real authority's import that carries a synthetic mark", () => {
    const submission = recast(fixture.submission);
    const source = submission.provenance.sourceDocument;
    if (
      source.kind !== "authority-publication" ||
      submission.approval.method !== "authority-publication"
    ) {
      throw new Error("an import");
    }
    source.authority = "EMA";
    source.request.authority = "EMA";
    submission.approval.authority = "EMA";
    submission.bundle.identifier.value = `authority-import:ema:${DOCUMENT_ID}`;

    // The fixture's narratives carry the synthetic marker.
    expect(issues(seal(submission))).toContain(
      "A non-synthetic submission carries a synthetic mark",
    );
  });

  it("requires the synthetic marker in every narrative of a synthetic submission", () => {
    const submission = structuredClone(fixture.submission);
    const composition = submission.bundle.entry[0]?.resource as unknown as {
      section: { section?: { text?: { div: string } }[] }[];
    };
    const text = composition.section[0]?.section?.find((section) => section.text)?.text;
    if (text === undefined) throw new Error("fixture has a narrative");
    text.div = '<div xmlns="http://www.w3.org/1999/xhtml"><p>Unmarked text.</p></div>';

    expect(issues(seal(submission))).toContain(
      "Every narrative of a synthetic submission carries the synthetic marker",
    );
  });

  it("refuses every authority import until the gate recomputes it", () => {
    expect(issues(recast(fixture.submission))).toContain(
      "Authority imports are not accepted until the gate recomputes them",
    );
  });

  it("requires a structured source's pages to be one per section and wholly body", () => {
    const submission = recast(fixture.submission);
    const sourceText = {
      ...structuredClone(fixture.sourceText),
      extractorVersion: "authority-import/1.0.0",
    };
    const source = submission.provenance.sourceDocument;
    if (source.kind !== "authority-publication") throw new Error("an import");
    source.extractedText.sha256 = sha256(sourceText);
    const found = issues(seal(submission), SYNTHETIC, sourceText);

    // The drawn fixture's pages have headers and footers and are not one per section.
    expect(found).toContain("A structured source has one page per section");
    expect(
      found.some((issue) => /^Page \d+ of a structured source is not wholly body$/.test(issue)),
    ).toBe(true);
  });
});
