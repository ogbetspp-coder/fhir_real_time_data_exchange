import { beforeAll, describe, expect, it } from "vitest";

import { AuthorityFetchError, type AuthorityFetcher } from "../../src/authority/fetch.js";
import { copiesFetcher, verifyAuthorityImport } from "../../src/authority/gate.js";
import { IMPORTER_VERSION, importPublication, sha256Bytes } from "../../src/authority/import.js";
import { emaShapedPublication, syntheticPublication } from "../../src/authority/synthetic.js";
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
    expect(result.importerVersion).toBe("2.3.1");
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

  it("refuses an import another importer version made, and fetches nothing", async () => {
    const unreachable: AuthorityFetcher = {
      fetch: () => Promise.reject(new Error("the gate fetched")),
    };
    const made = (parserVersion: string, extractorVersion: string) => {
      const input = imported();
      input.submission.provenance.extraction.parser.version = parserVersion;
      const source = input.submission.provenance.sourceDocument;
      if (source.kind !== "authority-publication") throw new Error("import");
      source.extractedText.extractorVersion = extractorVersion;
      reseal(input.submission);
      return input;
    };
    // Another version, named consistently: the gate's own check.
    expect(await issues(made("0.9.0", "authority-import/0.9.0"), unreachable)).toEqual([
      "The submission was made by another importer version than the gate runs",
    ]);
    // Either field alone changed: the parse refuses the disagreement first.
    const disagreeing =
      "sourceDocument.extractedText.extractorVersion must be extraction.parser's name/version";
    expect(
      await issues(made("0.9.0", `authority-import/${IMPORTER_VERSION}`), unreachable),
    ).toContain(disagreeing);
    expect(await issues(made(IMPORTER_VERSION, "authority-import/0.9.0"), unreachable)).toContain(
      disagreeing,
    );
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

  // Every stage but the last passes the EMA-shaped publication; the gate names the missing
  // renderer evidence as the importer's refusal (the renderer gate is not wired in yet).
  it("refuses an authority's publication for want of the renderer's evidence", async () => {
    const ema = emaShapedPublication(mapping);
    // A submission pinned to those files, as a producer that skipped the importer might write.
    const input = imported();
    const { submission } = input;
    const source = submission.provenance.sourceDocument;
    const { approval } = submission;
    if (source.kind !== "authority-publication" || approval.method !== "authority-publication") {
      throw new Error("import");
    }
    source.authority = "EMA";
    source.request = ema.request;
    source.document = {
      id: ema.request.documentId,
      sha256: sha256Bytes(ema.document),
      byteLength: ema.document.length,
    };
    Object.assign(source.index, {
      id: ema.request.indexId,
      sha256: sha256Bytes(ema.index),
      byteLength: ema.index.length,
    });
    approval.authority = "EMA";
    approval.publication.documentId = ema.request.documentId;
    approval.publication.indexId = ema.request.indexId;
    reseal(submission);
    expect(await issues(input, serving(ema))).toEqual([
      "The import refuses the authority's files at rendering: renderer-evidence-missing",
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

  // The parse's refinement hashes the Bundle, so the shape bound has to come first, as in the
  // ordinary gate: a pathological document is a classified rejection, never a RangeError.
  it("bounds the submission's shape before it parses or hashes it, and fetches nothing", async () => {
    const input = imported();
    let nested: unknown = "x";
    for (let depth = 0; depth < 20_000; depth += 1) nested = [nested];
    (input.submission.bundle as unknown as Record<string, unknown>).extension = nested;
    const unreachable: AuthorityFetcher = {
      fetch: () => Promise.reject(new Error("the gate fetched")),
    };

    expect(await issues(input, unreachable)).toEqual(["submission nesting exceeds depth 48"]);
  });

  it("refuses a member the parse would drop, and fetches nothing", async () => {
    const { submission, ...rest } = imported();
    const text = JSON.stringify(submission).replace(
      '"bundle":{',
      '"bundle":{"__proto__":{"note":"Take one tablet twice daily with food."},',
    );
    const unreachable: AuthorityFetcher = {
      fetch: () => Promise.reject(new Error("the gate fetched")),
    };

    expect(await issues({ ...rest, submission: JSON.parse(text) }, unreachable)).toEqual([
      "submission carries the reserved property name __proto__",
    ]);
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
