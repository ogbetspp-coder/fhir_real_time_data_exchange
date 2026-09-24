import { readFileSync } from "node:fs";

import { beforeAll, describe, expect, it } from "vitest";

import {
  IMPORTER_EXTRACTOR,
  ImportRefusedError,
  importPublication,
  sha256Bytes,
} from "../../src/authority/import.js";
import { syntheticPublication } from "../../src/authority/synthetic.js";
import { verifyDocumentSubmission, type ImportRequest } from "../../src/contracts/index.js";
import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import {
  hasValidationErrors,
  validateCanonicalPreflight,
  validateEmaPreflight,
} from "../../src/fhir/preflight.js";
import { transformType2ToEma } from "../../src/fhir/transform.js";
import { sha256 } from "../../src/lib/hash.js";
import { RUN, firstSubsection, mutated, sections, type Publication } from "./support.js";

// The authority importer (docs/design/authority-import-contract.md): what it makes of a
// publication, and the first check each refused publication fails, in the stated order (D10).

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

function refusal(publication: Publication, request: ImportRequest = publication.request): string {
  try {
    importPublication(request, publication, mapping, RUN);
  } catch (error) {
    if (error instanceof ImportRefusedError) return `${error.stage}: ${error.reason}`;
    throw error;
  }
  return "imported";
}

type Json = Record<string, unknown>;

describe("a synthetic publication", () => {
  it("imports as a Type 1 record the gate would accept once it recomputes it", () => {
    const publication = syntheticPublication(mapping);
    const { submission, fidelityReport, sourceText } = importPublication(
      publication.request,
      publication,
      mapping,
      RUN,
    );

    expect(submission.graphType).toBe("type1");
    expect(fidelityReport.status).toBe("passed");
    expect(fidelityReport.summary).toEqual({ total: 32, verified: 32 });
    expect(fidelityReport.coverage.uncoveredGaps).toBe(0);
    expect(sourceText.extractorVersion).toBe(IMPORTER_EXTRACTOR);
    expect(sourceText.pages).toHaveLength(32);
    expect(submission.bundle.identifier.value).toBe(
      `authority-import:synthetic:${publication.request.documentId}`,
    );
    const source = submission.provenance.sourceDocument;
    if (source.kind !== "authority-publication") throw new Error("an import");
    expect(source.document.sha256).toBe(sha256Bytes(publication.document));
    expect(source.sectionPages[0]).toEqual({
      page: 1,
      path: "Composition.section[0]",
      code: "200000029791",
    });
    expect(
      hasValidationErrors(validateCanonicalPreflight(submission.bundle as never, "type1")),
    ).toBe(false);
    // The gate accepts it only with the proof that it recomputed this very submission.
    expect(() =>
      verifyDocumentSubmission(
        { submission, fidelityReport, sourceText },
        mapping.sourceCodeSystem,
        { allowSyntheticSources: true, recomputedImport: { submissionSha256: sha256(submission) } },
      ),
    ).not.toThrow();
    // Through the crosswalk to the EMA's form, with the List naming the product's identity.
    const ema = transformType2ToEma(submission.bundle as never, mapping);
    expect(hasValidationErrors(validateEmaPreflight(ema.list, ema.documentBundle, mapping))).toBe(
      false,
    );
    expect(ema.list.title).toBe("Synthetic Paracetamol 500 mg tablets");
    expect((ema.list.extension as unknown[]).length).toBe(5);
  });

  it("is deterministic: the same bytes and run give the same submission", () => {
    const publication = syntheticPublication(mapping);
    const first = importPublication(publication.request, publication, mapping, RUN);
    const second = importPublication(
      publication.request,
      syntheticPublication(mapping),
      mapping,
      RUN,
    );
    expect(sha256(second)).toBe(sha256(first));
  });
});

describe("the first check a publication fails", () => {
  it("bytes and shape", () => {
    const publication = syntheticPublication(mapping);
    expect(
      refusal({ ...publication, document: new Uint8Array([0xef, 0xbb, 0xbf, 0x7b, 0x7d]) }),
    ).toBe("bytes: document-byte-order-mark");
    expect(refusal({ ...publication, index: new TextEncoder().encode('{"id":1,"id":2}') })).toBe(
      "bytes: list-duplicate-json-key",
    );
    expect(refusal(mutated(mapping, (document) => (document.extra = true)))).toBe(
      "shape: document-shape",
    );
    expect(refusal(mutated(mapping, (_, list) => (list.status = "retired")))).toBe(
      "shape: list-shape",
    );
    expect(
      refusal(
        mutated(mapping, (_, list) => {
          (list.subject as Json).extension = ((list.subject as Json).extension as Json[]).filter(
            ({ url }) => !String(url).endsWith("procedureNumber"),
          );
        }),
      ),
    ).toBe("shape: list-procedure-missing");
    expect(
      refusal(publication, { ...publication.request, documentId: publication.request.indexId }),
    ).toBe("shape: document-is-not-the-requested-one");
    expect(refusal(publication, { ...publication.request, authority: "EMA" })).toBe(
      "shape: real-publication-with-a-synthetic-value",
    );
  });

  it("the binding of the document to its List", () => {
    expect(
      refusal(
        mutated(mapping, (_, list) => {
          ((list.entry as Json[])[0]?.item as Json).reference =
            "Bundle/00000000-5979-4e74-8000-000000000009";
        }),
      ),
    ).toBe("binding: document-not-listed-once");
    expect(
      refusal(
        mutated(mapping, (_, list) => {
          ((list.entry as Json[])[0]?.item as Json).display = "Package Leaflet (English)";
        }),
      ),
    ).toBe("binding: listed-as-another-document-type");
    expect(refusal(mutated(mapping, (_, list) => (list.title = "Synthetic Other")))).toBe(
      "binding: title-differs-from-the-list",
    );
  });

  it("the section tree, then the headings", () => {
    expect(
      refusal(
        mutated(mapping, (document) => {
          const root = sections(document)[0] as Json;
          (root.section as Json[]).reverse();
        }),
      ),
    ).toBe("tree: section-tree-differs");
    expect(
      refusal(
        mutated(mapping, (document) => {
          const root = sections(document)[0] as Json;
          (root.section as Json[]).pop();
        }),
      ),
    ).toBe("tree: section-tree-differs");
    expect(
      refusal(
        mutated(mapping, (document) => {
          const first = firstSubsection(document);
          first.title = "1. NAME";
        }),
      ),
    ).toBe("titles: heading-not-permitted");
  });

  it("pictures, then the narrative", () => {
    const withDiv = (div: string) =>
      mutated(mapping, (document) => {
        const first = firstSubsection(document);
        first.text = { status: "generated", div };
      });
    const div = (inner: string) => `<div xmlns="http://www.w3.org/1999/xhtml">${inner}</div>`;
    expect(
      refusal(
        withDiv(
          div('<p>x <img src="~/_entity/annotation/0a8aaa81-0000-4000-8000-000000000000"/></p>'),
        ),
      ),
    ).toBe("pictures: picture-reference-without-template-or-evidence");
    expect(refusal(withDiv(div('<p><img src="data:image/png;base64,AA=="/></p>')))).toBe(
      "pictures: pictures-not-enabled",
    );
    expect(refusal(withDiv(div('<p><img src="SIGRE"/></p>')))).toBe(
      "pictures: picture-reference-in-no-known-grammar",
    );
    expect(refusal(withDiv(div("<p><img/></p>")))).toBe("pictures: picture-without-a-source");
    expect(refusal(withDiv(div('<p style="color:red">x; not for clinical use</p>')))).toBe(
      "narrative: scanner-forbidden-attribute",
    );
    expect(refusal(withDiv(div("<p>x⁠; not for clinical use</p>")))).toBe(
      "narrative: invisible-character",
    );
    expect(refusal(withDiv(div("<p>&#160;</p>")))).toBe("record: section-draws-nothing");
  });
});

describe("the pinned EMA labels", () => {
  const LABELS = "labels/ema-epi";
  const lock = JSON.parse(readFileSync(`${LABELS}/sources.lock.json`, "utf8")) as {
    sources: { file: string; url: string; list: string; listFile: string }[];
  };
  const id = (url: string): string => url.split("/").at(-1) ?? "";

  // Each refuses at the first check it fails, as recorded here when the importer was built:
  // Imatinib Teva passes the shape, binding, tree and headings and stops at its two picture
  // references, which PR 3 resolves; Nuvaxovid stops at its pictures; Brukinsa keeps the
  // template's brackets in two headings; Jentadueto has uncoded subheadings.
  const expected: Record<string, string> = {
    "imatinib-teva-smpc-en.json": "pictures: picture-reference-without-template-or-evidence",
    "nuvaxovid-smpc-en.json": "pictures: pictures-not-enabled",
    "brukinsa-smpc-en.json": "titles: heading-not-permitted",
    "jentadueto-smpc-en.json": "shape: document-shape",
  };

  it("refuse where the design records they do", () => {
    expect(Object.keys(expected).sort()).toEqual(lock.sources.map(({ file }) => file).sort());
    for (const source of lock.sources) {
      const publication = {
        request: {
          authority: "EMA" as const,
          documentId: id(source.url),
          indexId: id(source.list),
          language: "en" as const,
        },
        document: readFileSync(`${LABELS}/sources/${source.file}`),
        index: readFileSync(`${LABELS}/lists/${source.listFile}`),
      };
      expect(refusal(publication), source.file).toBe(expected[source.file]);
    }
  });
});
