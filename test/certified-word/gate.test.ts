import { beforeAll, describe, expect, it } from "vitest";

import { loadConfig, type AppConfig } from "../../src/config.js";
import {
  CERTIFIED_WORD_NOT_RECOMPUTED,
  verifyCertifiedWordImport,
} from "../../src/certified-word/gate.js";
import { importCertifiedWord } from "../../src/certified-word/import.js";
import { RUN, caseRequest, recomputed, recomputedCases } from "../../src/certified-word/vectors.js";
import {
  SubmissionRejectedError,
  approvedContent,
  certifiedWordExtractorRecord,
  structuralInvariantIssues,
  verifyDocumentSubmission,
  type CanonicalSubmission,
  type DocumentSubmissionInput,
} from "../../src/contracts/index.js";
import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import type { FhirComposition } from "../../src/fhir/types.js";
import { createSyntheticSubmission } from "../../src/fixtures/synthetic-submission.js";
import { createSyntheticType2Bundle } from "../../src/fixtures/synthetic.js";
import { sha256 } from "../../src/lib/hash.js";
import { runPipeline } from "../../src/pipeline.js";

// Zone B's gate for a certified Word source (docs/design/certified-word-import.md, D2), and the
// ingress rules ADR 0006 adds to ADR 0002 invariants 7, 10 and 11: until the worker recomputes it,
// a certified Word submission passes a dry run only.

let mapping: EmaMapping;
let config: AppConfig;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  config = loadConfig({ ALLOW_SYNTHETIC_SOURCES: "true", NODE_ENV: "test", DRY_RUN: "true" });
});

function imported(
  name = "smpc",
  request?: unknown,
): DocumentSubmissionInput & {
  submission: CanonicalSubmission;
} {
  const found = recomputedCases().find((candidate) => candidate.name === name);
  if (found === undefined) throw new Error(`no case ${name}`);
  return importCertifiedWord(recomputed(name), request ?? caseRequest(found), mapping, RUN);
}

const OPTIONS = { allowSyntheticSources: true, dryRun: true };

function issues(run: () => unknown): string[] {
  try {
    run();
  } catch (error) {
    if (error instanceof SubmissionRejectedError) return error.issues;
    throw error;
  }
  return [];
}

// The submission changed and its hashes made again, so only the rule under test refuses it.
function resealed(submission: CanonicalSubmission): CanonicalSubmission {
  const sealed = { ...submission, bundleSha256: sha256(submission.bundle) };
  return {
    ...sealed,
    approval: { ...sealed.approval, approvedContentSha256: sha256(approvedContent(sealed)) },
  };
}

describe("the certified Word gate", () => {
  it("passes a dry run, through every check of the ordinary gate", () => {
    const input = imported();
    const gate = verifyCertifiedWordImport(input, mapping.sourceCodeSystem, OPTIONS);
    expect(gate.submission).toEqual(input.submission);
    expect(gate.narrativeSections.length).toBe(input.submission.provenance.sections.length);
  });

  it("refuses one when DRY_RUN is false, with its closed code", () => {
    expect(
      issues(() =>
        verifyCertifiedWordImport(imported(), mapping.sourceCodeSystem, {
          ...OPTIONS,
          dryRun: false,
        }),
      ),
    ).toEqual([CERTIFIED_WORD_NOT_RECOMPUTED]);
  });

  it("refuses a submission another version of the importer made", () => {
    const input = imported();
    const source = input.submission.provenance.sourceDocument;
    if (source.kind !== "certified-word") throw new Error("not a certified Word source");
    // A whole submission another importer made: its token names that importer, and every hash
    // holds, so only the gate's own version check refuses it.
    const importer = "certified-word-import/0.0.0";
    const version = sha256(certifiedWordExtractorRecord({ ...source, importer }));
    const extractorVersion = `certified-word/${version}`;
    const other = resealed({
      ...input.submission,
      provenance: {
        ...input.submission.provenance,
        sourceDocument: {
          ...source,
          importer,
          extractedText: { ...source.extractedText, extractorVersion },
        },
        extraction: {
          ...input.submission.provenance.extraction,
          parser: { name: "certified-word", version },
        },
      },
    });
    expect(structuralInvariantIssues(other)).toEqual([]);
    expect(
      issues(() =>
        verifyCertifiedWordImport(
          { ...input, submission: other },
          mapping.sourceCodeSystem,
          OPTIONS,
        ),
      ),
    ).toEqual(["The submission was made by another importer version than the gate runs"]);
  });

  it("gives the HTTP caller its closed code when DRY_RUN is false", () => {
    try {
      verifyCertifiedWordImport(imported(), mapping.sourceCodeSystem, {
        ...OPTIONS,
        dryRun: false,
      });
    } catch (error) {
      expect(error).toBeInstanceOf(SubmissionRejectedError);
      expect((error as SubmissionRejectedError).reason).toBe("certified-word-not-recomputed");
      return;
    }
    throw new Error("not refused");
  });

  it("refuses what is not a certified Word submission, or not a submission", () => {
    const drawn = createSyntheticSubmission(mapping);
    expect(
      issues(() => verifyCertifiedWordImport(drawn, mapping.sourceCodeSystem, OPTIONS)),
    ).toEqual(["Not a certified Word import"]);
    const invalid = { ...imported(), submission: { schemaVersion: "3.0.0" } };
    expect(
      issues(() => verifyCertifiedWordImport(invalid, mapping.sourceCodeSystem, OPTIONS)).length,
    ).toBeGreaterThan(0);
  });

  it("bounds the input's shape before anything walks or hashes it", () => {
    let deep: unknown = "x";
    for (let depth = 0; depth < 60; depth += 1) deep = { deep };
    const found = issues(() =>
      verifyCertifiedWordImport(
        { ...imported(), fidelityReport: deep },
        mapping.sourceCodeSystem,
        OPTIONS,
      ),
    );
    expect(found).toEqual(["fidelityReport nesting exceeds depth 48"]);
  });

  it("is the only way past the ordinary gate, for the very submission it examined", () => {
    const input = imported();
    const rule = "A certified Word source is accepted only as a dry run until Zone B recomputes it";
    expect(
      issues(() =>
        verifyDocumentSubmission(input, mapping.sourceCodeSystem, { allowSyntheticSources: true }),
      ),
    ).toEqual([rule]);
    expect(
      issues(() =>
        verifyDocumentSubmission(input, mapping.sourceCodeSystem, {
          allowSyntheticSources: true,
          certifiedWordDryRun: { submissionSha256: "0".repeat(64) },
        }),
      ),
    ).toEqual([rule]);
  });
});

describe("a certified Word source's ingress rules", () => {
  it("synthetic only where the deployment accepts it, and marked as one throughout", () => {
    const input = imported();
    expect(
      issues(() =>
        verifyCertifiedWordImport(input, mapping.sourceCodeSystem, {
          allowSyntheticSources: false,
          dryRun: true,
        }),
      ),
    ).toEqual(["Synthetic content where the deployment accepts none"]);
    // Real ids, synthetic narratives: a non-synthetic submission carrying the marker.
    const found = recomputedCases()[0];
    if (found === undefined) throw new Error("no cases");
    const request = caseRequest(found);
    const real = imported("smpc", {
      ...request,
      documentId: "11111111-1111-4111-8111-111111111111",
      product: {
        ...request.product,
        id: "22222222-2222-4222-8222-222222222222",
        holder: { ...request.product.holder, id: "33333333-3333-4333-8333-333333333333" },
      },
    });
    expect(
      issues(() => verifyCertifiedWordImport(real, mapping.sourceCodeSystem, OPTIONS)),
    ).toEqual(["A non-synthetic submission carries a synthetic mark"]);
  });

  it("holds the source, the graph, the approval and the extractor together (invariant 7)", () => {
    const { submission } = imported();
    const source = submission.provenance.sourceDocument;
    if (source.kind !== "certified-word") throw new Error("not a certified Word source");
    const extraction = submission.provenance.extraction;
    const withSource = (change: Partial<typeof source>): CanonicalSubmission => ({
      ...submission,
      provenance: { ...submission.provenance, sourceDocument: { ...source, ...change } },
    });
    const withExtraction = (change: Partial<typeof extraction>): CanonicalSubmission => ({
      ...submission,
      provenance: { ...submission.provenance, extraction: { ...extraction, ...change } },
    });
    const cases: [CanonicalSubmission, string][] = [
      [{ ...submission, graphType: "type2" }, "A certified Word source carries a type1 graph"],
      [
        withExtraction({ parser: { ...extraction.parser, version: "1.0.0" } }),
        "A certified Word source's extractor version is the hash of its importer and recompute",
      ],
      [
        withExtraction({ model: { provider: "p", id: "m" } }),
        "A certified Word import uses no model and no prompt template",
      ],
      [
        withExtraction({
          terminologyService: {
            name: "cap-smpc-en",
            version: "0.0.1",
            snapshotSha256: "0".repeat(64),
          },
        }),
        "A certified Word import's terminology is the mapping its recompute used",
      ],
      [
        withSource({ sectionPages: source.sectionPages.map((page) => ({ ...page, page: 1 })) }),
        "sourceDocument.sectionPages must number pages 1..n",
      ],
      [
        withSource({
          sectionPages: source.sectionPages.map((page) => ({ ...page, key: "smpc" })),
        }),
        "sourceDocument.sectionPages repeats a section key",
      ],
      [
        {
          ...submission,
          approval: {
            method: "authority-publication",
          } as unknown as CanonicalSubmission["approval"],
        },
        "A certified Word source's approval is an attestation",
      ],
    ];
    for (const [changed, issue] of cases) {
      expect(structuralInvariantIssues(resealed(changed))).toContain(issue);
    }
    // Another importer named under the same token (review of #193).
    expect(
      structuralInvariantIssues(resealed(withSource({ importer: "certified-word-import/0.0.0" }))),
    ).toContain(
      "A certified Word source's extractor version is the hash of its importer and recompute",
    );
    const renamed = withExtraction({ parser: { ...extraction.parser, name: "other" } });
    expect(structuralInvariantIssues(resealed(renamed))).toContain(
      "A certified Word source's extractor is certified-word",
    );
  });

  it("writes its namespace only from its own source, as its importer derives it", () => {
    const input = imported();
    const odd = resealed({
      ...input.submission,
      bundle: {
        ...input.submission.bundle,
        identifier: {
          ...input.submission.bundle.identifier,
          value: `${input.submission.bundle.identifier.value}:smpc`,
        },
      },
    });
    expect(
      issues(() =>
        verifyCertifiedWordImport({ ...input, submission: odd }, mapping.sourceCodeSystem, OPTIONS),
      ),
    ).toEqual(["A certified Word import's Bundle identifier is its certified-word value"]);

    const drawn = createSyntheticSubmission(mapping);
    const claimed = resealed({
      ...drawn.submission,
      bundle: {
        ...drawn.submission.bundle,
        identifier: {
          ...drawn.submission.bundle.identifier,
          value: "certified-word:00000000-5979-4e74-8000-0000000000d0",
        },
      },
    });
    expect(
      issues(() =>
        verifyDocumentSubmission({ ...drawn, submission: claimed }, mapping.sourceCodeSystem, {
          allowSyntheticSources: true,
        }),
      ),
    ).toContain("The certified-word namespace is written only by its importer");
  });

  it("holds its pages to one per section, each wholly body", () => {
    const input = imported();
    const source = input.submission.provenance.sourceDocument;
    if (source.kind !== "certified-word") throw new Error("not a certified Word source");
    type Pages = { extractorVersion: string; pages: { page: number; bodyStart: number }[] };
    const text = input.sourceText as Pages;
    const found = (pages: Pages["pages"]): string[] => {
      const sourceText = { ...text, pages };
      const submission = resealed({
        ...input.submission,
        provenance: {
          ...input.submission.provenance,
          sourceDocument: {
            ...source,
            extractedText: { ...source.extractedText, sha256: sha256(sourceText) },
          },
        },
      });
      return issues(() =>
        verifyDocumentSubmission({ ...input, submission, sourceText }, mapping.sourceCodeSystem, {
          allowSyntheticSources: true,
          certifiedWordDryRun: { submissionSha256: sha256(submission) },
        }),
      );
    };
    const shifted = text.pages.map((page) => (page.page === 2 ? { ...page, bodyStart: 1 } : page));
    expect(found(shifted)).toContain("Page 2 of a structured source is not wholly body");
    expect(found(text.pages.slice(0, -1))).toContain(
      "A structured source has one page per section",
    );
    // Page i is the record's i-th section (review of #193): two keys swapped, or a span on another
    // section's page, refuse.
    const pageIssues = (sectionPages: typeof source.sectionPages, spanPage?: number): string[] => {
      const submission = resealed({
        ...input.submission,
        provenance: {
          ...input.submission.provenance,
          sourceDocument: { ...source, sectionPages },
          sections: input.submission.provenance.sections.map((section, at) =>
            at === 0 && spanPage !== undefined
              ? { ...section, spans: section.spans.map((span) => ({ ...span, page: spanPage })) }
              : section,
          ),
        },
      });
      return issues(() =>
        verifyDocumentSubmission({ ...input, submission }, mapping.sourceCodeSystem, {
          allowSyntheticSources: true,
          certifiedWordDryRun: { submissionSha256: sha256(submission) },
        }),
      );
    };
    expect(pageIssues(source.sectionPages)).toEqual([]);
    const swapped = source.sectionPages.map((page, at) =>
      at === 1
        ? { ...page, key: source.sectionPages[2]?.key ?? "" }
        : at === 2
          ? { ...page, key: source.sectionPages[1]?.key ?? "" }
          : page,
    );
    expect(pageIssues(swapped)).toContain(
      "sourceDocument.sectionPages are not the record's sections in order",
    );
    expect(pageIssues(source.sectionPages, 3)).toContain(
      "A section's span is not on its own section's page",
    );
    // A heading over its subsections has the empty page: one that draws something is refused.
    const heading = source.sectionPages.find(({ key }) => key === "smpc.4")?.page;
    const drawn = text.pages.map((page) =>
      page.page === heading ? { ...page, text: "\nX\n", bodyEnd: 3 } : page,
    );
    expect(found(drawn)).toContain(
      "A page of the Word label that no narrative covers is not blank",
    );
  });
});

describe("a certified Word submission through the worker's pipeline", () => {
  const run = (input: DocumentSubmissionInput, runConfig: AppConfig) =>
    runPipeline(
      {
        runId: "00000000-0000-4000-8000-00000000c0de",
        sourceKind: "document",
        sourceResource: "gs://synthetic-submissions/smpc-assigned.submission.json",
        ...input,
      } as never,
      mapping,
      runConfig,
    );

  it("runs dry, its assigned heading carried as written to the EMA Composition", async () => {
    const result = await run(imported("smpc-assigned"), config);
    expect(result.status).toBe("validated");
    expect(result.evidence.manifest.ingestion?.sourceKind).toBe("certified-word");
    expect(result.outcomes.emaPreflight.issue.map(({ severity }) => severity)).toEqual(["success"]);
    const composition = result.emaBundle.entry[0]?.resource as FhirComposition;
    const titles = (composition.section[0]?.section ?? []).flatMap((section) => [
      section.title,
      ...(section.section ?? []).map(({ title }) => title),
    ]);
    expect(titles).toContain("4.1 Indications");
    expect(titles).not.toContain("4.1 Therapeutic indications");
  });

  // With APPROVAL_ENFORCEMENT on or off: the gate refuses before any approval is asked for, and a
  // dry run, which persists nothing, is not asked.
  it.each([false, true])(
    "is refused before anything is written when DRY_RUN is false (enforcement %s)",
    async (enforced) => {
      const error: unknown = await run(imported(), {
        ...config,
        DRY_RUN: false,
        APPROVAL_ENFORCEMENT: enforced,
      }).then(
        () => undefined,
        (cause: unknown) => cause,
      );
      expect(error).toBeInstanceOf(SubmissionRejectedError);
      expect((error as SubmissionRejectedError).issues).toEqual([CERTIFIED_WORD_NOT_RECOMPUTED]);
    },
  );

  it("runs dry with APPROVAL_ENFORCEMENT on, unasked", async () => {
    const result = await run(imported(), { ...config, APPROVAL_ENFORCEMENT: true });
    expect(result.status).toBe("validated");
  });

  it("keeps its namespace from every other route", async () => {
    const source = createSyntheticType2Bundle(mapping);
    source.identifier = {
      system: "https://khs.dev/fhir/identifier/type2-document",
      value: "certified-word:00000000-5979-4e74-8000-0000000000d0",
    };
    await expect(
      runPipeline(
        {
          runId: "00000000-0000-4000-8000-00000000c0df",
          source,
          sourceKind: "fixture",
          sourceResource: "fixture:test",
        },
        mapping,
        config,
      ),
    ).rejects.toThrow("Source identifier is in the reserved certified-word namespace");
  });
});
