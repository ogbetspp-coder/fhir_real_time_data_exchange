import { beforeAll, describe, expect, it } from "vitest";

import { loadEmaMapping, type EmaMapping } from "../src/fhir/mapping.js";
import {
  hasValidationErrors,
  validateEmaPreflight,
  validateType2Preflight,
} from "../src/fhir/preflight.js";
import { transformType2ToEma } from "../src/fhir/transform.js";
import type {
  CompositionSection,
  FhirBundle,
  FhirComposition,
  FhirResource,
  OperationOutcomeIssue,
} from "../src/fhir/types.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";

// Every refusal the two structural preflights can make, one row each. Each row starts from an
// input that passes, breaks exactly one thing, and states the complete list of error issues the
// preflight must answer with: code, location and wording. A preflight that stopped checking one
// thing would answer a row with fewer issues (or a success) and fail that row; one that reported
// more than the fault would fail it too.

let mapping: EmaMapping;
let source: FhirBundle;
let list: FhirResource;
let documentBundle: FhirBundle;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  source = createSyntheticType2Bundle(mapping);
  const target = transformType2ToEma(source, mapping);
  list = target.list;
  documentBundle = target.documentBundle;
});

type Expected = Pick<OperationOutcomeIssue, "code" | "expression"> & { diagnostics: string };

function errors(issues: readonly OperationOutcomeIssue[]): Expected[] {
  return issues
    .filter(({ severity }) => severity === "error")
    .map(({ code, diagnostics, expression }) => ({
      code,
      diagnostics: diagnostics ?? "",
      ...(expression === undefined ? {} : { expression }),
    }));
}

function composition(bundle: FhirBundle): FhirComposition {
  return bundle.entry[0]?.resource as FhirComposition;
}

function rootSection(bundle: FhirBundle): CompositionSection {
  const section = composition(bundle).section[0];
  if (section === undefined) throw new Error("fixture has no root section");
  return section;
}

function childSection(bundle: FhirBundle, position: number): CompositionSection {
  const section = rootSection(bundle).section?.[position];
  if (section === undefined) throw new Error(`fixture has no child section ${String(position)}`);
  return section;
}

function withoutProfile(resource: FhirResource, profile: string): void {
  resource.meta = {
    ...resource.meta,
    profile: (resource.meta?.profile ?? []).filter((value) => value !== profile),
  };
}

function setCode(section: CompositionSection, system: string, code: string | undefined): void {
  section.code = {
    ...section.code,
    coding: (section.code.coding ?? []).flatMap((coding) => {
      if (coding.system !== system) return [coding];
      return code === undefined ? [] : [{ ...coding, code }];
    }),
  };
}

describe("the Type 2 preflight", () => {
  it("passes the synthetic graph with one success issue and nothing else", () => {
    const outcome = validateType2Preflight(structuredClone(source));
    expect(outcome.issue).toEqual([
      {
        severity: "success",
        code: "informational",
        diagnostics: "Canonical Type 2 preflight passed",
      },
    ]);
    expect(hasValidationErrors(outcome)).toBe(false);
  });

  const cases: {
    name: string;
    mutate: (bundle: FhirBundle) => void;
    expected: () => Expected[];
  }[] = [
    {
      name: "a Bundle that is not a document",
      mutate: (bundle) => {
        bundle.type = "collection";
      },
      expected: () => [
        {
          code: "value",
          diagnostics: "Type 2 ePI must use Bundle.type=document",
          expression: ["Bundle.type"],
        },
      ],
    },
    {
      name: "a first entry that is not the Composition",
      mutate: (bundle) => {
        const [first, ...rest] = bundle.entry;
        if (first === undefined) throw new Error("empty fixture");
        bundle.entry = [...rest, first];
      },
      expected: () => [
        {
          code: "structure",
          diagnostics: "A document Bundle must have Composition as its first entry",
          expression: ["Bundle.entry[0]"],
        },
      ],
    },
    {
      name: "a graph missing one required resource type",
      mutate: (bundle) => {
        bundle.entry = bundle.entry.filter(
          ({ resource }) => resource.resourceType !== "SubstanceDefinition",
        );
      },
      expected: () => [
        {
          code: "required",
          diagnostics: "Type 2 graph is missing SubstanceDefinition",
          expression: ["Bundle.entry"],
        },
      ],
    },
    {
      name: "a graph missing every non-Composition resource type",
      mutate: (bundle) => {
        bundle.entry = bundle.entry.filter(
          ({ resource }) => resource.resourceType === "Composition",
        );
      },
      expected: () =>
        [
          "Organization",
          "MedicinalProductDefinition",
          "RegulatedAuthorization",
          "PackagedProductDefinition",
          "ManufacturedItemDefinition",
          "AdministrableProductDefinition",
          "Ingredient",
          "SubstanceDefinition",
        ].map((type) => ({
          code: "required",
          diagnostics: `Type 2 graph is missing ${type}`,
          expression: ["Bundle.entry"],
        })),
    },
    {
      name: "an empty Bundle",
      mutate: (bundle) => {
        bundle.entry = [];
      },
      expected: () => [
        {
          code: "structure",
          diagnostics: "A document Bundle must have Composition as its first entry",
          expression: ["Bundle.entry[0]"],
        },
        ...[
          "Composition",
          "Organization",
          "MedicinalProductDefinition",
          "RegulatedAuthorization",
          "PackagedProductDefinition",
          "ManufacturedItemDefinition",
          "AdministrableProductDefinition",
          "Ingredient",
          "SubstanceDefinition",
        ].map((type) => ({
          code: "required",
          diagnostics: `Type 2 graph is missing ${type}`,
          expression: ["Bundle.entry"],
        })),
      ],
    },
    {
      name: "two entries sharing a fullUrl, reported once however often it repeats",
      mutate: (bundle) => {
        const shared = bundle.entry[1]?.fullUrl ?? "";
        const second = bundle.entry[2];
        const third = bundle.entry[3];
        if (second === undefined || third === undefined) throw new Error("small fixture");
        second.fullUrl = shared;
        third.fullUrl = shared;
      },
      expected: () => [
        {
          code: "duplicate",
          diagnostics: `Duplicate Bundle.entry.fullUrl ${source.entry[1]?.fullUrl ?? ""}`,
          expression: ["Bundle.entry"],
        },
      ],
    },
  ];

  it.each(cases)("refuses $name", ({ mutate, expected }) => {
    const bundle = structuredClone(source);
    mutate(bundle);
    const outcome = validateType2Preflight(bundle);

    expect(errors(outcome.issue)).toEqual(expected());
    expect(outcome.issue.some(({ severity }) => severity === "success")).toBe(false);
    expect(hasValidationErrors(outcome)).toBe(true);
  });
});

describe("the EMA preflight", () => {
  it("passes the transformed package with one success issue and nothing else", () => {
    const outcome = validateEmaPreflight(
      structuredClone(list),
      structuredClone(documentBundle),
      mapping,
    );
    expect(outcome.issue).toEqual([
      {
        severity: "success",
        code: "informational",
        diagnostics: "EMA structural preflight passed",
      },
    ]);
    expect(hasValidationErrors(outcome)).toBe(false);
  });

  const cases: {
    name: string;
    mutate: (list: FhirResource, bundle: FhirBundle) => void;
    expected: () => Expected[];
  }[] = [
    {
      name: "a List without the EMA List profile",
      mutate: (target) => {
        withoutProfile(target, mapping.profiles.list);
      },
      expected: () => [
        {
          code: "value",
          diagnostics: "EMA List profile is missing",
          expression: ["List.meta.profile"],
        },
      ],
    },
    {
      name: "a List with no meta at all",
      mutate: (target) => {
        delete target.meta;
      },
      expected: () => [
        {
          code: "value",
          diagnostics: "EMA List profile is missing",
          expression: ["List.meta.profile"],
        },
      ],
    },
    {
      name: "a Bundle without the EMA Bundle profile",
      mutate: (_list, bundle) => {
        withoutProfile(bundle, mapping.profiles.bundle);
      },
      expected: () => [
        {
          code: "value",
          diagnostics: "EMA Bundle profile is missing",
          expression: ["Bundle.meta.profile"],
        },
      ],
    },
    {
      name: "a Bundle whose first entry is not the Composition",
      mutate: (_list, bundle) => {
        const [first, ...rest] = bundle.entry;
        if (first === undefined) throw new Error("empty fixture");
        bundle.entry = [...rest, first];
      },
      expected: () => [
        {
          code: "structure",
          diagnostics: "EMA Bundle first entry is not Composition",
          expression: ["Bundle.entry[0]"],
        },
      ],
    },
    {
      name: "a Bundle with no entries",
      mutate: (_list, bundle) => {
        bundle.entry = [];
      },
      expected: () => [
        {
          code: "structure",
          diagnostics: "EMA Bundle first entry is not Composition",
          expression: ["Bundle.entry[0]"],
        },
      ],
    },
    {
      name: "a Composition missing each of its EMA profiles",
      mutate: (_list, bundle) => {
        delete composition(bundle).meta;
      },
      expected: () =>
        mapping.profiles.composition.map((profile) => ({
          code: "value",
          diagnostics: `EMA Composition profile ${profile} is missing`,
          expression: ["Composition.meta.profile"],
        })),
    },
    {
      name: "a Composition missing one EMA profile",
      mutate: (_list, bundle) => {
        withoutProfile(composition(bundle), mapping.profiles.composition[1] ?? "");
      },
      expected: () => [
        {
          code: "value",
          diagnostics: `EMA Composition profile ${mapping.profiles.composition[1] ?? ""} is missing`,
          expression: ["Composition.meta.profile"],
        },
      ],
    },
    {
      name: "a Composition with no root section",
      mutate: (_list, bundle) => {
        composition(bundle).section = [];
      },
      expected: () => [
        {
          code: "required",
          diagnostics: `Missing EMA section ${mapping.root.targetCode}`,
          expression: ["Composition.section[0]"],
        },
      ],
    },
    {
      name: "a root section carrying the wrong EMA code",
      mutate: (_list, bundle) => {
        setCode(rootSection(bundle), mapping.targetCodeSystem, "999999999999");
      },
      expected: () => [
        {
          code: "value",
          diagnostics: `Expected EMA code ${mapping.root.targetCode}, received 999999999999`,
          expression: ["Composition.section[0].code"],
        },
      ],
    },
    {
      name: "a root section carrying no EMA code",
      mutate: (_list, bundle) => {
        setCode(rootSection(bundle), mapping.targetCodeSystem, undefined);
      },
      expected: () => [
        {
          code: "value",
          diagnostics: `Expected EMA code ${mapping.root.targetCode}, received none`,
          expression: ["Composition.section[0].code"],
        },
      ],
    },
    {
      name: "a root section carrying the wrong title",
      mutate: (_list, bundle) => {
        rootSection(bundle).title = "Summary of product characteristics";
      },
      expected: () => [
        {
          code: "value",
          diagnostics: `Expected title "${mapping.root.title}"`,
          expression: ["Composition.section[0].title"],
        },
      ],
    },
    {
      name: "a missing child section, named by its position",
      mutate: (_list, bundle) => {
        const root = rootSection(bundle);
        root.section = (root.section ?? []).slice(0, 3);
      },
      expected: () =>
        (mapping.root.children ?? []).slice(3).map((rule, offset) => ({
          code: "required",
          diagnostics: `Missing EMA section ${rule.targetCode}`,
          expression: [`Composition.section[0].section[${String(3 + offset)}]`],
        })),
    },
    {
      name: "a child section carrying the wrong code and title",
      mutate: (_list, bundle) => {
        const child = childSection(bundle, 2);
        setCode(child, mapping.targetCodeSystem, "111111111111");
        child.title = "3. Something else";
      },
      expected: () => {
        const rule = mapping.root.children?.[2];
        if (rule === undefined) throw new Error("mapping has no third child");
        return [
          {
            code: "value",
            diagnostics: `Expected EMA code ${rule.targetCode}, received 111111111111`,
            expression: ["Composition.section[0].section[2].code"],
          },
          {
            code: "value",
            diagnostics: `Expected title "${rule.title}"`,
            expression: ["Composition.section[0].section[2].title"],
          },
        ];
      },
    },
    {
      name: "a child section carrying the source code system's code but not the EMA one",
      mutate: (_list, bundle) => {
        const child = childSection(bundle, 0);
        child.code = {
          coding: [
            {
              system: mapping.sourceCodeSystem,
              code: mapping.root.children?.[0]?.targetCode ?? "",
            },
          ],
        };
      },
      expected: () => [
        {
          code: "value",
          diagnostics: `Expected EMA code ${mapping.root.children?.[0]?.targetCode ?? ""}, received none`,
          expression: ["Composition.section[0].section[0].code"],
        },
      ],
    },
  ];

  it.each(cases)("refuses $name", ({ mutate, expected }) => {
    const target = structuredClone(list);
    const bundle = structuredClone(documentBundle);
    mutate(target, bundle);
    const outcome = validateEmaPreflight(target, bundle, mapping);

    expect(errors(outcome.issue)).toEqual(expected());
    expect(outcome.issue.some(({ severity }) => severity === "success")).toBe(false);
    expect(hasValidationErrors(outcome)).toBe(true);
  });
});

describe("hasValidationErrors", () => {
  it("counts fatal and error, and nothing milder", () => {
    const outcome = (severity: OperationOutcomeIssue["severity"]) => ({
      resourceType: "OperationOutcome" as const,
      issue: [{ severity, code: "x" }],
    });
    expect(hasValidationErrors(outcome("fatal"))).toBe(true);
    expect(hasValidationErrors(outcome("error"))).toBe(true);
    expect(hasValidationErrors(outcome("warning"))).toBe(false);
    expect(hasValidationErrors(outcome("information"))).toBe(false);
    expect(hasValidationErrors(outcome("success"))).toBe(false);
  });
});
