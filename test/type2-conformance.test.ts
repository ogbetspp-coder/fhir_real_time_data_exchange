import { readFile } from "node:fs/promises";

import { beforeAll, describe, expect, it } from "vitest";

import { collectNarrativeSections } from "../src/fidelity/index.js";
import type { EmaMapping, SectionRule } from "../src/fhir/mapping.js";
import { loadEmaMapping } from "../src/fhir/mapping.js";
import { transformType2ToEma } from "../src/fhir/transform.js";
import {
  isComposition,
  type CompositionSection,
  type FhirBundle,
  type FhirComposition,
  type FhirResource,
} from "../src/fhir/types.js";
import {
  SYNTHETIC_PRODUCT_IDS,
  SYNTHETIC_VERSIONS,
  type SyntheticProductId,
  type SyntheticVersion,
} from "../src/fixtures/synthetic-products.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";

// The official HL7 validator, run with the pinned Global ePI and EMA ePI packages, rejected the
// synthetic Type 2 Bundle and its EMA transform on 2026-09-20
// (docs/validation/changes/2026-09-20-mapping-qrd-displays-and-ema-list-code.md). Each block
// below pins one class of that rejection in-process so `npm run check` catches it before a
// deploy does. The validator itself is not run here: the packages are not vendored (AGENTS.md),
// so what can be pinned is what the packages say — copied into
// test/fixtures/terminology/ema-displays.json — and the structural rules the validator applied.

// Every QRD narrative div of every product and version, captured from the tree as it was
// before the conformance change. The fixture is the "before"; the assertions are the "after".
const NARRATIVE_FIXTURE = "test/fixtures/narrative/section-divs.json";
const TERMINOLOGY_FIXTURE = "test/fixtures/terminology/ema-displays.json";

const GLOBAL_EPI_PROFILE_BASE =
  "http://hl7.org/fhir/uv/emedicinal-product-info/StructureDefinition/";

type NarrativeFixture = Record<string, Record<string, string>>;

type TerminologyFixture = {
  package: string;
  packageSha256: string;
  qrdSections: { system: string; display: Record<string, string> };
  documentTypes: { system: string; display: Record<string, string> };
};

let mapping: EmaMapping;
let narrativeBefore: NarrativeFixture;
let terminology: TerminologyFixture;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  narrativeBefore = JSON.parse(await readFile(NARRATIVE_FIXTURE, "utf8")) as NarrativeFixture;
  terminology = JSON.parse(await readFile(TERMINOLOGY_FIXTURE, "utf8")) as TerminologyFixture;
});

function composition(bundle: FhirBundle): FhirComposition {
  const first = bundle.entry[0]?.resource;
  if (first === undefined || !isComposition(first)) {
    throw new Error("Bundle must have Composition as its first entry");
  }
  return first;
}

function flattenRules(rule: SectionRule): SectionRule[] {
  return [rule, ...(rule.children ?? []).flatMap(flattenRules)];
}

function flattenSections(sections: CompositionSection[]): CompositionSection[] {
  return sections.flatMap((section) => [section, ...flattenSections(section.section ?? [])]);
}

function everyProductVersion(): { product: SyntheticProductId; version: SyntheticVersion }[] {
  return SYNTHETIC_PRODUCT_IDS.flatMap((product) =>
    SYNTHETIC_VERSIONS.map((version) => ({ product, version })),
  );
}

// Every string under a key named `reference`, anywhere in the resource: the validator's own
// reachability walk follows every Reference and CodeableReference the same way.
function references(value: unknown, into: string[] = []): string[] {
  if (Array.isArray(value)) {
    for (const item of value) references(item, into);
  } else if (value !== null && typeof value === "object") {
    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
      if (key === "reference" && typeof child === "string") into.push(child);
      else references(child, into);
    }
  }
  return into;
}

function emptyArrays(value: unknown, path: string, into: string[] = []): string[] {
  if (Array.isArray(value)) {
    if (value.length === 0) into.push(path);
    value.forEach((item, index) => emptyArrays(item, `${path}[${String(index)}]`, into));
  } else if (value !== null && typeof value === "object") {
    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
      emptyArrays(child, `${path}.${key}`, into);
    }
  }
  return into;
}

function hasIdentifier(resource: FhirResource, path: string[] = ["identifier"]): boolean {
  let cursor: unknown = resource;
  for (const key of path) {
    if (cursor === null || typeof cursor !== "object") return false;
    cursor = (cursor as Record<string, unknown>)[key];
  }
  if (!Array.isArray(cursor) || cursor.length === 0) return false;
  return cursor.every(
    (identifier) =>
      typeof (identifier as { system?: unknown }).system === "string" &&
      typeof (identifier as { value?: unknown }).value === "string",
  );
}

describe("QRD narrative is untouched by the conformance change", () => {
  it("carries byte-identical section divs, source and EMA target, for every product and version", () => {
    const seen: string[] = [];
    for (const { product, version } of everyProductVersion()) {
      const key = `${product}@v${String(version)}`;
      const before = narrativeBefore[key];
      expect([key, before !== undefined]).toEqual([key, true]);
      if (before === undefined) continue;
      seen.push(key);

      const source = createSyntheticType2Bundle(mapping, { product, version });
      const target = transformType2ToEma(source, mapping).documentBundle;
      const sourceDivs = new Map(
        collectNarrativeSections(composition(source), mapping.sourceCodeSystem).map(
          ({ sourceKey, div }) => [sourceKey, div],
        ),
      );
      const targetDivs = collectNarrativeSections(
        composition(target),
        mapping.targetCodeSystem,
      ).map(({ div }) => div);

      // The same keys, no more and no fewer, and every div equal to the pinned one.
      expect([key, [...sourceDivs.keys()].sort()]).toEqual([key, Object.keys(before).sort()]);
      for (const [sourceKey, div] of sourceDivs) {
        expect([key, sourceKey, div]).toEqual([key, sourceKey, before[sourceKey]]);
      }
      // The target carries exactly the same divs, in the same order, under the EMA codes.
      expect([key, targetDivs]).toEqual([key, [...sourceDivs.values()]]);
    }
    expect(seen).toHaveLength(SYNTHETIC_PRODUCT_IDS.length * SYNTHETIC_VERSIONS.length);
    expect(Object.keys(narrativeBefore).sort()).toEqual(seen.sort());
  });
});

describe("EMA terminology on the transformed document", () => {
  it("is pinned against the EUePI package fhir/standards.lock.json names", async () => {
    const lock = JSON.parse(await readFile("fhir/standards.lock.json", "utf8")) as {
      artifacts: { package?: string; sha256: string }[];
    };
    const pinned = lock.artifacts.find((artifact) => artifact.package === terminology.package);
    expect(pinned?.sha256).toBe(terminology.packageSha256);
  });

  it("gives every QRD section coding the code system's own display string", () => {
    // The 59 sections of the EMA profile EUQRD-CAP-template-new-SmPC-en (mapping 1.4.0).
    const rules = flattenRules(mapping.root);
    expect(rules).toHaveLength(59);
    expect(mapping.targetCodeSystem).toBe(terminology.qrdSections.system);

    // The manifest: every rule's effective display is the package's string for its code.
    for (const rule of rules) {
      const expected = terminology.qrdSections.display[rule.targetCode];
      expect([rule.targetCode, rule.display ?? rule.title]).toEqual([rule.targetCode, expected]);
    }

    // The output, of a source with every optional section too: every target section carries
    // exactly that coding.
    const target = transformType2ToEma(
      createSyntheticType2Bundle(mapping, { optional: true }),
      mapping,
    );
    const sections = flattenSections(composition(target.documentBundle).section);
    expect(sections).toHaveLength(59);
    for (const section of sections) {
      const coding = section.code.coding?.[0];
      expect(coding?.system).toBe(terminology.qrdSections.system);
      expect([coding?.code, coding?.display]).toEqual([
        coding?.code,
        terminology.qrdSections.display[coding?.code ?? ""],
      ]);
    }
  });

  it("keeps the section heading the label carries separate from the coding display", () => {
    // The EMA EPI-23-1022 sample titles 6.6 "Special precautions for disposal" while its coding
    // display is the code system's bracketed string. The heading is authored content and is
    // not what the validator binds; only the coding display is.
    const target = transformType2ToEma(createSyntheticType2Bundle(mapping), mapping);
    const sections = flattenSections(composition(target.documentBundle).section);
    const byCode = new Map(sections.map((section) => [section.code.coding?.[0]?.code, section]));
    expect(byCode.get("200000029841")?.title).toBe(
      "6.5 Nature and contents of container and special equipment for use, administration or implantation",
    );
    expect(byCode.get("200000029842")?.title).toBe(
      "6.6 Special precautions for disposal and other handling",
    );
    expect(
      sections.filter((section) => section.title !== section.code.coding?.[0]?.display),
    ).toHaveLength(2);
  });

  it("codes the EMA List with a code that exists in the document-type code system", () => {
    const target = transformType2ToEma(createSyntheticType2Bundle(mapping), mapping);
    const coding = (
      target.list as { code?: { coding?: { system?: string; code?: string; display?: string }[] } }
    ).code?.coding?.[0];
    expect(coding?.system).toBe(terminology.documentTypes.system);
    expect(coding?.code).toBe("100000155539");
    expect(coding?.display).toBe(terminology.documentTypes.display["100000155539"]);
    expect(coding?.display).toBe("Combined File of all Documents");
  });
});

describe("synthetic Type 2 product graph against the Global ePI Bundle profile", () => {
  it("declares Bundle.language and the Global ePI profile on every entry", () => {
    for (const { product, version } of everyProductVersion()) {
      const bundle = createSyntheticType2Bundle(mapping, { product, version });
      expect(bundle.language).toBe("en");
      expect(bundle.meta?.profile).toEqual([`${GLOBAL_EPI_PROFILE_BASE}Bundle-uv-epi`]);
      for (const { resource } of bundle.entry) {
        expect([resource.resourceType, resource.meta?.profile]).toEqual([
          resource.resourceType,
          [`${GLOBAL_EPI_PROFILE_BASE}${resource.resourceType}-uv-epi`],
        ]);
      }
    }
  });

  it("carries every identifier and name the profiles make mandatory", () => {
    const bundle = createSyntheticType2Bundle(mapping);
    const byType = new Map(bundle.entry.map(({ resource }) => [resource.resourceType, resource]));
    for (const type of [
      "Organization",
      "MedicinalProductDefinition",
      "RegulatedAuthorization",
      "PackagedProductDefinition",
      "ManufacturedItemDefinition",
      "AdministrableProductDefinition",
      "SubstanceDefinition",
    ]) {
      const resource = byType.get(type);
      expect([type, resource !== undefined && hasIdentifier(resource)]).toEqual([type, true]);
    }
    const packaged = byType.get("PackagedProductDefinition");
    expect(typeof packaged?.name).toBe("string");
    expect(packaged !== undefined && hasIdentifier(packaged, ["packaging", "identifier"])).toBe(
      true,
    );
  });

  it("has no empty array anywhere, in the source or in the EMA output", () => {
    const source = createSyntheticType2Bundle(mapping);
    const target = transformType2ToEma(source, mapping);
    expect(emptyArrays(source, "Bundle")).toEqual([]);
    expect(emptyArrays(target.documentBundle, "Bundle")).toEqual([]);
    expect(emptyArrays(target.list, "List")).toEqual([]);
  });

  it("reaches every entry from the Composition by following references either way", () => {
    for (const { product, version } of everyProductVersion()) {
      for (const bundle of [
        createSyntheticType2Bundle(mapping, { product, version }),
        transformType2ToEma(createSyntheticType2Bundle(mapping, { product, version }), mapping)
          .documentBundle,
      ]) {
        const fullUrls = bundle.entry.map(({ fullUrl }) => fullUrl);
        const adjacency = new Map<string, Set<string>>(fullUrls.map((url) => [url, new Set()]));
        for (const { fullUrl, resource } of bundle.entry) {
          for (const reference of references(resource)) {
            if (!adjacency.has(reference)) {
              throw new Error(`${resource.resourceType} references ${reference}, not in Bundle`);
            }
            adjacency.get(fullUrl)?.add(reference);
            adjacency.get(reference)?.add(fullUrl);
          }
        }
        const start = fullUrls[0];
        if (start === undefined) throw new Error("empty Bundle");
        const reached = new Set<string>([start]);
        const queue = [start];
        for (let next = queue.shift(); next !== undefined; next = queue.shift()) {
          for (const neighbour of adjacency.get(next) ?? []) {
            if (!reached.has(neighbour)) {
              reached.add(neighbour);
              queue.push(neighbour);
            }
          }
        }
        const unreachable = fullUrls.filter((url) => !reached.has(url));
        expect([product, version, bundle.id, unreachable]).toEqual([
          product,
          version,
          bundle.id,
          [],
        ]);
      }
    }
  });
});

// Every Coding-shaped object, wherever it sits (a coding array, meta.tag, an extension's
// valueCoding, a Quantity's unit): any object with a string system, code or display, but a
// Reference (a string `reference`) and an Identifier (a string `value`).
type FoundCoding = { path: string; system: unknown; code: unknown };
function codings(value: unknown, path = "", into: FoundCoding[] = []): FoundCoding[] {
  if (Array.isArray(value)) {
    value.forEach((item, index) => codings(item, `${path}[${String(index)}]`, into));
  } else if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    const shaped = ["system", "code", "display"].some((key) => typeof record[key] === "string");
    if (shaped && typeof record.reference !== "string" && typeof record.value !== "string") {
      into.push({ path, system: record.system, code: record.code });
    }
    for (const [key, child] of Object.entries(record)) codings(child, `${path}.${key}`, into);
  }
  return into;
}

// R5's pattern for a code (hl7.fhir.r5.core#5.0.0, StructureDefinition code): not empty, and no
// leading, trailing or doubled whitespace.
const R5_CODE = /^[^\s]+( [^\s]+)*$/;

function codingProblem({ system, code }: FoundCoding): string | undefined {
  if (typeof system !== "string" || !/^https?:\/\//.test(system)) return "no system";
  if (typeof code !== "string" || !R5_CODE.test(code)) return "no code";
  return undefined;
}

// The official validator runs offline (-tx n/a). It checks a code, and its display, as an error
// only in a code system a pinned package holds with content `complete`; it warns that it cannot
// check UCUM, and says nothing at all about SNOMED CT (both measured 2026-10-06,
// docs/design/terminology-server.md). A new system must be put in one list or the other.
const CHECKED_OFFLINE = [
  "http://hl7.org/fhir/administrable-dose-form",
  "http://hl7.org/fhir/ingredient-role",
  "http://hl7.org/fhir/manufactured-dose-form",
  "http://hl7.org/fhir/medicinal-product-domain",
  "http://hl7.org/fhir/medicinal-product-type",
  "http://hl7.org/fhir/packaging-type",
  "http://hl7.org/fhir/publication-status",
  "http://hl7.org/fhir/unit-of-presentation",
  "https://khs.dev/fhir/CodeSystem/canonical-smpc-sections",
  "https://khs.dev/fhir/CodeSystem/document-type",
];
const UNCHECKED_OFFLINE = ["http://snomed.info/sct", "http://unitsofmeasure.org"];

describe("the synthetic sources' terminology", () => {
  it("names a system and a code for every coding, and only systems the gate has classified", () => {
    const systems = new Set<string>();
    for (const { product, version } of everyProductVersion()) {
      const found = codings(createSyntheticType2Bundle(mapping, { product, version }));
      expect(found.filter((coding) => codingProblem(coding) !== undefined)).toEqual([]);
      for (const { system } of found) systems.add(String(system));
    }
    expect([...systems].sort()).toEqual([...CHECKED_OFFLINE, ...UNCHECKED_OFFLINE].sort());
  });

  it("finds a coding wherever it sits, and refuses one without a system or a code", () => {
    const resource = {
      resourceType: "Basic",
      meta: { tag: [{ display: "y" }] },
      extension: [
        {
          url: "https://khs.dev/fhir/StructureDefinition/example",
          valueCoding: { system: "http://hl7.org/fhir/publication-status", code: "" },
        },
      ],
      identifier: [{ system: "https://khs.dev/fhir/identifier/example", value: "1" }],
      subject: { reference: "Basic/1", display: "not a coding" },
      code: { coding: [{ system: "http://hl7.org/fhir/publication-status", code: "active" }] },
      amount: { value: 1, unit: "mg", system: "http://unitsofmeasure.org", code: "mg" },
      other: { system: "http://hl7.org/fhir/publication-status", code: " active" },
    };
    expect(codings(resource).map((coding) => [coding.path, codingProblem(coding)])).toEqual([
      [".meta.tag[0]", "no system"],
      [".extension[0].valueCoding", "no code"],
      [".code.coding[0]", undefined],
      [".amount", undefined],
      [".other", "no code"],
    ]);
  });
});
