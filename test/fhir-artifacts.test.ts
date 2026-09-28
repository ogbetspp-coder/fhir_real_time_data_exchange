import { readFileSync } from "node:fs";

import { beforeAll, describe, expect, it } from "vitest";

import type { EmaMapping } from "../src/fhir/mapping.js";
import { loadEmaMapping } from "../src/fhir/mapping.js";
import { transformType2ToEma } from "../src/fhir/transform.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { SMOKE_PRODUCT_ID } from "../src/fixtures/synthetic-products.js";
import { stableUuid } from "../src/lib/hash.js";

// The published ConceptMap and StructureMap (fhir/generated/, from
// scripts/fhir/generate-artifacts.ts) describe the crosswalk; src/fhir/transform.ts executes it.
// The official validator checks their form in CI (scripts/ci/emit-validation-set.ts); this holds
// what they say to what the transform does. The StructureMap once said the document identifier
// was copied, while the transform mints a new one.

type Parameter = { valueString?: string; valueId?: string };
type Target = {
  context: string;
  element: string;
  variable?: string;
  transform?: string;
  parameter?: Parameter[];
};
type Rule = {
  name: string;
  documentation?: string;
  source: { context: string; element: string; variable: string }[];
  target: Target[];
};
type StructureMap = { group: { typeMode?: string; rule: Rule[] }[] };
type ConceptMap = {
  sourceScopeCanonical?: string;
  targetScopeCanonical?: string;
  group: {
    source: string;
    target: string;
    element: { code: string; target: { code: string }[] }[];
  }[];
};

function artifact<T>(file: string): T {
  return JSON.parse(readFileSync(`fhir/generated/${file}`, "utf8")) as T;
}

let mapping: EmaMapping;
let structureMap: StructureMap;
let conceptMap: ConceptMap;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  structureMap = artifact("StructureMap-type2-to-ema-cap-smpc-en.json");
  conceptMap = artifact("ConceptMap-canonical-to-ema-cap-smpc-en.json");
});

function rule(name: string): Rule {
  const found = structureMap.group
    .flatMap((group) => group.rule)
    .find((each) => each.name === name);
  if (found === undefined) throw new Error(`StructureMap has no rule ${name}`);
  return found;
}

describe("the published StructureMap", () => {
  it("says of the document identifier what the transform does: a new system and a derived value", () => {
    const source = createSyntheticType2Bundle(mapping, { product: SMOKE_PRODUCT_ID });
    const { documentBundle } = transformType2ToEma(source, mapping);
    const sourceIdentifier = source.identifier as { value: string };
    const targetIdentifier = documentBundle.identifier as { system: string; value: string };

    const derive = rule("deriveDocumentIdentifier");
    const system = derive.target.find((target) => target.element === "system");
    expect(system?.parameter?.[0]?.valueString).toBe(targetIdentifier.system);
    // No target copies the source identifier's value, and the value it names is the one minted.
    expect(derive.target.some((target) => target.element === "value")).toBe(false);
    expect(targetIdentifier.value).not.toBe(sourceIdentifier.value);
    const namespace = /namespace "([^"]+)"/.exec(derive.documentation ?? "")?.[1];
    expect(namespace).toBeDefined();
    expect(targetIdentifier.value).toBe(stableUuid(namespace ?? "", sourceIdentifier.value));
    expect(documentBundle.id).toBe(targetIdentifier.value);
  });

  it("copies the timestamp, as the transform does", () => {
    const source = createSyntheticType2Bundle(mapping, { product: SMOKE_PRODUCT_ID });
    const { documentBundle } = transformType2ToEma(source, mapping);
    const copy = rule("copyDocumentTimestamp");
    expect(copy.target).toEqual([
      expect.objectContaining({ element: "timestamp", transform: "copy" }),
    ]);
    expect(documentBundle.timestamp).toBe(source.timestamp);
  });

  it("names no group type mode, which R5 has no 'none' for", () => {
    expect(structureMap.group.every((group) => group.typeMode === undefined)).toBe(true);
  });
});

describe("the published ConceptMap", () => {
  it("scopes by value set, never by the code systems the groups name", () => {
    const systems = [mapping.sourceCodeSystem, mapping.targetCodeSystem];
    expect(systems).not.toContain(conceptMap.sourceScopeCanonical);
    expect(systems).not.toContain(conceptMap.targetScopeCanonical);
    expect(conceptMap.targetScopeCanonical).toBe(
      "http://ema.europa.eu/fhir/ValueSet/EUepismpcqrdcodesVs",
    );
    expect(conceptMap.group.map(({ source, target }) => [source, target])).toEqual([systems]);
  });
});
