import { readdirSync, readFileSync } from "node:fs";

import { beforeAll, describe, expect, it } from "vitest";

import { importPublication } from "../src/authority/import.js";
import { importCertifiedWord } from "../src/certified-word/import.js";
import {
  RUN as CERTIFIED_WORD_RUN,
  caseRequest,
  recomputed,
  recomputedCases,
} from "../src/certified-word/vectors.js";
import { syntheticPublication } from "../src/authority/synthetic.js";
import type { CanonicalSubmission } from "../src/contracts/index.js";
import type { FidelityReport } from "../src/fidelity/index.js";
import { loadEmaMapping, type EmaMapping } from "../src/fhir/mapping.js";
import { hasValidationErrors, validateCanonicalPreflight } from "../src/fhir/preflight.js";
import { toProvenanceResource } from "../src/fhir/provenance.js";
import {
  EMA_EU_NUMBER_SYSTEM,
  EU_AUTHORISATION_NUMBER_PATTERN,
  EU_AUTHORISATION_NUMBER_SYSTEM,
  EU_PRODUCT_IDENTITY_PROFILE,
  EU_PRODUCT_NUMBER_PATTERN,
  EU_PRODUCT_NUMBER_SYSTEM,
} from "../src/fhir/standards.js";
import {
  EMA_COMPOSITION_VERSION_SYSTEM,
  TransformationError,
  transformType2ToEma,
} from "../src/fhir/transform.js";
import type { FhirBundle, FhirResource } from "../src/fhir/types.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import {
  SMOKE_PRODUCT_ID,
  SYNTHETIC_PRODUCT_IDS,
  SYNTHETIC_VERSIONS,
} from "../src/fixtures/synthetic-products.js";
import { buildPersistTransaction } from "../src/gcp/healthcare.js";

// The version model of docs/design/version-identity.md, as the Global ePI and EMA profiles define
// it: what stays the same across an ePI's versions (Bundle.identifier and timestamp, the List's
// identifier, every resource id) and what changes (Composition.identifier and date); the
// transaction's precondition; and product identity by EU numbers (one RegulatedAuthorization per
// EU authorisation number, the product numbers on the MedicinalProductDefinition), with a
// NamingSystem for every identifier system written.

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

type Identifier = { system: string; value: string };

function compositionOf(bundle: FhirBundle): FhirResource {
  const composition = bundle.entry[0]?.resource;
  if (composition === undefined) throw new Error("no Composition");
  return composition;
}

describe("an ePI's versions", () => {
  it("keep the source's Bundle identity and timestamp, and change its Composition's identifier and date", () => {
    for (const product of SYNTHETIC_PRODUCT_IDS) {
      const [one, two] = SYNTHETIC_VERSIONS.map((version) =>
        createSyntheticType2Bundle(mapping, { product, version }),
      );
      if (one === undefined || two === undefined) throw new Error("two versions");
      expect(two.identifier).toEqual(one.identifier);
      expect(two.timestamp).toBe(one.timestamp);
      expect(compositionOf(two).identifier).not.toEqual(compositionOf(one).identifier);
      expect(compositionOf(two).date).not.toBe(compositionOf(one).date);
    }
  });

  it("transform to one EMA Bundle, List and Composition, with a new Composition identifier", () => {
    for (const product of SYNTHETIC_PRODUCT_IDS) {
      const [one, two] = SYNTHETIC_VERSIONS.map((version) =>
        transformType2ToEma(createSyntheticType2Bundle(mapping, { product, version }), mapping),
      );
      if (one === undefined || two === undefined) throw new Error("two versions");
      expect(two.documentBundle.id).toBe(one.documentBundle.id);
      expect(two.documentBundle.identifier).toEqual(one.documentBundle.identifier);
      expect(two.documentBundle.timestamp).toBe(one.documentBundle.timestamp);
      expect(two.list.id).toBe(one.list.id);
      expect(two.list.identifier).toEqual(one.list.identifier);
      expect(two.documentBundle.entry.map(({ resource }) => resource.id)).toEqual(
        one.documentBundle.entry.map(({ resource }) => resource.id),
      );

      const [first, second] = [one, two].map(
        ({ documentBundle }) => compositionOf(documentBundle).identifier as Identifier[],
      );
      expect(first).toHaveLength(1);
      expect(first?.[0]?.system).toBe(EMA_COMPOSITION_VERSION_SYSTEM);
      expect(second?.[0]?.system).toBe(EMA_COMPOSITION_VERSION_SYSTEM);
      expect(second?.[0]?.value).not.toBe(first?.[0]?.value);
    }
  });

  it("give the same content the same Composition identifier, and any change a new one", () => {
    const source = createSyntheticType2Bundle(mapping);
    const identifier = (bundle: FhirBundle): unknown =>
      compositionOf(transformType2ToEma(bundle, mapping).documentBundle).identifier;
    expect(identifier(createSyntheticType2Bundle(mapping))).toEqual(identifier(source));

    // A source identifier on the Composition does not reach the output; its content does.
    const relabelled = structuredClone(source);
    compositionOf(relabelled).identifier = [{ system: "https://khs.dev/fhir/x", value: "y" }];
    expect(identifier(relabelled)).toEqual(identifier(source));
    const retitled = structuredClone(source);
    compositionOf(retitled).title = "Synthetic retitled SmPC";
    expect(identifier(retitled)).not.toEqual(identifier(source));
  });
});

describe("the persist transaction's precondition", () => {
  it("conditions the document Bundle's entry, and only it, on the version the store held", () => {
    const { list, documentBundle } = transformType2ToEma(
      createSyntheticType2Bundle(mapping),
      mapping,
    );
    for (const [stored, condition] of [
      ["absent", { ifNoneMatch: "*" }],
      [{ versionId: "MTc5MDUyNzYzMTkyMTk4NTAwMA" }, { ifMatch: 'W/"MTc5MDUyNzYzMTkyMTk4NTAwMA"' }],
    ] as const) {
      const transaction = buildPersistTransaction(list, documentBundle, "run", stored);
      for (const { request, resource } of transaction.entry) {
        const expected = resource === documentBundle ? condition : {};
        expect(request).toEqual({
          method: "PUT",
          url: `${resource.resourceType}/${resource.id ?? ""}`,
          ...expected,
        });
      }
      expect(transaction.entry.some(({ resource }) => resource === documentBundle)).toBe(true);
    }
  });
});

// EU/1/24/9999 is the EMA's own example number, from its commented sample
// (fhir/standards.lock.json, "EMA EPI-23-1022 English commented sample").
function withNumbers(
  authorisations: string[][],
  products: string[],
  holder: string[] = [],
): FhirBundle {
  const bundle = createSyntheticType2Bundle(mapping, { product: SMOKE_PRODUCT_ID });
  const find = (type: string) => {
    const found = bundle.entry.find(({ resource }) => resource.resourceType === type);
    if (found === undefined) throw new Error(`fixture has a ${type}`);
    return found;
  };
  const authorisation = find("RegulatedAuthorization");
  bundle.entry = bundle.entry.filter((entry) => entry !== authorisation);
  authorisations.forEach((numbers, index) => {
    const copy = structuredClone(authorisation);
    copy.fullUrl = `${authorisation.fullUrl}-${index.toString()}`;
    copy.resource.id = `${authorisation.resource.id ?? ""}-${index.toString()}`;
    copy.resource.identifier = [
      ...(copy.resource.identifier as Identifier[]),
      ...numbers.map((value) => ({ system: EU_AUTHORISATION_NUMBER_SYSTEM, value })),
    ];
    bundle.entry.push(copy);
  });
  const add = (type: string, system: string, values: string[]): void => {
    const { resource } = find(type);
    resource.identifier = [
      ...(resource.identifier as Identifier[]),
      ...values.map((value) => ({ system, value })),
    ];
  };
  add("MedicinalProductDefinition", EU_PRODUCT_NUMBER_SYSTEM, products);
  add("Organization", EU_AUTHORISATION_NUMBER_SYSTEM, holder);
  return bundle;
}

function errors(bundle: FhirBundle): string[] {
  return validateCanonicalPreflight(bundle, "type2")
    .issue.filter(({ severity }) => severity === "error")
    .map(({ diagnostics }) => diagnostics ?? "");
}

describe("EU numbers in the canonical graph", () => {
  it("accept one RegulatedAuthorization per authorisation number and the product's number", () => {
    expect(
      errors(withNumbers([["EU/1/24/9999/001"], ["EU/1/24/9999/002"]], ["EU/1/24/9999"])),
    ).toEqual([]);
    expect(errors(withNumbers([[]], []))).toEqual([]);
  });

  it.each([
    ["a malformed number", [["EU/1/24/9999/1"]], ["EU/1/24/9999"], [], /is not \^EU/],
    ["a run of presentations", [["EU/1/24/9999/001-002"]], ["EU/1/24/9999"], [], /is not \^EU/],
    [
      "two numbers on one",
      [["EU/1/24/9999/001", "EU/1/24/9999/002"]],
      ["EU/1/24/9999"],
      [],
      /has one EU authorisation number/,
    ],
    [
      "one number on two",
      [["EU/1/24/9999/001"], ["EU/1/24/9999/001"]],
      ["EU/1/24/9999"],
      [],
      /Two RegulatedAuthorizations/,
    ],
    ["no product number", [["EU/1/24/9999/001"]], [], [], /not exactly those/],
    [
      "a product number no authorisation has",
      [["EU/1/24/9999/001"]],
      ["EU/1/24/9999", "EU/1/24/9998"],
      [],
      /not exactly those/,
    ],
    [
      "a malformed product number",
      [["EU/1/24/9999/001"]],
      ["EU/1/24/9999/001"],
      [],
      /eu-product-number is not/,
    ],
    [
      "an authorisation number on the holder",
      [["EU/1/24/9999/001"]],
      ["EU/1/24/9999"],
      ["EU/1/24/9999/002"],
      /Only a RegulatedAuthorization/,
    ],
  ])("refuse %s, Type 2 and Type 1 alike", (_case, authorisations, products, holder, message) => {
    const bundle = withNumbers(authorisations, products, holder);
    expect(errors(bundle).some((error) => message.test(error))).toBe(true);
    const type1 = structuredClone(bundle);
    const keep = [
      "Composition",
      "MedicinalProductDefinition",
      "Organization",
      "RegulatedAuthorization",
    ];
    type1.entry = type1.entry.filter(({ resource }) => keep.includes(resource.resourceType));
    expect(hasValidationErrors(validateCanonicalPreflight(type1, "type1"))).toBe(true);
  });

  it("give the EMA List the product's EU number, in the system the EMA's sample writes", () => {
    const { list } = transformType2ToEma(
      withNumbers([["EU/1/24/9999/001"], ["EU/1/24/9999/002"]], ["EU/1/24/9999"]),
      mapping,
    );
    expect(list.extension).toEqual([
      {
        url: "http://ema.europa.eu/fhir/StructureDefinition/ext-epi-eu-number",
        valueIdentifier: { system: EMA_EU_NUMBER_SYSTEM, value: "EU/1/24/9999" },
      },
    ]);
    // The List carries one EU number (EUEpiList, 0..1): two are ambiguous, never one chosen.
    expect(() =>
      transformType2ToEma(
        withNumbers([["EU/1/24/9999/001"], ["EU/1/24/9998/001"]], ["EU/1/24/9999", "EU/1/24/9998"]),
        mapping,
      ),
    ).toThrow(TransformationError);
  });

  it("are the rules the package's profile gives the official validator", () => {
    const profile = JSON.parse(
      readFileSync("fhir/generated/StructureDefinition-eu-product-identity.json", "utf8"),
    ) as {
      url: string;
      differential: { element: { constraint: { key: string; expression: string }[] }[] };
    };
    expect(profile.url).toBe(EU_PRODUCT_IDENTITY_PROFILE);
    const constraints = profile.differential.element[0]?.constraint ?? [];
    expect(constraints.map(({ key }) => key)).toEqual([
      "khs-eu-1",
      "khs-eu-2",
      "khs-eu-3",
      "khs-eu-4",
    ]);
    expect(constraints[0]?.expression).toContain(`matches('${EU_AUTHORISATION_NUMBER_PATTERN}')`);
    expect(constraints[1]?.expression).toContain(`matches('${EU_PRODUCT_NUMBER_PATTERN}')`);
  });
});

// Every `identifier` and `valueIdentifier` system under https://khs.dev/ in a value.
function khsSystems(value: unknown, found = new Set<string>()): Set<string> {
  if (Array.isArray(value)) {
    for (const item of value) khsSystems(item, found);
  } else if (value !== null && typeof value === "object") {
    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
      if (key === "identifier" || key === "valueIdentifier") {
        for (const identifier of Array.isArray(child) ? child : [child]) {
          const system = (identifier as { system?: unknown } | null)?.system;
          if (typeof system === "string" && system.startsWith("https://khs.dev/"))
            found.add(system);
        }
      }
      khsSystems(child, found);
    }
  }
  return found;
}

describe("the package's naming systems", () => {
  it("name every identifier system the pipeline and its fixtures write", () => {
    const fixture = (name: string): unknown =>
      JSON.parse(readFileSync(`test/fixtures/contracts/${name}`, "utf8"));
    const written = new Set<string>();
    const persist = (source: FhirBundle, provenance: FhirResource[]): void => {
      const { list, documentBundle } = transformType2ToEma(source, mapping);
      khsSystems(source, written);
      khsSystems(
        buildPersistTransaction(list, documentBundle, "run", "absent", provenance),
        written,
      );
    };
    for (const product of SYNTHETIC_PRODUCT_IDS) {
      for (const version of SYNTHETIC_VERSIONS) {
        persist(createSyntheticType2Bundle(mapping, { product, version }), []);
      }
    }
    persist(withNumbers([["EU/1/24/9999/001"]], ["EU/1/24/9999"]), []);
    const publication = syntheticPublication(mapping);
    const imported = importPublication(publication.request, publication, mapping, {
      submissionId: "00000000-0000-4000-8000-000000000001",
      createdAt: "2026-09-24T12:00:00Z",
      extractionRunId: "00000000-0000-4000-8000-000000000002",
      serviceVersion: "version-identity",
      requestedBy: "urn:requester:version-identity",
      requestedAt: "2026-09-24T12:00:00Z",
      sourceTextUri: "gs://version-identity/import.pages.json",
      fidelityReportUri: "gs://version-identity/import.fidelity-report.json",
    });
    for (const [submission, report, fetchedAt] of [
      ["canonical-submission.json", "fidelity-report.json", undefined],
      ["canonical-submission-type1.json", "fidelity-report-type1.json", "2026-09-24T12:00:00Z"],
    ] as const) {
      khsSystems(
        toProvenanceResource(
          fixture(submission) as CanonicalSubmission,
          fixture(report) as FidelityReport,
          {
            bundleId: "b",
            compositionId: "c",
            ...(fetchedAt === undefined ? {} : { fetchedAt }),
          },
        ),
        written,
      );
    }
    persist(imported.submission.bundle as unknown as FhirBundle, []);
    // A certified Word source's record, and its Provenance (ADR 0006 P4, D1).
    const [label] = recomputedCases();
    if (label === undefined) throw new Error("no recomputed label");
    const word = importCertifiedWord(
      recomputed(label.name),
      caseRequest(label),
      mapping,
      CERTIFIED_WORD_RUN,
    );
    persist(word.submission.bundle as unknown as FhirBundle, []);
    khsSystems(
      toProvenanceResource(word.submission, word.fidelityReport, {
        bundleId: "b",
        compositionId: "c",
      }),
      written,
    );

    const named = new Map(
      readdirSync("fhir/generated")
        .filter((file) => file.startsWith("NamingSystem-"))
        .map((file) => {
          const naming = JSON.parse(readFileSync(`fhir/generated/${file}`, "utf8")) as {
            status: string;
            kind: string;
            uniqueId: { type: string; value: string; preferred?: boolean }[];
          };
          expect(naming.kind).toBe("identifier");
          expect(naming.uniqueId).toEqual([
            { type: "uri", value: expect.any(String) as unknown, preferred: true },
          ]);
          return [naming.uniqueId[0]?.value ?? "", naming.status] as const;
        }),
    );
    expect(written.size).toBeGreaterThan(15);
    for (const system of written) expect([system, named.get(system)]).toEqual([system, "active"]);
    // The retired system is no longer written; versions written before keep it.
    expect(named.get("https://khs.dev/fhir/identifier/ema-composition")).toBe("retired");
    expect(written.has("https://khs.dev/fhir/identifier/ema-composition")).toBe(false);
  });
});
