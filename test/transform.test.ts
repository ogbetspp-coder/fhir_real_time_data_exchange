import { beforeAll, describe, expect, it } from "vitest";

import type { EmaMapping } from "../src/fhir/mapping.js";
import { loadEmaMapping } from "../src/fhir/mapping.js";
import {
  hasValidationErrors,
  validateEmaPreflight,
  validateType2Preflight,
} from "../src/fhir/preflight.js";
import { TransformationError, transformType2ToEma } from "../src/fhir/transform.js";
import type { CompositionSection, FhirBundle, FhirComposition } from "../src/fhir/types.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { canonicalJson } from "../src/lib/hash.js";

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

function composition(bundle: FhirBundle): FhirComposition {
  return bundle.entry[0]?.resource as FhirComposition;
}

function removeSection(sections: CompositionSection[], sourceKey: string): CompositionSection[] {
  return sections
    .filter(
      (section) =>
        section.code.coding?.find((coding) => coding.system === mapping.sourceCodeSystem)?.code !==
        sourceKey,
    )
    .map((section) => ({
      ...section,
      ...(section.section === undefined
        ? {}
        : { section: removeSection(section.section, sourceKey) }),
    }));
}

describe("deterministic Type 2 to EMA conversion", () => {
  it("maps the complete synthetic Type 2 graph and passes structural gates", () => {
    const source = createSyntheticType2Bundle(mapping);
    const sourceSnapshot = canonicalJson(source);
    const result = transformType2ToEma(source, mapping);

    expect(hasValidationErrors(validateType2Preflight(source))).toBe(false);
    expect(
      hasValidationErrors(validateEmaPreflight(result.list, result.documentBundle, mapping)),
    ).toBe(false);
    expect(canonicalJson(source)).toBe(sourceSnapshot);
    expect(result.mappingDecisions).toHaveLength(32);
    expect(result.mappingDecisions.every(({ narrativePreserved }) => narrativePreserved)).toBe(
      true,
    );

    const targetComposition = composition(result.documentBundle);
    expect(targetComposition.meta?.profile).toEqual(mapping.profiles.composition);
    expect(targetComposition.section[0]?.code.coding?.[0]?.code).toBe("200000029791");
    expect(targetComposition.section[0]?.section?.[3]?.code.coding?.[0]?.code).toBe("200000029798");
  });

  it("produces byte-identical canonical output for the same input", () => {
    const source = createSyntheticType2Bundle(mapping);
    const first = transformType2ToEma(source, mapping);
    const second = transformType2ToEma(structuredClone(source), mapping);

    expect(canonicalJson(first)).toBe(canonicalJson(second));
    expect(first.outputHash).toBe(second.outputHash);
  });

  it("fails closed when a mandatory source section is missing", () => {
    const source = createSyntheticType2Bundle(mapping);
    composition(source).section = removeSection(composition(source).section, "smpc.4.3");

    expect(() => transformType2ToEma(source, mapping)).toThrow(TransformationError);
    try {
      transformType2ToEma(source, mapping);
    } catch (error) {
      expect(error).toBeInstanceOf(TransformationError);
      expect((error as TransformationError).issues).toContain(
        "Missing mandatory source section smpc.4.3",
      );
    }
  });

  it("fails closed when a source section is ambiguous", () => {
    const source = createSyntheticType2Bundle(mapping);
    const root = composition(source).section[0];
    const duplicate = structuredClone(root?.section?.[0]);
    expect(root).toBeDefined();
    expect(duplicate).toBeDefined();
    if (root === undefined || duplicate === undefined) {
      throw new Error("Synthetic fixture requires a nested Composition section");
    }
    root.section?.push(duplicate);

    expect(() => transformType2ToEma(source, mapping)).toThrow(/transformation failed closed/i);
  });
});
