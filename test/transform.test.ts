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

function div(content: string): string {
  return `<div xmlns="http://www.w3.org/1999/xhtml">${content}</div>`;
}

const NARRATIVE = div("<p>Synthetic narrative.</p>");

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

    expect(transformIssues(source)).toEqual([
      "Unmapped source section smpc.4.1.1 at Composition.section[0].section[3].section[0].section[0]",
    ]);
  });

  it("fails closed when a section without a source code carries narrative", () => {
    const source = createSyntheticType2Bundle(mapping);
    findSection(composition(source).section, "smpc.6").section?.push({
      title: "6.7 Additional information",
      code: { coding: [{ system: "https://khs.dev/fhir/CodeSystem/other", code: "extra" }] },
      text: { status: "generated", div: NARRATIVE },
    });

    expect(transformIssues(source)).toEqual([
      "Uncoded source section with narrative at Composition.section[0].section[5].section[6]",
    ]);
  });

  it("fails closed when a section without a source code carries only a picture", () => {
    // Dropping the section would drop the picture with it.
    const source = createSyntheticType2Bundle(mapping);
    findSection(composition(source).section, "smpc.6").section?.push({
      title: "6.7 Additional information",
      code: { coding: [{ system: "https://khs.dev/fhir/CodeSystem/other", code: "extra" }] },
      text: {
        status: "generated",
        div: '<div xmlns="http://www.w3.org/1999/xhtml"><p><img src="data:image/png;base64,AA=="/></p></div>',
      },
    });

    expect(transformIssues(source)).toEqual([
      "Uncoded source section with narrative at Composition.section[0].section[5].section[6]",
    ]);
  });

  it.each([
    ["CDATA", "<p><![CDATA[Do not use in children]]></p>"],
    ["a comment", "<!-- Do not use in children -->"],
    ["an image", '<img src="https://khs.dev/pictogram.png"/>'],
    ["an unknown entity", "<p>&nbsp;</p>"],
  ])("fails closed when a section without a source code carries %s", (_name, content) => {
    const source = createSyntheticType2Bundle(mapping);
    findSection(composition(source).section, "smpc.6").section?.push({
      title: "6.7 Additional information",
      code: { coding: [] },
      text: { status: "generated", div: div(content) },
    });

    expect(transformIssues(source)).toEqual([
      "Uncoded source section with unreadable narrative at Composition.section[0].section[5].section[6]",
    ]);
  });

  it("accepts a section without a source code that is an empty container", () => {
    const source = createSyntheticType2Bundle(mapping);
    const pharmaceutical = findSection(composition(source).section, "smpc.6");
    pharmaceutical.section?.push(
      { title: "Container", code: { coding: [] } },
      { title: "Blank", code: { text: "blank" }, text: { status: "empty", div: div("<p> </p>") } },
    );

    const result = transformType2ToEma(source, mapping);
    expect(result.outputHash).toBe(
      transformType2ToEma(createSyntheticType2Bundle(mapping), mapping).outputHash,
    );
  });

  it("fails closed when a source section carries two codes in the source code system", () => {
    const source = createSyntheticType2Bundle(mapping);
    findSection(composition(source).section, "smpc.4.3").code.coding?.push({
      system: mapping.sourceCodeSystem,
      code: "smpc.4.4",
    });

    expect(transformIssues(source)).toEqual([
      "Missing mandatory source section smpc.4.3",
      "Ambiguous source section at Composition.section[0].section[3].section[2]: 2 codes in the source code system",
    ]);
  });

  it("fails closed with an issue, not a TypeError, when a source section has no code", () => {
    const source = createSyntheticType2Bundle(mapping);
    const incompatibilities: Partial<CompositionSection> = findSection(
      composition(source).section,
      "smpc.6.2",
    );
    delete incompatibilities.code;

    expect(transformIssues(source)).toEqual([
      "Missing mandatory source section smpc.6.2",
      "Source section at Composition.section[0].section[5].section[1] has no code",
      "Uncoded source section with narrative at Composition.section[0].section[5].section[1]",
    ]);
  });

  it("fails closed when a source section carries an element the mapping would drop", () => {
    const source = createSyntheticType2Bundle(mapping);
    Object.assign(findSection(composition(source).section, "smpc.4.3"), {
      entry: [{ reference: "https://khs.dev/fhir/Ingredient/synthetic" }],
      emptyReason: { coding: [{ code: "unavailable" }] },
    });

    expect(transformIssues(source)).toEqual([
      "Source section at Composition.section[0].section[3].section[2] carries entry, which the mapping would drop",
      "Source section at Composition.section[0].section[3].section[2] carries emptyReason, which the mapping would drop",
    ]);
  });

  it("fails closed when source subsections are out of manifest order", () => {
    const source = createSyntheticType2Bundle(mapping);
    const clinical = findSection(composition(source).section, "smpc.4");
    const [indications, posology, ...rest] = clinical.section ?? [];
    if (indications === undefined || posology === undefined) {
      throw new Error("Synthetic fixture requires sections 4.1 and 4.2");
    }
    clinical.section = [posology, indications, ...rest];

    expect(transformIssues(source)).toEqual([
      "Source section smpc.4.2 comes before smpc.4.1 under smpc.4; the manifest orders smpc.4.1 first",
    ]);
  });

  it("fails closed when a source section sits under the wrong parent", () => {
    const source = createSyntheticType2Bundle(mapping);
    const sections = composition(source).section;
    const indications = findSection(sections, "smpc.4.1");
    const moved = removeSection(sections, "smpc.4.1");
    findSection(moved, "smpc.6").section?.push(indications);
    composition(source).section = moved;

    expect(transformIssues(source)).toEqual([
      "Source section smpc.4.1 is under smpc.6, expected under smpc.4",
    ]);
  });

  it("fails closed when the root source section is not at the top level", () => {
    const source = createSyntheticType2Bundle(mapping);
    const root = composition(source).section[0];
    if (root === undefined) throw new Error("Synthetic fixture requires a root section");
    composition(source).section = [{ title: "Wrapper", code: { coding: [] }, section: [root] }];

    expect(transformIssues(source)).toEqual([
      "Source section smpc is under uncoded section at Composition.section[0], expected under top level",
    ]);
  });

  it("fails closed when a mandatory leaf section has no narrative", () => {
    const source = createSyntheticType2Bundle(mapping);
    delete findSection(composition(source).section, "smpc.4.3").text;
    findSection(composition(source).section, "smpc.6.2").text = {
      status: "empty",
      div: div("<p> </p>"),
    };

    expect(transformIssues(source)).toEqual([
      "Mandatory source section smpc.4.3 has no narrative",
      "Mandatory source section smpc.6.2 has no narrative",
    ]);
  });

  it.each([
    ["a non-breaking space by number", "<p>&#160;</p>", "has no narrative"],
    ["a zero-width space by number", "<p>&#x200B;</p>", "has no narrative"],
    [
      "a zero-width space as a character",
      `<p>${String.fromCodePoint(0x200b)}</p>`,
      "has no narrative",
    ],
    [
      "a byte-order mark, a word joiner and a soft hyphen",
      "<p>&#xFEFF;&#x2060;&#xAD; </p>",
      "has no narrative",
    ],
    ["a named entity the scanner does not know", "<p>&nbsp;</p>", "has unreadable narrative"],
    ["a comment holding a >", "<!-- a > b -->", "has unreadable narrative"],
    ["an attribute holding a >", '<p title="x>y"></p>', "has unreadable narrative"],
    ["a comment holding an img", "<!-- <img> -->", "has unreadable narrative"],
    ["an image only", '<img src="https://khs.dev/pictogram.png"/>', "has unreadable narrative"],
    // fidelity-norm/3.0.0: a table's grid markers are structure, not text.
    ["a table of empty cells", "<table><tr><td></td><td> </td></tr></table>", "has no narrative"],
    ["a picture by reference", '<p><img src="images/logo.png"/></p>', "has unreadable narrative"],
    // A picture can draw nothing (these bytes draw a broken-image icon), and what one shows is
    // never read, so a mandatory section needs text.
    ["a picture", '<p><img src="data:image/png;base64,AA=="/></p>', "has no narrative"],
  ])("fails closed when a mandatory leaf section holds only %s", (_name, content, outcome) => {
    const source = createSyntheticType2Bundle(mapping);
    findSection(composition(source).section, "smpc.4.3").text = {
      status: "generated",
      div: div(content),
    };

    expect(transformIssues(source)).toEqual([`Mandatory source section smpc.4.3 ${outcome}`]);
  });

  it.each([
    ["a numbered item", '<ol start="2"><li></li></ol>'],
    ["a table with one filled cell", "<table><tr><td></td><td>x</td></tr></table>"],
    ["an Ogham space mark, which is drawn as a stroke", "<p>&#x1680;</p>"],
  ])("counts %s as narrative a reader sees", (_name, content) => {
    const source = createSyntheticType2Bundle(mapping);
    findSection(composition(source).section, "smpc.4.3").text = {
      status: "generated",
      div: div(content),
    };

    expect(() => transformType2ToEma(source, mapping)).not.toThrow();
  });

  it("fails closed when 4.8 has no narrative above its reporting subsection", () => {
    // 4.8 has a child rule, so only the manifest's narrative flag makes its own text mandatory.
    const source = createSyntheticType2Bundle(mapping);
    delete findSection(composition(source).section, "smpc.4.8").text;

    expect(transformIssues(source)).toEqual(["Mandatory source section smpc.4.8 has no narrative"]);
  });

  it("does not require narrative on a section whose rule has children and no narrative flag", () => {
    const source = createSyntheticType2Bundle(mapping);
    delete findSection(composition(source).section, "smpc.4").text;
    delete findSection(composition(source).section, "smpc.4.2").text;

    expect(() => transformType2ToEma(source, mapping)).not.toThrow();
  });

  it("fails closed when the source declares a language other than English", () => {
    const source = createSyntheticType2Bundle(mapping);
    composition(source).language = "fr";
    source.language = "de-AT";

    expect(transformIssues(source)).toEqual([
      "Source Composition.language fr is not English; the mapping is English-only",
      "Source Bundle.language de-AT is not English; the mapping is English-only",
    ]);
  });

  it.each(["eng", "en-", "en-x-fr", "en-GB-x-fr", "en-Cyrl", "en-GB-oxendict", "fr-CA"])(
    "fails closed on the language tag %j",
    (language) => {
      const source = createSyntheticType2Bundle(mapping);
      composition(source).language = language;

      expect(transformIssues(source)).toEqual([
        `Source Composition.language ${language} is not English; the mapping is English-only`,
      ]);
    },
  );

  it("fails closed when the source Composition or Bundle declares no language", () => {
    const source = createSyntheticType2Bundle(mapping);
    delete composition(source).language;
    delete source.language;

    expect(transformIssues(source)).toEqual([
      "Source Composition.language is missing; the mapping is English-only",
      "Source Bundle.language is missing; the mapping is English-only",
    ]);
  });

  it.each(["en", "EN", "en-GB", "en-us", "en-001", "en-Latn", "en-Latn-GB"])(
    "accepts the English language tag %j and publishes en on the Composition and the Bundle",
    (language) => {
      const source = createSyntheticType2Bundle(mapping);
      composition(source).language = language;
      source.language = language;

      const result = transformType2ToEma(source, mapping);
      expect(composition(result.documentBundle).language).toBe("en");
      expect(result.documentBundle.language).toBe("en");
    },
  );

  it("fails closed on a mapping whose rules share a sourceKey or a targetCode", () => {
    // The loader refuses such a manifest; the transform refuses one built in memory too.
    const source = createSyntheticType2Bundle(mapping);
    const altered = structuredClone(mapping);
    const clinical = altered.root.children?.[3];
    const warnings = clinical?.children?.find(({ sourceKey }) => sourceKey === "smpc.4.4");
    if (warnings === undefined) throw new Error("Manifest requires a 4.4 rule");
    warnings.sourceKey = "smpc.4.3";
    warnings.targetCode = "200000029805";

    expect(transformIssues(source, altered)).toEqual([
      "Duplicate sourceKey smpc.4.3 in mapping manifest",
      "Duplicate targetCode 200000029805 in mapping manifest",
      "Unmapped source section smpc.4.4 at Composition.section[0].section[3].section[3]",
    ]);
  });
});
