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

function searchSection(
  sections: CompositionSection[],
  sourceKey: string,
): CompositionSection | undefined {
  for (const section of sections) {
    const code = section.code.coding?.find(
      (coding) => coding.system === mapping.sourceCodeSystem,
    )?.code;
    if (code === sourceKey) return section;
    const nested = searchSection(section.section ?? [], sourceKey);
    if (nested !== undefined) return nested;
  }
  return undefined;
}

function findSection(sections: CompositionSection[], sourceKey: string): CompositionSection {
  const section = searchSection(sections, sourceKey);
  if (section === undefined) throw new Error(`Synthetic fixture has no section ${sourceKey}`);
  return section;
}

function transformIssues(source: FhirBundle, withMapping: EmaMapping = mapping): string[] {
  try {
    transformType2ToEma(source, withMapping);
  } catch (error) {
    expect(error).toBeInstanceOf(TransformationError);
    return (error as TransformationError).issues;
  }
  throw new Error("Expected the transformation to fail closed");
}

const NARRATIVE = '<div xmlns="http://www.w3.org/1999/xhtml"><p>Synthetic narrative.</p></div>';

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

  it("fails closed when a coded source subsection has no manifest rule", () => {
    const source = createSyntheticType2Bundle(mapping);
    const indications = findSection(composition(source).section, "smpc.4.1");
    indications.section = [
      {
        title: "4.1.1 Paediatric indications",
        code: { coding: [{ system: mapping.sourceCodeSystem, code: "smpc.4.1.1" }] },
        text: { status: "generated", div: NARRATIVE },
      },
    ];

    expect(transformIssues(source)).toContain(
      "Unmapped source section smpc.4.1.1 at Composition.section[0].section[3].section[0].section[0]",
    );
  });

  it("fails closed when a section without a source code carries narrative", () => {
    const source = createSyntheticType2Bundle(mapping);
    findSection(composition(source).section, "smpc.6").section?.push({
      title: "6.7 Additional information",
      code: { coding: [{ system: "https://khs.dev/fhir/CodeSystem/other", code: "extra" }] },
      text: { status: "generated", div: NARRATIVE },
    });

    expect(transformIssues(source)).toContain(
      "Uncoded source section with narrative at Composition.section[0].section[5].section[6]",
    );
  });

  it("fails closed when a source section sits under the wrong parent", () => {
    const source = createSyntheticType2Bundle(mapping);
    const sections = composition(source).section;
    const indications = findSection(sections, "smpc.4.1");
    const moved = removeSection(sections, "smpc.4.1");
    findSection(moved, "smpc.6").section?.push(indications);
    composition(source).section = moved;

    expect(transformIssues(source)).toContain(
      "Source section smpc.4.1 is under smpc.6, expected under smpc.4",
    );
  });

  it("fails closed when the root source section is not at the top level", () => {
    const source = createSyntheticType2Bundle(mapping);
    const root = composition(source).section[0];
    if (root === undefined) throw new Error("Synthetic fixture requires a root section");
    composition(source).section = [{ title: "Wrapper", code: { coding: [] }, section: [root] }];

    expect(transformIssues(source)).toContain(
      "Source section smpc is under uncoded section at Composition.section[0], expected under top level",
    );
  });

  it("fails closed when a mandatory leaf section has no narrative", () => {
    const source = createSyntheticType2Bundle(mapping);
    delete findSection(composition(source).section, "smpc.4.3").text;
    findSection(composition(source).section, "smpc.6.2").text = {
      status: "empty",
      div: '<div xmlns="http://www.w3.org/1999/xhtml"><p> </p></div>',
    };

    const issues = transformIssues(source);
    expect(issues).toContain("Mandatory source section smpc.4.3 has no narrative");
    expect(issues).toContain("Mandatory source section smpc.6.2 has no narrative");
  });

  it("does not require narrative on a section that has child rules", () => {
    const source = createSyntheticType2Bundle(mapping);
    delete findSection(composition(source).section, "smpc.4").text;

    expect(() => transformType2ToEma(source, mapping)).not.toThrow();
  });

  it("fails closed when the source declares a language other than English", () => {
    const source = createSyntheticType2Bundle(mapping);
    composition(source).language = "fr";
    source.language = "de-AT";

    const issues = transformIssues(source);
    expect(issues).toContain(
      "Source Composition.language fr is not English; the mapping is English-only",
    );
    expect(issues).toContain(
      "Source Bundle.language de-AT is not English; the mapping is English-only",
    );
  });

  it("fails closed on a mapping whose rules share a sourceKey or a targetCode", () => {
    // The loader refuses such a manifest; the transform refuses one built in memory too.
    const source = createSyntheticType2Bundle(mapping);
    const altered = structuredClone(mapping);
    const clinical = altered.root.children?.[3];
    const warnings = clinical?.children?.find(({ sourceKey }) => sourceKey === "smpc.4.4");
    if (warnings === undefined) throw new Error("Manifest requires a 4.4 rule");
    warnings.sourceKey = "smpc.4.3";
    warnings.targetCode = "200000029805";

    const issues = transformIssues(source, altered);
    expect(issues).toContain("Duplicate sourceKey smpc.4.3 in mapping manifest");
    expect(issues).toContain("Duplicate targetCode 200000029805 in mapping manifest");
  });

  it("accepts a regional English source and still publishes en", () => {
    const source = createSyntheticType2Bundle(mapping);
    composition(source).language = "en-GB";

    const result = transformType2ToEma(source, mapping);
    expect(composition(result.documentBundle).language).toBe("en");
  });
});
