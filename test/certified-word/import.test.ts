import { readFileSync } from "node:fs";

import { beforeAll, describe, expect, it } from "vitest";

import {
  CANONICAL_ORGANIZATION_SYSTEM,
  CANONICAL_PRODUCT_SYSTEM,
  CERTIFIED_WORD_IMPORTER,
  CertifiedWordRefusedError,
  IMPORTER_VERSION,
  certifiedWordExtractor,
  importCertifiedWord,
} from "../../src/certified-word/import.js";
import {
  RUN,
  caseRequest,
  recomputed,
  recomputedCases,
  type RecomputedCase,
} from "../../src/certified-word/vectors.js";
import { CanonicalSubmissionSchema, type CanonicalSubmission } from "../../src/contracts/index.js";
import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import { validateCanonicalPreflight } from "../../src/fhir/preflight.js";
import type { FhirBundle } from "../../src/fhir/types.js";
import { xhtmlToText } from "../../src/fidelity/index.js";
import { sha256 } from "../../src/lib/hash.js";

// The certified Word importer (docs/design/certified-word-import.md, D1) on what zone_a.recompute
// wrote for the synthetic Word labels of test/fixtures/certified-word/recompute/: what it makes,
// and each refusal, by stage and closed reason.

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

type Json = Record<string, unknown>;
type Section = { title: string; code: { coding: { code: string }[] }; section?: Section[] };

function found(name: string): RecomputedCase {
  const match = recomputedCases().find((candidate) => candidate.name === name);
  if (match === undefined) throw new Error(`no case ${name}`);
  return match;
}

function result(name: string): Json {
  return JSON.parse(new TextDecoder().decode(recomputed(name))) as Json;
}

function sections(json: Json): Json[] {
  return json.sections as Json[];
}

// The list's item at `index`, which the test needs to be there.
function at<T>(list: T[], index: number): T {
  const item = list.at(index);
  if (item === undefined) throw new Error(`no item ${String(index)}`);
  return item;
}

function bytes(json: unknown): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(json));
}

function imported(name: string) {
  return importCertifiedWord(recomputed(name), caseRequest(found(name)), mapping, RUN);
}

// Each section of the record in pre-order, with its key.
function recordSections(submission: CanonicalSubmission): { key: string; section: Json }[] {
  const out: { key: string; section: Json }[] = [];
  const walk = (list: Section[] | undefined): void => {
    for (const entry of list ?? []) {
      out.push({ key: entry.code.coding[0]?.code ?? "", section: entry });
      walk(entry.section);
    }
  };
  walk((submission.bundle.entry[0]?.resource as { section?: Section[] }).section);
  return out;
}

// The first line of a section's page in a result.
function firstPageLine(json: Json, key: string): string | undefined {
  const page = sections(json).find((entry) => entry.key === key)?.page;
  return String(page)
    .split("\n")
    .find((line) => line.length > 0);
}

function refusal(run: () => unknown): string {
  try {
    run();
  } catch (error) {
    if (error instanceof CertifiedWordRefusedError) return `${error.stage}: ${error.reason}`;
    throw error;
  }
  return "imported";
}

describe("what the importer makes of a recomputed Word label", () => {
  it("carries every section's title, narrative and page exactly as the recompute gave them", () => {
    for (const name of ["smpc", "smpc-tracked", "smpc-assigned"]) {
      const { submission, sourceText, fidelityReport } = imported(name);
      const recomputedSections = sections(result(name));
      const record = recordSections(submission);
      expect(record.map(({ key }) => key)).toEqual(recomputedSections.map(({ key }) => key));
      record.forEach(({ section: carried }, index) => {
        const from = at(recomputedSections, index);
        expect(carried.title).toBe(from.title);
        expect((carried.text as { div?: string } | undefined)?.div ?? null).toBe(from.narrative);
        expect(sourceText.pages[index]?.text).toBe(from.page);
      });
      expect(sourceText.pages.map(({ page }) => page)).toEqual(record.map((_, at) => at + 1));
      expect(fidelityReport.status).toBe("passed");
      expect(fidelityReport.coverage.uncoveredGaps).toBe(0);
      expect(CanonicalSubmissionSchema.safeParse(submission).success).toBe(true);
    }
  });

  it("carries an assigned heading as written, not the template's", () => {
    const assigned = recordSections(imported("smpc-assigned").submission);
    const plain = recordSections(imported("smpc").submission);
    const title = (record: typeof plain): unknown =>
      record.find(({ key }) => key === "smpc.4.1")?.section.title;
    expect([title(assigned), title(plain)]).toEqual([
      "4.1 Indications",
      "4.1 Therapeutic indications",
    ]);
  });

  it("names the extractor by the hash of the importer and every version the recompute names", () => {
    const { submission, sourceText } = imported("smpc");
    const versions = result("smpc").versions as Record<string, string>;
    const record = { importer: CERTIFIED_WORD_IMPORTER, recompute: versions };
    const extractor = `certified-word/${sha256(record)}`;
    expect(CERTIFIED_WORD_IMPORTER).toBe(`certified-word-import/${IMPORTER_VERSION}`);
    expect(certifiedWordExtractor(versions as never)).toBe(extractor);
    expect(sourceText.extractorVersion).toBe(extractor);
    const source = submission.provenance.sourceDocument;
    if (source.kind !== "certified-word") throw new Error("not a certified Word source");
    expect(source.importer).toBe(CERTIFIED_WORD_IMPORTER);
    expect(source.extractedText.extractorVersion).toBe(extractor);
    expect(source.recompute.versions).toEqual(versions);
    expect(submission.provenance.extraction.parser).toEqual({
      name: "certified-word",
      version: sha256(record),
    });
    // Another importer, the same recompute: another extractor (review of #193).
    expect(sha256({ ...record, importer: "certified-word-import/0.0.0" })).not.toBe(sha256(record));
  });

  it("pins the uploaded bytes, the request and the view's changes", () => {
    const tracked = imported("smpc-tracked").submission.provenance.sourceDocument;
    if (tracked.kind !== "certified-word") throw new Error("not a certified Word source");
    const from = result("smpc-tracked");
    expect(tracked.document).toEqual({
      sha256: (from.source as Json).sha256,
      byteLength: (from.source as Json).bytes,
      filename: "smpc-tracked.docx",
      storageUri: "gs://synthetic-submissions/smpc-tracked.docx",
    });
    expect([tracked.recompute.view, tracked.changes]).toEqual(["accepted", 2]);
    expect(tracked.sectionPages[0]).toEqual({ page: 1, key: "smpc", code: "200000029791" });
  });

  it("builds a Type 1 graph from the confirmed product only, and the preflight passes it", () => {
    const { submission } = imported("smpc");
    const product = found("smpc").product;
    const resources = submission.bundle.entry.map(({ resource }) => resource as Json);
    expect(resources.map(({ resourceType }) => resourceType)).toEqual([
      "Composition",
      "MedicinalProductDefinition",
      "Organization",
      "RegulatedAuthorization",
      "RegulatedAuthorization",
    ]);
    const [composition, medicinal, holder, ...authorisations] = resources;
    expect(composition?.title).toBe(product.name);
    expect(medicinal?.name).toEqual([{ productName: product.name }]);
    expect(medicinal?.identifier).toEqual([
      { system: CANONICAL_PRODUCT_SYSTEM, value: product.id },
      { system: "https://khs.dev/fhir/identifier/eu-product-number", value: "EU/1/24/9999" },
    ]);
    expect(holder?.name).toBe(product.holder.name);
    expect(holder?.identifier).toEqual([
      { system: CANONICAL_ORGANIZATION_SYSTEM, value: product.holder.id },
    ]);
    expect(authorisations.map(({ identifier }) => identifier)).toEqual(
      product.euAuthorisationNumbers.map((value) => [
        { system: "https://khs.dev/fhir/identifier/eu-authorisation-number", value },
      ]),
    );
    expect(submission.graphType).toBe("type1");
    expect(submission.bundle.identifier.value).toBe(`certified-word:${found("smpc").documentId}`);
    const preflight = validateCanonicalPreflight(
      submission.bundle as unknown as FhirBundle,
      "type1",
    );
    expect(preflight.issue.map(({ severity }) => severity)).toEqual(["success"]);
  });

  it("carries the approval placeholder, bound to the content", () => {
    const { submission } = imported("smpc");
    expect(submission.approval).toEqual({
      ...caseRequest(found("smpc")).approval,
      meaning: "reviewed-fidelity-and-structure",
      approvedContentSha256: submission.approval.approvedContentSha256,
    });
  });

  it("makes the same submission from the same bytes and request", () => {
    expect(sha256(imported("smpc"))).toBe(sha256(imported("smpc")));
  });
});

describe("what the importer refuses, by stage", () => {
  const smpc = (): { json: Json; request: ReturnType<typeof caseRequest> } => ({
    json: result("smpc"),
    request: caseRequest(found("smpc")),
  });
  const refusedWith = (json: unknown, request: unknown, map = mapping): string =>
    refusal(() =>
      importCertifiedWord(json instanceof Uint8Array ? json : bytes(json), request, map, RUN),
    );

  it("a request that is not what a person confirmed", () => {
    const { json, request } = smpc();
    const product = request.product;
    for (const changed of [
      { ...request, extra: 1 },
      { ...request, documentId: "not-a-uuid" },
      { ...request, documentId: request.documentId.toUpperCase() },
      { ...request, product: { ...product, name: "" } },
      { ...request, product: { ...product, name: "Synthetic\tExampline" } },
      // Whitespace at an end is never what a person chose from the label (review of #193).
      { ...request, product: { ...product, name: "Synthetic Exampline " } },
      { ...request, product: { ...product, name: "\u00a0Synthetic Exampline" } },
      { ...request, product: { ...product, name: "Synthetic\u2028Exampline" } },
      {
        ...request,
        product: { ...product, holder: { ...product.holder, name: "Synthetic Holder B.V. " } },
      },
      { ...request, product: { ...product, euAuthorisationNumbers: ["EU/1/24/9999/001-002"] } },
      { ...request, product: { ...product, euAuthorisationNumbers: ["EU/1/24/9999"] } },
      // An SmPC states at least one in its section 8.
      { ...request, product: { ...product, euAuthorisationNumbers: [] } },
      { ...request, approval: { ...request.approval, approverId: "a person@example.org" } },
    ]) {
      expect(refusedWith(json, changed)).toBe("request: request-shape");
    }
    expect(
      refusedWith(json, { ...request, documentId: "00000000-0000-4000-8000-0000000000d0" }),
    ).toBe("request: synthetic-and-real-ids");
    const repeated = [...product.euAuthorisationNumbers, ...product.euAuthorisationNumbers];
    expect(
      refusedWith(json, { ...request, product: { ...product, euAuthorisationNumbers: repeated } }),
    ).toBe("request: eu-number-repeated");
  });

  it("bytes that are not strict JSON", () => {
    const { request } = smpc();
    const text = new TextDecoder().decode(recomputed("smpc"));
    const encode = (value: string): Uint8Array => new TextEncoder().encode(value);
    expect(refusedWith(encode(`\ufeff${text}`), request)).toBe("bytes: byte-order-mark");
    expect(refusedWith(Uint8Array.of(0x7b, 0xff, 0x7d), request)).toBe("bytes: invalid-utf-8");
    expect(refusedWith(encode('{"view":null,"view":null}'), request)).toMatch(/^bytes: /);
    expect(refusedWith(encode(text.slice(0, -10)), request)).toMatch(/^bytes: /);
  });

  it("the recompute's refusal", () => {
    expect(refusal(() => imported("smpc-refused"))).toBe("recompute: recompute-refused");
  });

  it("a result in another shape", () => {
    const { json, request } = smpc();
    const first = at(sections(json), 0);
    for (const changed of [
      { ...json, extra: 1 },
      { ...json, structure: { ...(json.structure as Json), ready: false } },
      { ...json, sections: [{ ...first, refusal: { code: "tab" } }, ...sections(json).slice(1)] },
      { ...json, sections: [{ ...first, key: "SMPC" }, ...sections(json).slice(1)] },
      { ...json, sections: [] },
    ]) {
      expect(refusedWith(changed, request)).toBe("shape: result-shape");
    }
  });

  it("a result that is not the one the request names", async () => {
    const { json, request } = smpc();
    const versions = json.versions as Json;
    const structure = json.structure as Json;
    const cases: [unknown, unknown, string][] = [
      [
        { ...json, versions: { ...versions, builder: "word-epi/0.0.0" } },
        request,
        "other-versions",
      ],
      [
        { ...json, structure: { ...structure, structurer: "smpc-structure/0.0.0" } },
        request,
        "structure-of-other-versions",
      ],
      [{ ...json, document: "pl" }, request, "other-document"],
      [{ ...json, view: "accepted" }, request, "other-view"],
      [{ ...json, part: 1 }, request, "other-part"],
      [
        json,
        { ...request, recompute: { ...request.recompute, assignments: { "smpc.4.1": 3 } } },
        "assignments-differ",
      ],
    ];
    for (const [changed, asked, reason] of cases) {
      expect(refusedWith(changed, asked)).toBe(`binding: ${reason}`);
    }
    // The headings a person assigned are bound both ways (review of #193): a result made with
    // {"smpc.4.1": 12} is not the one a request naming none, or another, names.
    const assigned = caseRequest(found("smpc-assigned"));
    const assignedResult = recomputed("smpc-assigned");
    expect(refusal(() => imported("smpc-assigned"))).toBe("imported");
    for (const assignments of [{}, { "smpc.4.2": 15 }, { "smpc.4.1": 12, "smpc.4.2": 15 }]) {
      expect(
        refusedWith(assignedResult, {
          ...assigned,
          recompute: { ...assigned.recompute, assignments },
        }),
      ).toBe("binding: assignments-differ");
    }
    // A result made for another document than the mapping maps.
    const leaflet = await loadEmaMapping("fhir/mappings/cap-pl-en.json");
    expect(refusedWith(json, request, leaflet)).toBe("binding: mapping-of-another-document");
    expect(refusedWith(recomputed("pl"), caseRequest(found("pl")))).toBe(
      "binding: mapping-of-another-document",
    );
    const other = { ...versions, mappingVersion: "9.9.9" };
    expect(
      refusedWith(
        { ...json, versions: other, structure: { ...structure, mappingVersion: "9.9.9" } },
        { ...request, recompute: { ...request.recompute, versions: other } },
      ),
    ).toBe("binding: other-mapping-version");
  });

  it("sections that are not the mapping's tree", () => {
    const { json, request } = smpc();
    const list = sections(json);
    const without = (key: string): Json[] => list.filter((entry) => entry.key !== key);
    const changed = (key: string, change: Json): Json[] =>
      list.map((entry) => (entry.key === key ? { ...entry, ...change } : entry));
    for (const tree of [
      [...list].reverse(),
      without("smpc.4.1"),
      [...list, { ...at(list, -1), key: "smpc.99" }],
      changed("smpc.4.1", { parent: "smpc" }),
      changed("smpc.4.1", { key: "smpc.custom.h4" }),
    ]) {
      expect(refusedWith({ ...json, sections: tree }, request)).toBe("tree: section-tree-differs");
    }
    expect(
      refusedWith({ ...json, sections: changed("smpc.4.1", { code: "200000029800" }) }, request),
    ).toBe("tree: section-code-differs");
  });

  it("a title that is not one line of plain text", () => {
    const { json, request } = smpc();
    for (const title of [
      "",
      " ",
      "\u00a0",
      "4.1 Therapeutic\nindications",
      "4.1\u00adTherapeutic",
      "4.1 Therapeutic\u2028indications",
      "4.1 Therapeutic\u2029indications",
    ]) {
      const list = sections(json).map((entry) =>
        entry.key === "smpc.4.1" ? { ...entry, title } : entry,
      );
      expect(refusedWith({ ...json, sections: list }, request)).toBe("titles: title-not-one-line");
    }
  });

  it("a narrative that is not its page, or draws nothing where it must", () => {
    const { json, request } = smpc();
    const change = (key: string, values: Json): Json => ({
      ...json,
      sections: sections(json).map((entry) =>
        entry.key === key ? { ...entry, ...values } : entry,
      ),
    });
    const root = '<div xmlns="http://www.w3.org/1999/xhtml" lang="en" xml:lang="en">';
    const cases: [Json, string][] = [
      [change("smpc.4.1", { narrative: null }), "page-without-narrative"],
      [change("smpc.4.1", { narrative: null, page: "" }), "section-draws-nothing"],
      [change("smpc.4.1", { narrative: `${root}<p>a</p>` }), "scanner-malformed-xml"],
      [
        change("smpc.4.1", { narrative: `${root}<p>a\u2060b, not for clinical use</p></div>` }),
        "invisible-character",
      ],
      [
        change("smpc.4.1", {
          page: String(sections(json).find(({ key }) => key === "smpc.4.1")?.page).replace(
            "Bold",
            "Bo\u2060ld",
          ),
        }),
        "invisible-character",
      ],
      [change("smpc.4.1", { page: "\nAnother text.\n" }), "narrative-differs-from-page"],
      [change("smpc.4.1", { page: "\nA\u0001.\n" }), "not-normalisable"],
      [
        change("smpc.4.1", { narrative: `${root}<p>\u00a0</p></div>`, page: "\n\u00a0\n" }),
        "narrative-draws-nothing",
      ],
    ];
    for (const [changed, reason] of cases) {
      expect(refusedWith(changed, request)).toMatch(
        reason.startsWith("scanner-")
          ? /^narrative: scanner-/
          : new RegExp(`^narrative: ${reason}$`),
      );
    }
  });

  it("a product that is not the label's", () => {
    const { json, request } = smpc();
    const { product } = request;
    const asked = (change: Partial<typeof product>): unknown => ({
      ...request,
      product: { ...product, ...change },
    });
    // The name begins section 1's first line ("Synthetic Exampline 10 mg film-coated tablets") and
    // ends where a word does, not in punctuation; anything else is not the name a person chose
    // (review of #193: each of these imported when the name was checked as a substring).
    for (const name of [
      "SYNTHETIC EXAMPLINE",
      "Synthetic Exampli",
      "Synthetic Exampline 10 mg film-coated tablet",
      "mg",
      "10 mg film-coated tablets",
      "Synthetic text, not for clinical use.",
      "Synthetic text",
      "tablets Synthetic text",
      "Synthetic Exampline 10 mg film-coated tablets,",
      // Cut at a word boundary, but not at the strength (re-review of #193).
      "Synthetic",
      "Synthetic Exampline 10",
      "Synthetic Exampline 10 mg film-coated",
    ]) {
      expect([name, refusedWith(json, asked({ name }))]).toEqual([
        name,
        "product: name-not-in-section-1",
      ]);
    }
    // The whole first line, or the line up to its strength.
    for (const name of ["Synthetic Exampline", "Synthetic Exampline 10 mg film-coated tablets"]) {
      expect([name, refusedWith(json, asked({ name }))]).toEqual([name, "imported"]);
    }
    // The holder is section 7's first line, exactly.
    for (const name of [
      "Synthetic Holder BV",
      "Synthetic Holder B.V",
      "Holder B",
      "1 Example Street",
      "Synthetic Holder",
    ]) {
      expect([name, refusedWith(json, asked({ holder: { ...product.holder, name } }))]).toEqual([
        name,
        "product: holder-not-in-section-7",
      ]);
    }
    expect(refusedWith(json, asked({ euAuthorisationNumbers: ["EU/1/24/9999/001"] }))).toBe(
      "product: eu-numbers-differ",
    );
    expect(
      refusedWith(
        json,
        asked({ euAuthorisationNumbers: [...product.euAuthorisationNumbers, "EU/1/24/9999/003"] }),
      ),
    ).toBe("product: eu-numbers-differ");
    const withSection8 = (page: string): Json => ({
      ...json,
      sections: sections(json).map((entry) =>
        entry.key === "smpc.8"
          ? {
              ...entry,
              page,
              narrative: `<div xmlns="http://www.w3.org/1999/xhtml">${page
                .trim()
                .split("\n")
                .map((line) => `<p>${line}</p>`)
                .join("")}</div>`,
            }
          : entry,
      ),
    });
    // Section 8 states a number the strict form does not read: a run, one run into a word, one in
    // another case or spacing, or one a full stop runs into (review of #193).
    for (const extra of [
      "EU/1/24/9999/001-002",
      "See EU/1/24/9999/003x",
      "eu/1/24/9999/003",
      "Eu/1/24/9999/003",
      "EU /1/24/9999/003",
      "E U/1/24/9999/003",
      "EU\u00a0/1/24/9999/003",
      "EU/1/24/9999/001.3",
      "XEU/1/24/9999/003",
    ]) {
      const page = `\nEU/1/24/9999/001\nEU/1/24/9999/002\n${extra}\n`;
      expect([extra, refusedWith(withSection8(page), request)]).toEqual([
        extra,
        "product: eu-number-unread",
      ]);
    }
    // A number after a comma or in brackets, or one a full stop ends, is read.
    for (const page of [
      "\nEU/1/24/9999/001, (EU/1/24/9999/002).\n",
      "\nEU/1/24/9999/001.\nEU/1/24/9999/002. Synthetic text\n",
    ]) {
      expect([page, refusedWith(withSection8(page), request)]).toEqual([page, "imported"]);
    }
  });

  // Re-review of #193: a section 1 or 7 that begins with a table begins with a line only the page
  // writes, which a name or a holder of "\ufdd0" matched.
  it("a name or holder from a line only the page writes", () => {
    const { json, request } = smpc();
    const { product } = request;
    const asked = (change: Partial<typeof product>): unknown => ({
      ...request,
      product: { ...product, ...change },
    });
    const withTable = (key: string, cell: string): Json => {
      const narrative = `<div xmlns="http://www.w3.org/1999/xhtml"><table><tr><td><p>${cell}</p></td></tr></table><p>not for clinical use</p></div>`;
      return {
        ...json,
        sections: sections(json).map((entry) =>
          entry.key === key ? { ...entry, narrative, page: xhtmlToText(narrative) } : entry,
        ),
      };
    };
    const tabled = withTable("smpc.1", "Synthetic Exampline 10 mg film-coated tablets");
    expect(firstPageLine(tabled, "smpc.1")).toBe("\ufdd0");
    expect(refusedWith(tabled, request)).toBe("product: section-1-begins-with-no-text");
    expect(refusedWith(withTable("smpc.7", "Synthetic Holder B.V."), request)).toBe(
      "product: section-7-begins-with-no-text",
    );
    for (const name of ["\ufdd0", "Synthetic\ufffcExampline", "Synthetic Exampline\uffff"]) {
      expect([name, refusedWith(tabled, asked({ name }))]).toEqual([
        name,
        "request: request-shape",
      ]);
      expect(refusedWith(tabled, asked({ holder: { ...product.holder, name } }))).toBe(
        "request: request-shape",
      );
    }
  });

  // Re-review of #193: a look-alike letter or slash put a third number past the guard.
  it("a number written with a look-alike letter or slash", () => {
    const { json, request } = smpc();
    for (const extra of [
      "\u0415U/1/24/9999/003",
      "EU\u22151/24/9999/003",
      "EU\u20441/24/9999/003",
      "EU\uff0f1/24/9999/003",
      "See 1/24/9999/003",
    ]) {
      const page = `\nEU/1/24/9999/001\nEU/1/24/9999/002\n${extra}\n`;
      const changed = {
        ...json,
        sections: sections(json).map((entry) =>
          entry.key === "smpc.8"
            ? {
                ...entry,
                page,
                narrative: `<div xmlns="http://www.w3.org/1999/xhtml">${page
                  .trim()
                  .split("\n")
                  .map((line) => `<p>${line}</p>`)
                  .join("")}</div>`,
              }
            : entry,
        ),
      };
      expect([extra, refusedWith(changed, request)]).toEqual([extra, "product: eu-number-unread"]);
    }
  });

  it("keeps the committed results as the zone-a script wrote them", () => {
    // The TypeScript side reads the bytes the command wrote, never a re-serialisation.
    const text = readFileSync("test/fixtures/certified-word/recompute/smpc.json", "utf8");
    expect(text.endsWith("\n")).toBe(true);
    expect(text.split("\n")).toHaveLength(2);
  });
});

// The package leaflet (ADR 0006 owner decision 8; docs/design/pl-structure.md, "Zone B"), mapped by
// its own manifest: its name is what stands for X in its section 1 heading, its holder the first
// line of section 6's holder section, and it states no EU number.
describe("a package leaflet", () => {
  let leaflet: EmaMapping;

  beforeAll(async () => {
    leaflet = await loadEmaMapping("fhir/mappings/cap-pl-en.json");
  });

  const pl = (): { json: Json; request: ReturnType<typeof caseRequest> } => ({
    json: result("pl"),
    request: caseRequest(found("pl")),
  });
  const refusedWith = (json: unknown, request: unknown): string =>
    refusal(() =>
      importCertifiedWord(json instanceof Uint8Array ? json : bytes(json), request, leaflet, RUN),
    );
  const withSection = (json: Json, key: string, change: Json): Json => ({
    ...json,
    sections: sections(json).map((entry) => (entry.key === key ? { ...entry, ...change } : entry)),
  });

  it("is carried as its recompute gave it, a Type 1 record of its name and holder only", () => {
    const { json, request } = pl();
    const { submission, sourceText, fidelityReport } = importCertifiedWord(
      recomputed("pl"),
      request,
      leaflet,
      RUN,
    );
    const record = recordSections(submission);
    expect(record.map(({ key }) => key)).toEqual(sections(json).map(({ key }) => key));
    record.forEach(({ section: carried }, index) => {
      const from = at(sections(json), index);
      expect(carried.title).toBe(from.title);
      expect((carried.text as { div?: string } | undefined)?.div ?? null).toBe(from.narrative);
      expect(sourceText.pages[index]?.text).toBe(from.page);
    });
    expect(fidelityReport.status).toBe("passed");
    expect(CanonicalSubmissionSchema.safeParse(submission).success).toBe(true);

    const resources = submission.bundle.entry.map(({ resource }) => resource as Json);
    expect(resources.map(({ resourceType }) => resourceType)).toEqual([
      "Composition",
      "MedicinalProductDefinition",
      "Organization",
    ]);
    const [composition, medicinal, holder] = resources;
    expect(composition?.type).toEqual({
      coding: [
        {
          system: "https://khs.dev/fhir/CodeSystem/document-type",
          code: "pl",
          display: "Package Leaflet",
        },
      ],
    });
    expect(composition?.title).toBe("Synthetic Exampline");
    expect(medicinal?.identifier).toEqual([
      { system: CANONICAL_PRODUCT_SYSTEM, value: request.product.id },
    ]);
    expect(medicinal?.name).toEqual([{ productName: "Synthetic Exampline" }]);
    expect(holder?.name).toBe("Synthetic Holder B.V.");
    // Where each confirmed value stands in the leaflet, and the mapping that coded it.
    const decided = (target: string): unknown =>
      submission.provenance.decisions.find((decision) => decision.target === target)?.sourceKey;
    expect([
      decided("Composition.title"),
      decided("MedicinalProductDefinition.name"),
      decided("Organization.name"),
    ]).toEqual(["pl.1", "pl.1", "pl.6.holder"]);
    expect(submission.provenance.extraction.terminologyService?.name).toBe("cap-pl-en");
    const preflight = validateCanonicalPreflight(
      submission.bundle as unknown as FhirBundle,
      "type1",
    );
    expect(preflight.issue.map(({ severity }) => severity)).toEqual(["success"]);
  });

  it("is refused where its section 1 heading does not give the confirmed name", () => {
    const { json, request } = pl();
    const structure = json.structure as Json;
    const named = (name: string) => ({ ...request, product: { ...request.product, name } });
    // Another name, a name in another case, a part of the name.
    for (const name of ["Synthetic Exampline 10 mg", "SYNTHETIC EXAMPLINE", "Exampline"]) {
      expect([name, refusedWith(json, named(name))]).toEqual([
        name,
        "product: name-not-in-section-1",
      ]);
    }
    for (const changed of [
      // The structure gives no name, or another, than the heading as written.
      { ...json, structure: { ...structure, name: null } },
      { ...json, structure: { ...structure, name: "Exampline" } },
      { ...json, structure: { ...structure, name: undefined } },
      // A heading the template does not word, or with the name written otherwise.
      withSection(json, "pl.1", { title: "1. What Synthetic Exampline is" }),
      withSection(json, "pl.1", {
        title: "1. What Synthetic  Exampline is and what it is used for",
      }),
      withSection(json, "pl.1", { title: "1. What X is and what it is used for" }),
    ]) {
      expect(refusedWith(changed, request)).toBe("product: name-not-in-section-1");
    }
  });

  it("is refused where section 6 does not begin with the confirmed holder", () => {
    const { json, request } = pl();
    const holderPage = (page: string): Json =>
      withSection(json, "pl.6.holder", {
        page,
        narrative: `<div xmlns="http://www.w3.org/1999/xhtml">${page
          .trim()
          .split("\n")
          .map((line) => `<p>${line}</p>`)
          .join("")}</div>`,
      });
    expect(
      refusedWith(
        holderPage("\nMarketing Authorisation Holder\nSynthetic Holder B.V.\nSynthetic text.\n"),
        request,
      ),
    ).toBe("product: holder-not-in-section-6");
    expect(
      refusedWith(json, {
        ...request,
        product: {
          ...request.product,
          holder: { ...request.product.holder, name: "1 Example Street" },
        },
      }),
    ).toBe("product: holder-not-in-section-6");
    // A first line only the page writes: a table's.
    const narrative = `<div xmlns="http://www.w3.org/1999/xhtml"><table><tr><td><p>Synthetic Holder B.V.</p></td></tr></table><p>not for clinical use</p></div>`;
    expect(
      refusedWith(
        withSection(json, "pl.6.holder", { narrative, page: xhtmlToText(narrative) }),
        request,
      ),
    ).toBe("product: section-6-begins-with-no-text");
  });

  it("is refused with an EU number, which a leaflet does not state", () => {
    const { json, request } = pl();
    for (const numbers of [["EU/1/24/9999/001"], ["EU/1/24/9999/001", "EU/1/24/9999/002"]]) {
      expect(
        refusedWith(json, {
          ...request,
          product: { ...request.product, euAuthorisationNumbers: numbers },
        }),
      ).toBe("product: eu-numbers-not-in-leaflet");
    }
  });

  it("is refused by the SmPC's mapping, and an SmPC by the leaflet's", () => {
    const { json, request } = pl();
    expect(refusal(() => importCertifiedWord(bytes(json), request, mapping, RUN))).toBe(
      "binding: mapping-of-another-document",
    );
    expect(refusedWith(recomputed("smpc"), caseRequest(found("smpc")))).toBe(
      "binding: mapping-of-another-document",
    );
  });
});
