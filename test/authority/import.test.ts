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

// The first item of a list, which the synthetic publication always has.
function only(list: unknown): Json {
  const [first] = list as (Json | undefined)[];
  if (first === undefined) throw new Error("the synthetic publication has it");
  return first;
}

describe("each stated rule, on its own", () => {
  const subject = (list: Json) => list.subject as Json;
  const extensions = (list: Json) => subject(list).extension as Json[];
  const coding = (document: Json) =>
    ((only(document.entry).resource as Json).type as Json).coding as Json[];

  it("refuses the shape's closed values", () => {
    expect(
      refusal(
        mutated(mapping, (document) => {
          (only(document.entry).resource as Json).language = 5;
        }),
      ),
    ).toBe("shape: document-shape");
    expect(
      refusal(
        mutated(mapping, (document) => {
          const entries = document.entry as Json[];
          entries.push(structuredClone(only(entries)));
        }),
      ),
    ).toBe("shape: document-shape");
    expect(refusal(mutated(mapping, (_, list) => (list.title = "Synthetic\u0007")))).toBe(
      "shape: list-shape",
    );
    const publication = syntheticPublication(mapping);
    const text = new TextDecoder()
      .decode(publication.document)
      .replace('"language": 0', '"language": 0.0');
    expect(refusal({ ...publication, document: new TextEncoder().encode(text) })).toBe(
      "bytes: document-non-canonical-number",
    );
    expect(
      refusal(
        mutated(mapping, (_, list) => {
          extensions(list).push(structuredClone(only(extensions(list))));
        }),
      ),
    ).toBe("shape: list-extension-repeated");
    expect(
      refusal(
        mutated(mapping, (_, list) => {
          subject(list).extension = extensions(list).filter(
            ({ url }) => !String(url).endsWith("versionNumber"),
          );
        }),
      ),
    ).toBe("shape: list-version-missing");
  });

  it("refuses a List other than the requested one, and synthetic values mixed with real ones", () => {
    const publication = syntheticPublication(mapping);
    expect(
      refusal(publication, { ...publication.request, indexId: publication.request.documentId }),
    ).toBe("shape: list-is-not-the-requested-one");
    expect(
      refusal(
        mutated(mapping, (document) => {
          (document.identifier as Json).value = "1286255d-f544-ef11-a317-000d3aaa05e0";
        }),
      ),
    ).toBe("shape: synthetic-publication-with-a-real-id");
    expect(
      refusal(
        mutated(mapping, (_, list) => {
          const holder = extensions(list).find(({ url }) =>
            String(url).endsWith("marketingAuthorisationHolder"),
          );
          (only([holder]).valueCoding as Json).code = "ORG-100001110";
        }),
      ),
    ).toBe("shape: synthetic-publication-with-a-real-value");
  });

  it("refuses a document the List lists twice, or in another language", () => {
    expect(
      refusal(
        mutated(mapping, (_, list) => {
          const entries = list.entry as Json[];
          entries.push(structuredClone(only(entries)));
        }),
      ),
    ).toBe("binding: document-not-listed-once");
    expect(
      refusal(
        mutated(mapping, (document, list) => {
          const display = "Summary of Product Characteristics (Danish)";
          only(coding(document)).display = display;
          (only(list.entry).item as Json).display = display;
        }),
      ),
    ).toBe("binding: language-differs-from-the-request");
  });

  it("refuses a mapping that does not alias the EMA's section system", () => {
    const publication = syntheticPublication(mapping);
    const unaliased = { ...mapping, targetCodeSystemAliases: [] };
    try {
      importPublication(publication.request, publication, unaliased, RUN);
      throw new Error("imported");
    } catch (error) {
      expect(error).toBeInstanceOf(ImportRefusedError);
      expect((error as ImportRefusedError).reason).toBe("section-code-system-not-aliased");
    }
  });

  it("refuses a leaf without text, and a section the mapping needs that draws nothing", () => {
    expect(
      refusal(
        mutated(mapping, (document) => {
          delete firstSubsection(document).text;
        }),
      ),
    ).toBe("record: section-draws-nothing");
    expect(
      refusal(
        mutated(mapping, (document) => {
          const find = (list: Json[]): Json | undefined => {
            for (const candidate of list) {
              if (candidate.id === "smpc.4.8") return candidate;
              const inner = find((candidate.section as Json[] | undefined) ?? []);
              if (inner !== undefined) return inner;
            }
            return undefined;
          };
          const section = find(sections(document));
          if (section === undefined) throw new Error("4.8");
          section.text = {
            status: "generated",
            div: '<div xmlns="http://www.w3.org/1999/xhtml"><p>&#160;</p></div>',
          };
        }),
      ),
    ).toBe("record: section-draws-nothing");
  });

  it("records a decision for every value the record takes by rule", () => {
    const publication = syntheticPublication(mapping);
    const { submission } = importPublication(publication.request, publication, mapping, RUN);
    const targets = new Set(submission.provenance.decisions.map(({ target }) => target));
    for (const target of [
      "Bundle.type",
      "Bundle.id",
      "Bundle.meta.profile",
      "Bundle.entry[0].fullUrl",
      "Composition.id",
      "RegulatedAuthorization.meta.profile",
      "Composition.section[0].id",
      "Composition.section[0].text.status",
    ]) {
      expect(targets.has(target), target).toBe(true);
    }
  });

  it("refuses two authors, a List entry that is not a document's GUID, and each synthetic mark on a real publication", () => {
    expect(
      refusal(
        mutated(mapping, (document) => {
          const composition = only(document.entry).resource as Json;
          composition.author = [
            { identifier: { system: "http://www.test.com", value: "a" } },
            { identifier: { system: "http://www.test.com", value: "b" } },
          ];
        }),
      ),
    ).toBe("shape: document-shape");
    expect(
      refusal(
        mutated(mapping, (_, list) => {
          (only(list.entry).item as Json).reference = `Bundle/${"-".repeat(36)}`;
        }),
      ),
    ).toBe("shape: list-shape");
    const real = { authority: "EMA" as const };
    const realIds = (document: Json, list: Json) => {
      document.id = "1286255d-f544-ef11-a317-000d3aaa05e0";
      (document.identifier as Json).value = "1286255d-f544-ef11-a317-000d3aaa05e0";
      list.id = "f2b36255-f544-ef11-b4ad-6045bd9c274b";
      (only(list.entry).item as Json).reference = "Bundle/1286255d-f544-ef11-a317-000d3aaa05e0";
    };
    const request = {
      ...syntheticPublication(mapping).request,
      ...real,
      documentId: "1286255d-f544-ef11-a317-000d3aaa05e0",
      indexId: "f2b36255-f544-ef11-b4ad-6045bd9c274b",
    };
    // Real ids, synthetic values.
    expect(refusal(mutated(mapping, realIds), request)).toBe(
      "shape: real-publication-with-a-synthetic-value",
    );
    // Real values, one synthetic id.
    const realValues = (document: Json, list: Json) => {
      realIds(document, list);
      (document.identifier as Json).value = "00000000-5979-4e74-8000-000000000003";
      list.identifier = [{ system: "http://ema.europa.eu/fhir/epiId", value: "EPI/24/35" }];
      for (const extension of (list.subject as Json).extension as Json[]) {
        const valueCoding = extension.valueCoding as Json | undefined;
        if (valueCoding !== undefined) valueCoding.code = "ORG-100001110";
        const valueIdentifier = extension.valueIdentifier as Json | undefined;
        if (valueIdentifier !== undefined) valueIdentifier.value = "EMEA/H/C/002585/IA/0056";
      }
    };
    expect(refusal(mutated(mapping, realValues), request)).toBe(
      "shape: real-publication-with-a-synthetic-value",
    );
  });

  it("reads the language only from the closed display suffix", () => {
    for (const display of [
      "Summary of Product Characteristics (English) (Danish)",
      "Summary of Product Characteristics(English)",
    ]) {
      expect(
        refusal(
          mutated(mapping, (document, list) => {
            only(coding(document)).display = display;
            (only(list.entry).item as Json).display = display;
          }),
        ),
        display,
      ).toBe("binding: language-differs-from-the-request");
    }
  });

  it("carries a heading section that draws nothing without text, and its page blank", () => {
    const publication = mutated(mapping, (document) => {
      const [root] = sections(document) as [Json];
      const [clinical] = (root.section as Json[]).filter((section) => section.id === "smpc.4") as [
        Json,
      ];
      clinical.text = {
        status: "generated",
        div: '<div xmlns="http://www.w3.org/1999/xhtml"><p>&#160;</p></div>',
      };
    });
    const { submission, sourceText } = importPublication(
      publication.request,
      publication,
      mapping,
      RUN,
    );
    const composition = submission.bundle.entry[0]?.resource as unknown as {
      section: { section: { code: { coding: { code: string }[] }; text?: unknown }[] }[];
    };
    const clinical = composition.section[0]?.section.find(
      (section) => section.code.coding[0]?.code === "smpc.4",
    );
    expect(clinical?.text).toBeUndefined();
    const source = submission.provenance.sourceDocument;
    if (source.kind !== "authority-publication") throw new Error("an import");
    const page = source.sectionPages.find(({ code }) => code === "200000029798");
    expect(page).toBeDefined();
    expect(sourceText.pages[(page?.page ?? 0) - 1]?.text.trim().replace(/\u00a0/gu, "")).toBe("");
  });
});
