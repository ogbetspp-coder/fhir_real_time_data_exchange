import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";

import { beforeAll, describe, expect, it } from "vitest";

import type { EmaMapping, SectionRule } from "../src/fhir/mapping.js";
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

function artifact(file: string): unknown {
  return JSON.parse(readFileSync(`fhir/generated/${file}`, "utf8"));
}

let mapping: EmaMapping;
let structureMap: StructureMap;
let conceptMap: ConceptMap;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  structureMap = artifact("StructureMap-type2-to-ema-cap-smpc-en.json") as StructureMap;
  conceptMap = artifact("ConceptMap-canonical-to-ema-cap-smpc-en.json") as ConceptMap;
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

  // The documented algorithm, carried out here from the text alone rather than through
  // stableUuid, so a change to either the code or the text that parts them fails.
  it("documents a derivation that, carried out independently, gives the transform's value", () => {
    const documentation = rule("deriveDocumentIdentifier").documentation ?? "";
    const namespace = /namespace "([^"]+)"/.exec(documentation)?.[1] ?? "";
    for (const step of [
      `the SHA-256 of the UTF-8 string "${namespace}:" followed by the value, in lowercase hex`,
      "keep its first 32 hex digits",
      "set the 13th digit to 5 and the 17th digit to a",
      "in groups of 8-4-4-4-12 joined by hyphens",
    ]) {
      expect(documentation).toContain(step);
    }

    const independently = (value: string): string => {
      const first32 = createHash("sha256")
        .update(`${namespace}:${value}`, "utf8")
        .digest("hex")
        .slice(0, 32);
      // The 13th digit (index 12) is 5 and the 17th (index 16) is a.
      const hex = `${first32.slice(0, 12)}5${first32.slice(13, 16)}a${first32.slice(17)}`;
      return [
        hex.slice(0, 8),
        hex.slice(8, 12),
        hex.slice(12, 16),
        hex.slice(16, 20),
        hex.slice(20, 32),
      ].join("-");
    };
    const source = createSyntheticType2Bundle(mapping, { product: SMOKE_PRODUCT_ID });
    const sourceValue = (source.identifier as { value: string }).value;
    const { documentBundle } = transformType2ToEma(source, mapping);
    expect(independently(sourceValue)).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-a[0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(independently(sourceValue)).toBe(stableUuid(namespace, sourceValue));
    expect(independently(sourceValue)).toBe((documentBundle.identifier as { value: string }).value);
    // And for values other than the fixture's, non-ASCII among them.
    for (const value of ["", "x", "EPI/24/35", "ünïcode-ε"]) {
      expect(independently(value)).toBe(stableUuid(namespace, value));
    }
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
    expect(conceptMap.sourceScopeCanonical).toBe(
      "https://khs.dev/fhir/ValueSet/canonical-smpc-sections",
    );
    expect(conceptMap.group.map(({ source, target }) => [source, target])).toEqual([systems]);
  });
});

type Concept = { code: string; display: string; concept?: Concept[] };

describe("the canonical section code system", () => {
  it("is the mapping's section tree: every key, its heading, nested as the sections are", () => {
    const codeSystem = artifact("CodeSystem-canonical-smpc-sections.json") as {
      url: string;
      version: string;
      content: string;
      caseSensitive: boolean;
      concept: Concept[];
    };
    const tree = (rule: SectionRule): Concept => ({
      code: rule.sourceKey,
      display: rule.title,
      ...(rule.children === undefined ? {} : { concept: rule.children.map(tree) }),
    });
    expect(codeSystem).toMatchObject({
      url: mapping.sourceCodeSystem,
      version: mapping.mappingVersion,
      content: "complete",
      caseSensitive: true,
    });
    expect(codeSystem.concept).toEqual([tree(mapping.root)]);
  });
});

// The package the validator loads (Dockerfile.validator, a fifth -ig) is the committed resources
// and nothing else, in an archive whose bytes depend on them alone: npm run artifacts:check
// regenerates it and fails on any byte that differs.
describe("the repository's own package", () => {
  const gzip = readFileSync("fhir/generated/dev.khs.fhir.epi.tgz");
  const tar = gunzipSync(gzip);
  const members: { name: string; mode: string; owner: string; mtime: string; data: string }[] = [];
  for (let at = 0; tar[at] !== 0;) {
    const field = (start: number, length: number) =>
      tar
        .subarray(at + start, at + start + length)
        .toString("latin1")
        .replace(/\0.*$/s, "");
    const size = Number.parseInt(field(124, 12), 8);
    members.push({
      name: field(0, 100),
      mode: field(100, 8),
      owner: `${field(108, 8)}:${field(116, 8)}:${field(265, 32)}:${field(297, 32)}`,
      mtime: field(136, 12),
      data: tar.subarray(at + 512, at + 512 + size).toString("utf8"),
    });
    at += 512 + Math.ceil(size / 512) * 512;
  }

  it("is gzip with no time and a fixed OS byte, and members sorted, owned by 0, at one time", () => {
    expect([...gzip.subarray(0, 10)]).toEqual([0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 255]);
    const names = members.map(({ name }) => name);
    expect(names).toEqual([...names].sort());
    for (const { mode, owner, mtime } of members) {
      expect({ mode, owner, mtime }).toEqual({
        mode: "0000644",
        owner: "0000000:0000000::",
        mtime: "03560116604",
      });
    }
  });

  it("holds package.json, its index and every committed resource, byte for byte", () => {
    const resources = readdirSync("fhir/generated").filter((file) => file.endsWith(".json"));
    expect(members.map(({ name }) => name).sort()).toEqual(
      [
        "package/.index.json",
        "package/package.json",
        ...resources.map((file) => `package/${file}`),
      ].sort(),
    );
    for (const file of resources) {
      expect(members.find(({ name }) => name === `package/${file}`)?.data).toBe(
        readFileSync(`fhir/generated/${file}`, "utf8"),
      );
    }
    const manifest = JSON.parse(
      members.find(({ name }) => name === "package/package.json")?.data ?? "{}",
    ) as Record<string, unknown>;
    expect(manifest).toMatchObject({
      name: "dev.khs.fhir.epi",
      fhirVersions: ["5.0.0"],
      dependencies: { "hl7.fhir.r5.core": "5.0.0" },
    });
    const index = JSON.parse(
      members.find(({ name }) => name === "package/.index.json")?.data ?? "{}",
    ) as { files: { filename: string }[] };
    expect(index.files.map(({ filename }) => filename).sort()).toEqual([...resources].sort());
  });
});
