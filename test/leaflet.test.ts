import { beforeAll, describe, expect, it } from "vitest";

import { loadConfig, type AppConfig } from "../src/config.js";
import { SourceKey } from "../src/contracts/index.js";
import {
  DOCUMENT_TYPE_SYSTEM,
  EMA_DOCUMENT_TYPE_SYSTEM,
  loadEmaMappings,
  mappingFor,
  mappingReference,
  type EmaMapping,
  type SectionRule,
} from "../src/fhir/mapping.js";
import {
  hasValidationErrors,
  validateCanonicalPreflight,
  validateEmaPreflight,
} from "../src/fhir/preflight.js";
import { TransformationError, transformType2ToEma } from "../src/fhir/transform.js";
import type { FhirBundle, FhirComposition } from "../src/fhir/types.js";
import { LEAFLET_PRODUCT_ID } from "../src/fixtures/synthetic-products.js";
import { createSyntheticSubmission } from "../src/fixtures/synthetic-submission.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { runPipeline } from "../src/pipeline.js";

// The package leaflet in Zone B (ADR 0006 owner decision 8; docs/design/pl-structure.md, "Zone
// B"): its manifest is taken by the source's document type, its EMA Composition is the leaflet's,
// and the record it comes from is a Type 2 graph or, for a certified Word leaflet, a Type 1 record
// with no authorisation (test/certified-word/ holds that one).

let smpc: EmaMapping;
let leaflet: EmaMapping;
let mappings: [EmaMapping, EmaMapping];
let config: AppConfig;

beforeAll(async () => {
  ({ smpc, pl: leaflet } = await loadEmaMappings());
  mappings = [smpc, leaflet];
  config = loadConfig({ ALLOW_SYNTHETIC_SOURCES: "true", NODE_ENV: "test", DRY_RUN: "true" });
});

const source = (optional = false): FhirBundle =>
  createSyntheticType2Bundle(leaflet, { product: LEAFLET_PRODUCT_ID, optional });

function composition(bundle: FhirBundle): FhirComposition {
  return bundle.entry[0]?.resource as FhirComposition;
}

function typed(bundle: FhirBundle, coding: { system: string; code: string }[]): FhirBundle {
  const copy = structuredClone(bundle);
  composition(copy).type = { coding };
  return copy;
}

function issues(run: () => unknown): string[] {
  try {
    run();
  } catch (error) {
    if (error instanceof TransformationError) return error.issues;
    throw error;
  }
  return [];
}

function keys(rule: SectionRule): string[] {
  return [rule.sourceKey, ...(rule.children ?? []).flatMap(keys)];
}

describe("the mapping for a source", () => {
  it("is the one its Composition.type names, in our code system or the EMA's", () => {
    expect(mappingFor(source(), mappings)).toBe(leaflet);
    expect(mappingFor(createSyntheticType2Bundle(smpc), mappings)).toBe(smpc);
    const ema = (code: string, system = EMA_DOCUMENT_TYPE_SYSTEM) =>
      typed(source(), [{ system, code }]);
    expect(mappingFor(ema("100000155538"), mappings)).toBe(leaflet);
    expect(mappingFor(ema("100000155532"), mappings)).toBe(smpc);
    expect(
      mappingFor(
        ema("100000155538", "https://spor.ema.europa.eu/v1/lists/100000155531/terms/"),
        mappings,
      ),
    ).toBe(leaflet);
    // Both systems naming one document are one document.
    expect(
      mappingFor(
        typed(source(), [
          { system: DOCUMENT_TYPE_SYSTEM, code: "pl" },
          { system: EMA_DOCUMENT_TYPE_SYSTEM, code: "100000155538" },
        ]),
        mappings,
      ),
    ).toBe(leaflet);
  });

  it("is none where it names none, two, or one no mapping maps", () => {
    for (const coding of [
      [],
      [{ system: "http://loinc.org", code: "34076-0" }],
      [{ system: DOCUMENT_TYPE_SYSTEM, code: "labelling" }],
      [{ system: EMA_DOCUMENT_TYPE_SYSTEM, code: "100000155535" }],
      [
        { system: DOCUMENT_TYPE_SYSTEM, code: "pl" },
        { system: DOCUMENT_TYPE_SYSTEM, code: "smpc" },
      ],
      [
        { system: DOCUMENT_TYPE_SYSTEM, code: "pl" },
        { system: EMA_DOCUMENT_TYPE_SYSTEM, code: "100000155532" },
      ],
    ]) {
      expect([coding, mappingFor(typed(source(), coding), mappings)]).toEqual([coding, undefined]);
    }
    expect(mappingFor(undefined, mappings)).toBeUndefined();
    expect(mappingFor({ entry: "not a list" }, mappings)).toBeUndefined();
    // A document only one of the given mappings maps.
    expect(mappingFor(source(), [smpc])).toBeUndefined();
  });

  it("names the leaflet's mapping in lineage by its own name", () => {
    expect(mappingReference(leaflet)).toBe(`cap-pl-en#${leaflet.mappingVersion}`);
    expect(mappingReference(smpc)).toBe(`cap-smpc-en#${smpc.mappingVersion}`);
  });
});

describe("a Type 2 package leaflet through the crosswalk", () => {
  it.each([false, true])(
    "is the EMA's leaflet document, every section in place (optional sections %s)",
    (optional) => {
      const record = source(optional);
      expect(hasValidationErrors(validateCanonicalPreflight(record, "type2"))).toBe(false);
      const { list, documentBundle } = transformType2ToEma(record, leaflet);
      expect(hasValidationErrors(validateEmaPreflight(list, documentBundle, leaflet))).toBe(false);
      const target = composition(documentBundle);
      expect(target.type).toEqual({
        coding: [
          {
            system: EMA_DOCUMENT_TYPE_SYSTEM,
            code: "100000155538",
            display: "Package Leaflet",
          },
        ],
      });
      expect(target.meta?.profile).toEqual(leaflet.profiles.composition);
      const codes: string[] = [];
      const walk = (sections: FhirComposition["section"]): void => {
        for (const section of sections) {
          codes.push(section.code.coding?.[0]?.code ?? "");
          walk(section.section ?? []);
        }
      };
      walk(target.section);
      const rules: SectionRule[] = [];
      const visit = (rule: SectionRule): void => {
        if (optional || rule.required) {
          rules.push(rule);
          (rule.children ?? []).forEach(visit);
        }
      };
      visit(leaflet.root);
      expect(codes).toEqual(rules.map(({ targetCode }) => targetCode));
      // The List names the leaflet's document and carries the product's one EU product number.
      expect(list.entry).toEqual([
        { item: { reference: `urn:uuid:${documentBundle.id}`, display: target.title } },
      ]);
      expect(list.extension).toEqual([
        {
          url: "http://ema.europa.eu/fhir/StructureDefinition/ext-epi-eu-number",
          valueIdentifier: { system: "http://ema.europa.eu/fhir/euNumber/", value: "EU/1/24/9999" },
        },
      ]);
    },
  );

  it("is refused by the SmPC's mapping, and an SmPC by the leaflet's", () => {
    expect(issues(() => transformType2ToEma(source(), smpc))).toContain(
      "Source Composition.type names another document than the mapping's smpc",
    );
    expect(issues(() => transformType2ToEma(createSyntheticType2Bundle(smpc), leaflet))).toContain(
      "Source Composition.type names another document than the mapping's pl",
    );
  });

  it("is refused by the EMA preflight where its Composition is not typed the leaflet", () => {
    const { list, documentBundle } = transformType2ToEma(source(), leaflet);
    const wrong = typed(documentBundle, [
      { system: EMA_DOCUMENT_TYPE_SYSTEM, code: "100000155532" },
    ]);
    expect(
      validateEmaPreflight(list, wrong, leaflet).issue.map(({ diagnostics }) => diagnostics),
    ).toEqual(["EMA Composition type is not the pl document's"]);
  });
});

describe("a package leaflet through the worker's pipeline", () => {
  it("runs dry from the fixture route, by the mapping its document type names", async () => {
    const result = await runPipeline(
      {
        runId: "00000000-0000-4000-8000-0000000001e0",
        source: source(true),
        sourceKind: "fixture",
        sourceResource: "fixture:leaflet",
      },
      mappings,
      config,
    );
    expect(result.status).toBe("validated");
    expect(composition(result.emaBundle).type.coding?.[0]?.code).toBe("100000155538");
    expect(result.evidence.manifest.standards.mappingVersion).toBe(leaflet.mappingVersion);
  });

  it("runs dry from an approved drawn submission, through the document gate", async () => {
    const { submission, fidelityReport, sourceText } = createSyntheticSubmission(leaflet, {
      product: LEAFLET_PRODUCT_ID,
      optional: true,
    });
    const result = await runPipeline(
      {
        runId: "00000000-0000-4000-8000-0000000001e1",
        sourceKind: "document",
        sourceResource: "document:leaflet",
        submission,
        fidelityReport,
        sourceText,
      },
      mappings,
      config,
    );
    expect(result.status).toBe("validated");
    expect(result.evidence.manifest.ingestion?.fidelity.sectionsMatched).toBe(
      submission.provenance.sections.length,
    );
  });

  it("is refused where its document type names no mapping, or another than the one given", async () => {
    const run = (bundle: FhirBundle, given: EmaMapping | [EmaMapping, EmaMapping]) =>
      runPipeline(
        {
          runId: "00000000-0000-4000-8000-0000000001e2",
          source: bundle,
          sourceKind: "fixture",
          sourceResource: "fixture:leaflet",
        },
        given,
        config,
      );
    await expect(run(typed(source(), []), mappings)).rejects.toThrow(
      "No mapping carries the source's document",
    );
    await expect(run(source(), smpc)).rejects.toBeInstanceOf(TransformationError);
  });
});

describe("a Type 1 package leaflet record", () => {
  // A leaflet states no authorisation number; an SmPC states one or more in its section 8.
  it("may have no RegulatedAuthorization, where an SmPC's must", () => {
    const record = source();
    const type1 = (bundle: FhirBundle, document: string): FhirBundle => {
      const copy = typed(bundle, [{ system: DOCUMENT_TYPE_SYSTEM, code: document }]);
      copy.entry = copy.entry.filter(({ resource }) =>
        ["Composition", "MedicinalProductDefinition", "Organization"].includes(
          resource.resourceType,
        ),
      );
      for (const { resource } of copy.entry) {
        if (resource.resourceType === "MedicinalProductDefinition") {
          resource.identifier = (resource.identifier as { system: string }[]).filter(
            ({ system }) => !system.includes("eu-product-number"),
          );
        }
      }
      return copy;
    };
    expect(hasValidationErrors(validateCanonicalPreflight(type1(record, "pl"), "type1"))).toBe(
      false,
    );
    expect(
      validateCanonicalPreflight(type1(record, "smpc"), "type1").issue.map(
        ({ diagnostics }) => diagnostics,
      ),
    ).toEqual(["Type 1 record is missing RegulatedAuthorization"]);
  });
});

describe("the canonical section keys", () => {
  // Every key a submission names is a contract SourceKey (src/contracts/common.ts), whose grammar
  // has no hyphen: the leaflet's keys are all in it. Two optional SmPC keys of mapping 1.4.0 are
  // not, so no submission can carry those sections yet; they are listed here so that the list
  // cannot grow unseen (docs/design/pl-structure.md, "Zone B").
  it("are contract source keys, but for two optional SmPC sections", () => {
    const outside = (mapping: EmaMapping): string[] =>
      keys(mapping.root).filter((key) => !SourceKey.safeParse(key).success);
    expect(outside(leaflet)).toEqual([]);
    expect(outside(smpc)).toEqual(["smpc.4.6.breast-feeding", "smpc.5.2.pk-pd"]);
  });
});
