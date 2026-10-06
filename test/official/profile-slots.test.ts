import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

import { beforeAll, describe, expect, it } from "vitest";

import { pinnedValidator } from "../../scripts/fhir/compile-map.mjs";
import { loadEmaMapping, type EmaMapping, type SectionRule } from "../../src/fhir/mapping.js";

// Every section slot of the EMA's CAP SmPC template profile is either mapped by a rule of the
// manifest (and so by the ConceptMap, equivalent) or named in its unmapped list (noMap, with a
// reason). The slots are read from the profile's own StructureDefinition in the pinned EUePI
// package, the one the validator sidecar loads, checked against its SHA-256 here; nothing is
// restated from memory. Run in CI's Official validation job (npm run test:official), where the
// pinned packages are fetched and checked.

const PROFILE = "StructureDefinition-EUQRD-CAP-template-new-SmPC-en.json";

type ElementDefinition = {
  id: string;
  sliceName?: string;
  min?: number;
  max?: string;
  short?: string;
  patternCode?: string;
};

type Slot = {
  id: string;
  parent: string | undefined;
  code: string;
  min: number;
  max: string;
  title: string | undefined;
  custom: boolean;
};

// The sidecar's -ig package whose bytes are the EUePI#1.0.0 pin of fhir/standards.lock.json
// (pinnedValidator has checked each against Dockerfile.validator).
function euepiPackage(): string {
  const lock = JSON.parse(readFileSync("fhir/standards.lock.json", "utf8")) as {
    artifacts: { package?: string; sha256: string }[];
  };
  const pinned = lock.artifacts.find((artifact) => artifact.package === "EUePI#1.0.0")?.sha256;
  const file = pinnedValidator().packages.find(
    (each) => createHash("sha256").update(readFileSync(each)).digest("hex") === pinned,
  );
  if (pinned === undefined || file === undefined) {
    throw new Error("the sidecar loads no package with the EUePI#1.0.0 pin");
  }
  return file;
}

function slots(profile: { differential: { element: ElementDefinition[] } }): Slot[] {
  const byId = new Map(profile.differential.element.map((element) => [element.id, element]));
  return profile.differential.element
    .filter((element) => element.sliceName !== undefined)
    .map((element) => {
      const code = byId.get(`${element.id}.code.coding.code`)?.patternCode;
      if (code === undefined) throw new Error(`${element.id} fixes no code`);
      const cut = element.id.lastIndexOf(".section:");
      return {
        id: element.id,
        parent: cut === -1 ? undefined : element.id.slice(0, cut),
        code,
        min: element.min ?? 0,
        max: element.max ?? "*",
        title: byId.get(`${element.id}.title`)?.short,
        custom: element.sliceName?.endsWith("-Blanksubsections") ?? false,
      };
    });
}

function flatten(
  rule: SectionRule,
  parent?: SectionRule,
): [SectionRule, SectionRule | undefined][] {
  return [[rule, parent], ...(rule.children ?? []).flatMap((child) => flatten(child, rule))];
}

let mapping: EmaMapping;
let all: Slot[];

beforeAll(async () => {
  mapping = await loadEmaMapping();
  const read = spawnSync("tar", ["-xzOf", euepiPackage(), `package/${PROFILE}`], {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });
  if (read.status !== 0) throw new Error(`could not read ${PROFILE} from the EUePI package`);
  const profile = JSON.parse(read.stdout) as {
    url: string;
    differential: { element: ElementDefinition[] };
  };
  expect(profile.url).toBe(mapping.profiles.composition.at(-1));
  all = slots(profile);
});

describe("the EMA's CAP SmPC template profile, read from the pinned EUePI package", () => {
  it("has 103 section slots: 59 of the template's own sections and 44 for custom subsections", () => {
    expect(all).toHaveLength(103);
    expect(all.filter((slot) => !slot.custom)).toHaveLength(59);
    expect(all.filter((slot) => slot.custom)).toHaveLength(44);
  });

  it("has no slot that is neither mapped nor explicitly unmapped", () => {
    const mapped = new Set(flatten(mapping.root).map(([rule]) => rule.targetCode));
    const unmapped = new Set((mapping.unmapped ?? []).map((slot) => slot.targetCode));
    expect(all.filter((slot) => !mapped.has(slot.code) && !unmapped.has(slot.code))).toEqual([]);
    // A custom subsection slot is unmapped, never mapped; every other slot is mapped.
    expect(all.filter((slot) => slot.custom && !unmapped.has(slot.code))).toEqual([]);
    expect(all.filter((slot) => !slot.custom && !mapped.has(slot.code))).toEqual([]);
  });

  it("is the manifest's tree: each section a rule, under its parent's rule, in order, as required", () => {
    const named = all.filter((slot) => !slot.custom);
    const rules = flatten(mapping.root);
    expect(rules).toHaveLength(named.length);
    const byId = new Map(named.map((slot) => [slot.id, slot]));
    for (const slot of named) {
      const found = rules.find(([rule]) => rule.targetCode === slot.code);
      expect(found, slot.id).toBeDefined();
      const [rule, parent] = found ?? [];
      expect([slot.id, parent?.targetCode]).toEqual([
        slot.id,
        slot.parent === undefined ? undefined : byId.get(slot.parent)?.code,
      ]);
      expect([slot.id, rule?.required, rule?.title]).toEqual([slot.id, slot.min === 1, slot.title]);
      expect(slot.max).toBe("1");
      const children = named.filter((child) => child.parent === slot.id).map(({ code }) => code);
      expect((rule?.children ?? []).map(({ targetCode }) => targetCode)).toEqual(children);
    }
  });

  it("names every unmapped code in a custom subsection slot", () => {
    const custom = new Set(all.filter((slot) => slot.custom).map((slot) => slot.code));
    expect((mapping.unmapped ?? []).map((slot) => slot.targetCode).sort()).toEqual(
      [...custom].sort(),
    );
    for (const slot of all.filter((each) => each.custom)) {
      expect([slot.id, slot.min, slot.max]).toEqual([slot.id, 0, "*"]);
    }
  });
});
