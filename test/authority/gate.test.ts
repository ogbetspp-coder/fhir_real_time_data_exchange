import { beforeAll, describe, expect, it } from "vitest";

import { AuthorityFetchError, type AuthorityFetcher } from "../../src/authority/fetch.js";
import { copiesFetcher, verifyAuthorityImport } from "../../src/authority/gate.js";
import { importPublication, sha256Bytes } from "../../src/authority/import.js";
import { syntheticPublication } from "../../src/authority/synthetic.js";
import {
  SubmissionRejectedError,
  approvedContent,
  type CanonicalSubmission,
  type DocumentSubmissionInput,
} from "../../src/contracts/index.js";
import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import { createSyntheticSubmission } from "../../src/fixtures/synthetic-submission.js";
import { sha256 } from "../../src/lib/hash.js";
import { RUN, mutated, type Publication } from "./support.js";

// Zone B's gate for an authority import recomputes it from the files it fetched itself and
// accepts only the very submission, page text and report the importer makes of them (D1).

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

const OPTIONS = { allowSyntheticSources: true, dryRun: true };
const FETCHED_AT = "2026-09-24T12:00:00.000Z";

// A fetcher that serves exactly these bytes.
function serving(publication: Publication): AuthorityFetcher {
  return {
    fetch: (_authority, file) =>
      Promise.resolve({
        url: `https://synthetic.invalid/${file.kind}/${file.id}`,
        bytes: file.kind === "document" ? publication.document : publication.index,
        fetchedAt: FETCHED_AT,
      }),
  };
}

function imported(
  publication: Publication = syntheticPublication(mapping),
): DocumentSubmissionInput & {
  submission: CanonicalSubmission;
} {
  const { submission, fidelityReport, sourceText } = importPublication(
    publication.request,
    publication,
    mapping,
    RUN,
  );
  return { submission, fidelityReport, sourceText };
}

function reseal(submission: CanonicalSubmission): CanonicalSubmission {
  submission.bundleSha256 = sha256(submission.bundle);
  submission.approval.approvedContentSha256 = sha256(approvedContent(submission));
  return submission;
}

async function issues(
  input: DocumentSubmissionInput,
  fetcher: AuthorityFetcher = serving(syntheticPublication(mapping)),
  options = OPTIONS,
): Promise<string[]> {
  try {
    await verifyAuthorityImport(input, mapping, options, fetcher);
  } catch (error) {
    if (error instanceof SubmissionRejectedError) return error.issues;
    throw error;
  }
  return [];
}

describe("the authority gate", () => {
  it("accepts the import it recomputes, and says what it fetched and which importer ran", async () => {
    const result = await verifyAuthorityImport(
      imported(),
      mapping,
      OPTIONS,
      serving(syntheticPublication(mapping)),
    );
    expect(result.importerVersion).toBe("2.0.0");
    expect(result.fetched.map(({ url }) => url.split("/")[3])).toEqual(["document", "index"]);
    expect(result.gate.submission.graphType).toBe("type1");
  });

  it("re-verifies a recorded run from copies of what was fetched", async () => {
    const publication = syntheticPublication(mapping);
    const copies = copiesFetcher({
      document: { url: "copy:document", bytes: publication.document, fetchedAt: FETCHED_AT },
      index: { url: "copy:index", bytes: publication.index, fetchedAt: FETCHED_AT },
    });
    expect(await issues(imported(), copies)).toEqual([]);
    const other = mutated(mapping, (document) => (document.timestamp = "2026-09-25T00:00:00Z"));
    const altered = copiesFetcher({
      document: { url: "copy:document", bytes: other.document, fetchedAt: FETCHED_AT },
      index: { url: "copy:index", bytes: publication.index, fetchedAt: FETCHED_AT },
    });
    expect(await issues(imported(), altered)).toEqual([
      "The authority now serves another document than the one pinned",
    ]);
  });

  it("refuses to run an import other than dry, before PR 5 can publish one", async () => {
    expect(await issues(imported(), undefined, { ...OPTIONS, dryRun: false })).toEqual([
      "An authority import runs only as a dry run until it can be published",
    ]);
  });

  it("refuses when the authority serves other bytes, or cannot serve them", async () => {
    const other = mutated(mapping, (document) => (document.timestamp = "2026-09-25T00:00:00Z"));
    expect(await issues(imported(), serving(other))).toEqual([
      "The authority now serves another document than the one pinned",
    ]);
    const failing: AuthorityFetcher = {
      fetch: () => Promise.reject(new AuthorityFetchError("http-404")),
    };
    expect(await issues(imported(), failing)).toEqual([
      "The authority's document could not be fetched: http-404",
    ]);
  });

  it("refuses a submission that is not what the importer makes of the files", async () => {
    const input = imported();
    const product = input.submission.bundle.entry[1]?.resource as unknown as {
      name: { productName: string }[];
    };
    product.name = [{ productName: "Synthetic Other 1 mg tablets" }];
    reseal(input.submission);
    expect(await issues(input)).toEqual([
      "The submission is not what the importer makes of the authority's files",
    ]);
  });

  it("refuses page text or a report the importer did not make", async () => {
    const pages = imported();
    const page = pages.sourceText as { pages: { text: string }[] };
    if (page.pages[0] !== undefined) page.pages[0].text = "other\n";
    expect(await issues(pages)).toEqual([
      "The page text is not what the importer makes of the authority's files",
    ]);
    const report = imported();
    (report.fidelityReport as { issues: string[] }).issues = ["other"];
    expect(await issues(report)).toEqual(["The fidelity report is not the recomputed one"]);
  });

  it("refuses an import another importer version made", async () => {
    const input = imported();
    const version = "0.9.0";
    input.submission.provenance.extraction.parser.version = version;
    const source = input.submission.provenance.sourceDocument;
    if (source.kind !== "authority-publication") throw new Error("import");
    source.extractedText.extractorVersion = `authority-import/${version}`;
    reseal(input.submission);
    expect(await issues(input)).toEqual([
      "The submission is not what the importer makes of the authority's files",
    ]);
  });

  it("refuses an import requested after the gate fetched the files", async () => {
    const input = imported();
    if (input.submission.approval.method !== "authority-publication") throw new Error("import");
    input.submission.approval.requestedAt = "2026-09-24T13:00:00Z";
    reseal(input.submission);
    expect(await issues(input)).toEqual([
      "The import was requested after the gate fetched the authority's files",
    ]);
  });

  it("refuses pinned files the importer refuses, naming the stage", async () => {
    const refused = mutated(mapping, (_, list) => (list.title = "Synthetic Other"));
    // A submission pinned to those bytes, as a producer that skipped the importer might write.
    const input = imported();
    const source = input.submission.provenance.sourceDocument;
    if (source.kind !== "authority-publication") throw new Error("import");
    source.document.sha256 = sha256Bytes(refused.document);
    source.document.byteLength = refused.document.length;
    source.index.sha256 = sha256Bytes(refused.index);
    source.index.byteLength = refused.index.length;
    reseal(input.submission);
    expect(await issues(input, serving(refused))).toEqual([
      "The import refuses the authority's files at binding: title-differs-from-the-list",
    ]);
  });

  it("refuses a drawn submission, one without a report location, and one that does not parse", async () => {
    const drawn = createSyntheticSubmission(mapping);
    expect(await issues(drawn)).toEqual(["Not an authority import"]);
    const unlocated = imported();
    delete unlocated.submission.provenance.fidelity.reportUri;
    reseal(unlocated.submission);
    expect(await issues(unlocated)).toEqual(["An authority import names its report's location"]);
    const found = await issues({ ...imported(), submission: { schemaVersion: "2.0.0" } });
    expect(found.length).toBeGreaterThan(0);
  });
});

describe("a structured source's uncovered pages", () => {
  it("refuses a record that drops a section's words its page still holds", async () => {
    const { verifyDocumentSubmission } = await import("../../src/contracts/index.js");
    const { collectNarrativeSections, verifyNarrativeFidelity, NORMALIZATION_VERSION } =
      await import("../../src/fidelity/index.js");
    const input = imported();
    const { submission } = input;
    // Drop the narrative of the Composition's last leaf section, and its provenance, as a record
    // that lost a section's words would; the page text keeps them.
    const composition = submission.bundle.entry[0]?.resource as unknown as {
      section: { section?: { text?: unknown }[] }[];
    };
    const leaves = composition.section[0]?.section ?? [];
    const dropped = leaves.at(-1);
    if (dropped === undefined) throw new Error("a leaf section");
    delete dropped.text;
    const narratives = collectNarrativeSections(composition as never, mapping.sourceCodeSystem);
    const keys = new Set(narratives.map(({ sourceKey }) => sourceKey));
    submission.provenance.sections = submission.provenance.sections.filter(({ sourceKey }) =>
      keys.has(sourceKey),
    );
    const report = verifyNarrativeFidelity({
      normalizationVersion: NORMALIZATION_VERSION,
      source: input.sourceText as never,
      sections: narratives,
      provenance: submission.provenance.sections,
    });
    expect(report.coverage.uncoveredGaps).toBe(1);
    Object.assign(submission.provenance.fidelity, {
      sectionsChecked: report.summary.total,
      sectionsMatched: report.summary.verified,
      narrativeBindingSha256: report.narrativeBindingSha256,
      reportSha256: report.reportHash,
    });
    reseal(submission);

    let found: string[] = [];
    try {
      verifyDocumentSubmission(
        { submission, fidelityReport: report, sourceText: input.sourceText },
        mapping.sourceCodeSystem,
        { allowSyntheticSources: true, recomputedImport: { submissionSha256: sha256(submission) } },
      );
    } catch (error) {
      if (error instanceof SubmissionRejectedError) found = error.issues;
      else throw error;
    }
    expect(found).toContain(
      "A page of the authority's document that no narrative covers is not blank",
    );
  });
});
