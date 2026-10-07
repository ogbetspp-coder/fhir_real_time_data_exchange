import { readdirSync } from "node:fs";
import path from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import { pipelineFailure } from "../src/app.js";
import { importCertifiedWord } from "../src/certified-word/import.js";
import { RUN, caseRequest, recomputed, recomputedCases } from "../src/certified-word/vectors.js";
import { loadConfig, type AppConfig } from "../src/config.js";
import { SourceKey } from "../src/contracts/index.js";
import {
  DOCUMENT_TYPE_SYSTEM,
  EMA_DOCUMENT_TYPE_SYSTEM,
  LEAFLET_TITLES_NOT_CARRIED,
  loadEmaMapping,
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
// B"): its manifest is taken by the source's document type and its EMA Composition is the
// leaflet's. It is carried only with its titles as written, from a certified Word source, whose
// Type 1 record has no authorisation (test/certified-word/ holds that one): a Type 2 leaflet,
// under the template's titles, is refused. No leaflet persists: a run that is not a dry run is
// refused. The synthetic Type 2 leaflet is the refusal's fixture.

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

// The certified Word leaflet's Type 1 record (test/fixtures/certified-word/recompute/pl.json).
function wordLeaflet() {
  const found = recomputedCases().find(({ name }) => name === "pl");
  if (found === undefined) throw new Error("no leaflet");
  return importCertifiedWord(recomputed("pl"), caseRequest(found), leaflet, RUN);
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

describe("a package leaflet through the crosswalk", () => {
  it("is the EMA's leaflet document, carried with its titles as written", () => {
    const record = wordLeaflet().submission.bundle as unknown as FhirBundle;
    const { list, documentBundle } = transformType2ToEma(record, leaflet, undefined, "as-written");
    expect(
      hasValidationErrors(validateEmaPreflight(list, documentBundle, leaflet, "as-written")),
    ).toBe(false);
    const target = composition(documentBundle);
    expect(target.type).toEqual({
      coding: [
        { system: EMA_DOCUMENT_TYPE_SYSTEM, code: "100000155538", display: "Package Leaflet" },
      ],
    });
    expect(target.meta?.profile).toEqual(leaflet.profiles.composition);
    expect(target.section[0]?.section?.[0]?.title).toBe(
      "1. What Synthetic Exampline is and what it is used for",
    );
    expect(list.entry).toEqual([
      { item: { reference: `urn:uuid:${documentBundle.id}`, display: target.title } },
    ]);
  });

  // The tree with every optional section, under the rule a certified Word source is carried by
  // (Zone A finds the required sections only): each section in place, in the manifest's order.
  it("places every section of the manifest, optional ones included", () => {
    const { documentBundle } = transformType2ToEma(source(true), leaflet, undefined, "as-written");
    const codes: string[] = [];
    const walk = (sections: FhirComposition["section"]): void => {
      for (const section of sections) {
        codes.push(section.code.coding?.[0]?.code ?? "");
        walk(section.section ?? []);
      }
    };
    walk(composition(documentBundle).section);
    const rules: SectionRule[] = [];
    const visit = (rule: SectionRule): void => {
      rules.push(rule);
      (rule.children ?? []).forEach(visit);
    };
    visit(leaflet.root);
    expect(codes).toEqual(rules.map(({ targetCode }) => targetCode));
  });

  // The template's titles write X and the template's choices ("Do not take use X"), which no
  // label says.
  it("is refused under the template's titles, by the crosswalk and the EMA preflight", () => {
    for (const optional of [false, true]) {
      expect(issues(() => transformType2ToEma(source(optional), leaflet))).toEqual([
        LEAFLET_TITLES_NOT_CARRIED,
      ]);
    }
    const word = wordLeaflet().submission.bundle as unknown as FhirBundle;
    expect(issues(() => transformType2ToEma(word, leaflet, undefined, "template"))).toEqual([
      LEAFLET_TITLES_NOT_CARRIED,
    ]);
    const { list, documentBundle } = transformType2ToEma(word, leaflet, undefined, "as-written");
    // The preflight also holds each title to the template's, which a heading as written is not.
    expect(
      validateEmaPreflight(list, documentBundle, leaflet).issue.map(
        ({ diagnostics }) => diagnostics,
      )[0],
    ).toBe(LEAFLET_TITLES_NOT_CARRIED);
  });

  it("is refused by the SmPC's mapping, and an SmPC by the leaflet's", () => {
    expect(issues(() => transformType2ToEma(source(), smpc))).toContain(
      "Source Composition.type names another document than the mapping's smpc",
    );
    expect(issues(() => transformType2ToEma(createSyntheticType2Bundle(smpc), leaflet))).toContain(
      "Source Composition.type names another document than the mapping's pl",
    );
  });

  it("is refused by the EMA preflight where its Composition is not typed the leaflet", () => {
    const word = wordLeaflet().submission.bundle as unknown as FhirBundle;
    const { list, documentBundle } = transformType2ToEma(word, leaflet, undefined, "as-written");
    const wrong = typed(documentBundle, [
      { system: EMA_DOCUMENT_TYPE_SYSTEM, code: "100000155532" },
    ]);
    expect(
      validateEmaPreflight(list, wrong, leaflet, "as-written").issue.map(
        ({ diagnostics }) => diagnostics,
      ),
    ).toEqual(["EMA Composition type is not the pl document's"]);
  });
});

describe("a package leaflet through the worker's pipeline", () => {
  const drawn = () => {
    const { submission, fidelityReport, sourceText } = createSyntheticSubmission(leaflet, {
      product: LEAFLET_PRODUCT_ID,
    });
    return { sourceKind: "document" as const, submission, fidelityReport, sourceText };
  };

  it("refuses a Type 2 leaflet in a dry run, from the fixture route or an approved drawn submission", async () => {
    for (const input of [{ sourceKind: "fixture" as const, source: source(true) }, drawn()]) {
      const error: unknown = await runPipeline(
        { runId: "00000000-0000-4000-8000-0000000001e0", sourceResource: "leaflet", ...input },
        mappings,
        config,
      ).then(
        () => undefined,
        (cause: unknown) => cause,
      );
      expect(error).toBeInstanceOf(TransformationError);
      expect((error as TransformationError).issues).toEqual([LEAFLET_TITLES_NOT_CARRIED]);
    }
  });

  // No leaflet persists until the query service and the signer read one: whatever approvals say,
  // a run that is not a dry run is refused before the gate or anything else reads.
  it.each([false, true])(
    "refuses to persist a leaflet, with its closed reason (approval enforcement %s)",
    async (enforced) => {
      const persisting = { ...config, DRY_RUN: false, APPROVAL_ENFORCEMENT: enforced };
      const word = wordLeaflet();
      for (const input of [
        drawn(),
        {
          sourceKind: "document" as const,
          submission: word.submission,
          fidelityReport: word.fidelityReport,
          sourceText: word.sourceText,
        },
      ]) {
        const error: unknown = await runPipeline(
          { runId: "00000000-0000-4000-8000-0000000001e1", sourceResource: "leaflet", ...input },
          mappings,
          persisting,
        ).then(
          () => undefined,
          (cause: unknown) => cause,
        );
        expect(error).toBeInstanceOf(Error);
        expect(pipelineFailure(error as Error)).toEqual({
          reason: "leaflet-not-readable",
          status: 422,
        });
      }
    },
  );

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
  // has no hyphen; a manifest key outside it is a section no submission can carry. So every key of
  // every manifest in fhir/mappings/, its rules' and its unmapped slots', must be one. SmPC
  // mapping 1.4.0 had two that were not (smpc.4.6.breast-feeding, smpc.5.2.pk-pd; 1.5.0 renamed
  // them), as leaflet mapping 1.0.0 had three.
  it("are contract source keys, in every manifest", async () => {
    const files = readdirSync("fhir/mappings").filter((file) => file.endsWith(".json"));
    expect(files).toEqual(expect.arrayContaining(["cap-pl-en.json", "cap-smpc-en.json"]));
    for (const file of files) {
      const mapping = await loadEmaMapping(path.resolve("fhir/mappings", file));
      const all = [
        ...keys(mapping.root),
        ...(mapping.unmapped ?? []).map((slot) => slot.sourceKey),
      ];
      expect([file, all.filter((key) => !SourceKey.safeParse(key).success)]).toEqual([file, []]);
    }
  });
});
