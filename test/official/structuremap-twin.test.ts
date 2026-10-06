import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  MAP_COMPILED,
  MAP_URL,
  compileMap,
  pinnedValidator,
  serialise,
} from "../../scripts/fhir/compile-map.mjs";
import { importPublication } from "../../src/authority/import.js";
import { EMA_SECTION_SYSTEM } from "../../src/authority/shape.js";
import { syntheticPublication } from "../../src/authority/synthetic.js";
import { loadEmaMapping, type EmaMapping, type SectionRule } from "../../src/fhir/mapping.js";
import { TransformationError, transformType2ToEma } from "../../src/fhir/transform.js";
import type { CompositionSection, FhirBundle, FhirComposition } from "../../src/fhir/types.js";
import { createSyntheticType2Bundle } from "../../src/fixtures/synthetic.js";
import {
  SYNTHETIC_PRODUCT_IDS,
  type SyntheticVersion,
} from "../../src/fixtures/synthetic-products.js";
import { canonicalJson } from "../../src/lib/hash.js";
import { RUN, mutated, sections as publicationSections } from "../authority/support.js";

// The StructureMap twin (fhir/maps/type2-to-ema-cap-smpc-en.map) against the crosswalk
// (src/fhir/transform.ts). Both run on every case below; the twin on the pinned validator's own
// transform engine (fhir/maps/TwinRunner.java, one JVM for every case), loading the sidecar's five
// packages, our own among them with the compiled map and the ConceptMap it translates through.
//
// - fixtures: the crosswalk's output and the twin's must be the same document Bundle, compared as
//   canonical JSON (sorted keys) once each has had removed what the map leaves to the
//   implementation (FML has no SHA-256 and no generic walk): the Bundle's id and identifier value,
//   every entry's fullUrl and resource id, the Composition's identifier values, every section's id
//   and every Reference.reference;
// - refusals: sources the crosswalk refuses and the map refuses too, by a check clause;
// - the EMA's pinned ePIs (labels/ema-epi/sources): the crosswalk refuses each one (style
//   attributes its fidelity scanner forbids, among others), so there is no output to compare;
//   the twin must then refuse it too or carry every section's narrative byte for byte under the
//   mapped code, the crosswalk's own invariant. Their sections with no EMA code are left out of
//   the source, since the canonical record has no place for them.
//
// A failure names the case and the JSON pointers that differ, never a value.

type Json = Record<string, unknown>;
type Case = { name: string; source: unknown };

const NARRATIVE_ROOT = "smpc";

let mapping: EmaMapping;
let work: string;
const twin = new Map<string, { output?: Json; refused?: string }>();
const fixtures: Case[] = [];
const refusals: Case[] = [];
const labels: (Case & { file: string })[] = [];

function composition(bundle: FhirBundle): FhirComposition {
  return bundle.entry[0]?.resource as FhirComposition;
}

function walk(sections: CompositionSection[], visit: (section: CompositionSection) => void): void {
  for (const section of sections) {
    visit(section);
    walk(section.section ?? [], visit);
  }
}

function sectionWithKey(bundle: FhirBundle, key: string): CompositionSection {
  let found: CompositionSection | undefined;
  walk(composition(bundle).section, (section) => {
    if (section.code.coding?.some((coding) => coding.code === key)) found = section;
  });
  if (found === undefined) throw new Error(`no section ${key}`);
  return found;
}

// What the map leaves to the implementation, removed from a document Bundle.
function masked(bundle: unknown): Json {
  const copy = structuredClone(bundle) as Json;
  delete copy.id;
  delete (copy.identifier as Json | undefined)?.value;
  const entries = (copy.entry as Json[] | undefined) ?? [];
  entries.forEach((entry, position) => {
    delete entry.fullUrl;
    const resource = entry.resource as Json | undefined;
    if (resource === undefined) return;
    delete resource.id;
    if (position === 0) {
      for (const identifier of (resource.identifier as Json[] | undefined) ?? []) {
        delete identifier.value;
      }
      walk((resource.section as CompositionSection[] | undefined) ?? [], (section) => {
        delete (section as Json).id;
      });
    }
  });
  const references = (node: unknown): void => {
    if (Array.isArray(node)) node.forEach(references);
    else if (node !== null && typeof node === "object") {
      const object = node as Json;
      if (typeof object.reference === "string") delete object.reference;
      Object.values(object).forEach(references);
    }
  };
  references(copy);
  return copy;
}

// The JSON pointers at which two values differ.
function differences(left: unknown, right: unknown, at = ""): string[] {
  if (canonicalJson(left) === canonicalJson(right)) return [];
  if (
    left === null ||
    right === null ||
    typeof left !== "object" ||
    typeof right !== "object" ||
    Array.isArray(left) !== Array.isArray(right)
  ) {
    return [at || "/"];
  }
  const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
  return [...keys]
    .sort()
    .flatMap((key) =>
      differences((left as Json)[key], (right as Json)[key], `${at}/${key.replaceAll("/", "~1")}`),
    );
}

function crosswalk(source: unknown): { output: Json } | { refused: string[] } {
  try {
    return { output: transformType2ToEma(source as FhirBundle, mapping).documentBundle };
  } catch (error) {
    if (error instanceof TransformationError) return { refused: error.issues };
    throw error;
  }
}

// --- the cases -------------------------------------------------------------------------------

function fixture(name: string, source: FhirBundle): void {
  fixtures.push({ name, source });
}

// A fixture whose root section carries `div` as its narrative: the crosswalk does not read a
// section's narrative that has subsections and no narrative flag, so any div reaches the output.
function withRootNarrative(div: string): FhirBundle {
  const source = createSyntheticType2Bundle(mapping);
  sectionWithKey(source, NARRATIVE_ROOT).text = { status: "generated", div };
  return source;
}

function buildCases(): void {
  const versions: SyntheticVersion[] = [1, 2];
  for (const product of SYNTHETIC_PRODUCT_IDS) {
    for (const version of versions) {
      fixture(`${product}-v${version}`, createSyntheticType2Bundle(mapping, { product, version }));
      fixture(
        `${product}-v${version}-every-section`,
        createSyntheticType2Bundle(mapping, { product, version, optional: true }),
      );
    }
  }

  // The headings the QRD template permits without their optional wording, and a heading it does
  // not permit (the crosswalk writes the rule's).
  const short = createSyntheticType2Bundle(mapping);
  sectionWithKey(short, "smpc.6.5").title = "6.5 Nature and contents of container";
  sectionWithKey(short, "smpc.6.6").title = "6.6 Special precautions for disposal";
  sectionWithKey(short, "smpc.4.2.administration").title = "Special populations";
  sectionWithKey(short, "smpc.1").title = "1. Name";
  fixture("headings", short);
  const untitled = createSyntheticType2Bundle(mapping);
  delete (sectionWithKey(untitled, "smpc.6.5") as Json).title;
  fixture("heading-missing", untitled);

  // Composition elements the crosswalk copies or replaces: a QRD template version of the
  // source's own (replaced), another extension (kept), a category, an attester, a note.
  const elements = createSyntheticType2Bundle(mapping);
  Object.assign(composition(elements), {
    extension: [
      {
        url: "http://ema.europa.eu/fhir/StructureDefinition/ext-epi-qrdtemplate-version",
        valueString: "9.0",
      },
      { url: "https://khs.dev/fhir/StructureDefinition/synthetic-note", valueString: "kept" },
    ],
    category: [{ text: "Synthetic" }],
    attester: [{ mode: { text: "official" }, time: "2026-09-01" }],
    note: [{ text: "Synthetic note; not for clinical use." }],
    version: "2",
  });
  fixture("composition-elements", elements);

  // The authority import's Type 1 record, as the gate passes it to the crosswalk, and the same
  // with optional subsections of 4.6.
  const publication = syntheticPublication(mapping);
  const imported = importPublication(publication.request, publication, mapping, RUN);
  fixture("authority-import", imported.submission.bundle as unknown as FhirBundle);
  const subsection = (code: string, title: string): Json => ({
    id: code,
    title,
    code: { coding: [{ system: EMA_SECTION_SYSTEM, code, display: title }] },
    text: {
      status: "generated",
      div: `<div xmlns="http://www.w3.org/1999/xhtml"><p>Synthetic ${title.toLowerCase()} text; not for clinical use.</p></div>`,
    },
  });
  const optional = mutated(mapping, (document) => {
    const [root] = publicationSections(document) as [Json];
    const clinical = (root.section as Json[])[3];
    const fertilityPregnancy = (clinical?.section as Json[] | undefined)?.[5];
    if (fertilityPregnancy === undefined) throw new Error("no section 4.6");
    fertilityPregnancy.section = [
      subsection("200000029812", "Pregnancy"),
      subsection("200000029814", "Fertility"),
    ];
  });
  const importedOptional = importPublication(optional.request, optional, mapping, RUN);
  fixture("authority-import-optional", importedOptional.submission.bundle as unknown as FhirBundle);

  // Narratives: every div the fidelity scanner accepts in its vectors, and every div the
  // importer's T writes in its vectors, each on the root section.
  const fidelity = JSON.parse(readFileSync("test/fixtures/fidelity/vectors.json", "utf8")) as {
    xhtml: { name: string; input: string; expected: unknown }[];
  };
  for (const vector of fidelity.xhtml.filter(({ expected }) => typeof expected === "string")) {
    fixture(`fidelity-${vector.name}`, withRootNarrative(vector.input));
  }
  const authority = JSON.parse(readFileSync("test/fixtures/authority/vectors.json", "utf8")) as {
    transforms: { name: string; output?: string }[];
  };
  for (const vector of authority.transforms) {
    if (typeof vector.output === "string") {
      fixture(`t-${vector.name}`, withRootNarrative(vector.output));
    }
  }

  // Sources both must refuse.
  const refuse = (name: string, change: (source: FhirBundle) => void): void => {
    const source = createSyntheticType2Bundle(mapping);
    change(source);
    refusals.push({ name, source });
  };
  refuse("not-a-document", (source) => {
    (source as Json).type = "collection";
  });
  refuse("first-entry-not-a-composition", (source) => {
    source.entry.reverse();
  });
  refuse("unmapped-slot-key", (source) => {
    sectionWithKey(source, "smpc.4.4").section = [
      {
        title: "Lactic acidosis",
        code: { coding: [{ system: mapping.sourceCodeSystem, code: "smpc.custom.h4" }] },
        text: {
          status: "generated",
          div: '<div xmlns="http://www.w3.org/1999/xhtml"><p>x</p></div>',
        },
      },
    ];
  });
  refuse("unknown-key", (source) => {
    const section = sectionWithKey(source, "smpc.4.3");
    section.code = { coding: [{ system: mapping.sourceCodeSystem, code: "smpc.4.10" }] };
  });
  refuse("two-canonical-codes", (source) => {
    sectionWithKey(source, "smpc.4.3").code.coding?.push({
      system: mapping.sourceCodeSystem,
      code: "smpc.4.4",
    });
  });
  refuse("uncoded-section-with-narrative", (source) => {
    sectionWithKey(source, "smpc.4.4").section = [
      {
        title: "Elderly",
        code: { text: "Elderly" },
        text: {
          status: "generated",
          div: '<div xmlns="http://www.w3.org/1999/xhtml"><p>x</p></div>',
        },
      },
    ];
  });
  refuse("narrative-not-well-formed", (source) => {
    sectionWithKey(source, "smpc.4.3").text = {
      status: "generated",
      div: '<div xmlns="http://www.w3.org/1999/xhtml"><p>GFR < 30 ml/min</p></div>',
    };
  });

  // The EMA's pinned ePIs, their sections recoded to the canonical keys the manifest maps.
  const byCode = new Map<string, string>();
  const index = (rule: SectionRule): void => {
    byCode.set(rule.targetCode, rule.sourceKey);
    (rule.children ?? []).forEach(index);
  };
  index(mapping.root);
  for (const file of readdirSync("labels/ema-epi/sources").sort()) {
    const label = JSON.parse(readFileSync(`labels/ema-epi/sources/${file}`, "utf8")) as Json;
    const [entry] = label.entry as [Json];
    const document = entry.resource as Json;
    const recode = (sections: Json[]): Json[] =>
      sections.flatMap((section) => {
        const code = ((section.code as Json | undefined)?.coding as Json[] | undefined)?.[0]
          ?.code as string | undefined;
        const key = code === undefined ? undefined : byCode.get(code);
        if (key === undefined) return [];
        return [
          {
            ...(section.id === undefined ? {} : { id: section.id }),
            title: section.title,
            code: { coding: [{ system: mapping.sourceCodeSystem, code: key }] },
            ...(section.text === undefined ? {} : { text: section.text }),
            ...(section.section === undefined
              ? {}
              : { section: recode(section.section as Json[]) }),
          },
        ];
      });
    labels.push({
      file,
      name: `label-${file.replace(/\.json$/, "")}`,
      source: {
        resourceType: "Bundle",
        language: "en",
        identifier: { value: `label:${file}` },
        type: "document",
        timestamp: label.timestamp,
        entry: [
          {
            fullUrl: "urn:uuid:00000000-0000-4000-8000-000000000001",
            resource: {
              resourceType: "Composition",
              language: "en",
              status: "final",
              title: document.title,
              section: recode(document.section as Json[]),
            },
          },
        ],
      },
    });
  }
}

// --- one run of the twin over every case -----------------------------------------------------

beforeAll(async () => {
  mapping = await loadEmaMapping();
  buildCases();
  const validator = pinnedValidator();
  work = mkdtempSync(path.join(tmpdir(), "structuremap-twin-"));
  const inputs = path.join(work, "in");
  const outputs = path.join(work, "out");
  for (const directory of [inputs, outputs]) mkdirSync(directory, { recursive: true });
  const all = [...fixtures, ...refusals, ...labels];
  const files = all.map(({ name, source }) => {
    const file = path.join(inputs, `${name}.json`);
    writeFileSync(file, JSON.stringify(source));
    return file;
  });
  const run = spawnSync(
    validator.java,
    [
      ...validator.jvm,
      "-Xmx1536m",
      "-cp",
      validator.jar,
      path.resolve("fhir/maps/TwinRunner.java"),
      validator.version,
      MAP_URL,
      outputs,
      ...validator.packages,
      "--",
      ...files,
    ],
    { encoding: "utf8", maxBuffer: 256 * 1024 * 1024 },
  );
  if (run.error !== undefined) throw run.error;
  if (run.status !== 0) {
    throw new Error(
      `TwinRunner exited ${run.status}: ${run.stderr.split("\n").slice(-20).join("\n")}`,
    );
  }
  for (const { name } of all) {
    const output = path.join(outputs, `${name}.json`);
    const refused = `${output}.refused`;
    const produced = readdirSync(outputs);
    if (produced.includes(`${name}.json`)) {
      twin.set(name, { output: JSON.parse(readFileSync(output, "utf8")) as Json });
    } else if (produced.includes(`${name}.json.refused`)) {
      twin.set(name, { refused: readFileSync(refused, "utf8") });
    }
  }
});

afterAll(() => {
  rmSync(work, { recursive: true, force: true });
});

describe("the StructureMap twin", () => {
  it("is what the pinned validator compiles from the FML, as committed", () => {
    expect(serialise(compileMap())).toBe(readFileSync(MAP_COMPILED, "utf8"));
  });

  it("gives every fixture the crosswalk's document Bundle", () => {
    expect(fixtures.length).toBeGreaterThan(150);
    const failures: string[] = [];
    for (const { name, source } of fixtures) {
      const expected = crosswalk(source);
      const actual = twin.get(name);
      if ("refused" in expected) {
        failures.push(`${name}: the crosswalk refused the fixture: ${expected.refused.join("; ")}`);
      } else if (actual?.output === undefined) {
        failures.push(`${name}: the twin refused: ${actual?.refused ?? "no result"}`);
      } else {
        const differ = differences(masked(expected.output), masked(actual.output));
        if (differ.length > 0) failures.push(`${name}: ${differ.slice(0, 10).join(", ")}`);
      }
    }
    // A difference passes only as the one reviewed below, and a reviewed one that no longer
    // occurs fails too.
    expect(failures).toEqual(KNOWN_DIFFERENCES.map(({ name, outcome }) => `${name}: ${outcome}`));
  });

  it("refuses, by a check clause, what the crosswalk refuses and FML can see", () => {
    const outcomes = refusals.map(({ name, source }) => {
      const expected = crosswalk(source);
      return {
        name,
        crosswalk: "refused" in expected ? "refused" : "output",
        twin: twin.get(name)?.refused === undefined ? "output" : "refused",
      };
    });
    expect(outcomes).toEqual(
      refusals.map(({ name }) => ({ name, crosswalk: "refused", twin: "refused" })),
    );
  });

  it("refuses each pinned EMA ePI the crosswalk refuses, or carries its narrative byte for byte", () => {
    const outcomes = labels.map(({ name, source }) => {
      const expected = crosswalk(source);
      const actual = twin.get(name);
      if (actual?.output === undefined) {
        const rule = /Rule "([^"]+)": Check condition failed/.exec(actual?.refused ?? "")?.[1];
        return { name, crosswalk: "refused" in expected, twin: `refused by ${rule ?? "?"}` };
      }
      // Each source section's narrative and mapped code, in document order, against the twin's.
      const flat = (sections: Json[]): Json[] =>
        sections.flatMap((section) => [
          section,
          ...flat((section.section as Json[] | undefined) ?? []),
        ]);
      const before = flat(
        (((source as Json).entry as [Json])[0].resource as Json).section as Json[],
      );
      const after = flat(((actual.output.entry as [Json])[0].resource as Json).section as Json[]);
      const rules = new Map<string, string>();
      const index = (rule: SectionRule): void => {
        rules.set(rule.sourceKey, rule.targetCode);
        (rule.children ?? []).forEach(index);
      };
      index(mapping.root);
      const same =
        before.length === after.length &&
        before.every((section, position) => {
          const target = after[position];
          if (target === undefined) return false;
          const key = ((section.code as Json).coding as Json[])[0]?.code as string;
          const code = ((target.code as Json).coding as Json[])[0]?.code;
          return (
            canonicalJson(section.text ?? null) === canonicalJson(target.text ?? null) &&
            code === rules.get(key)
          );
        });
      return {
        name,
        crosswalk: "refused" in expected,
        twin: same ? `every narrative and code of ${before.length} sections` : "different",
      };
    });
    expect(outcomes).toEqual(LABEL_OUTCOMES);
  });
});

// Measured with fhir/maps/TwinRunner.java on validator 6.10.4, 2026-10-06. The crosswalk refuses
// every label (its fidelity scanner forbids their style attributes, and Jentadueto's 4.3 is not
// well-formed). The twin carries three labels whole; it refuses Jentadueto (seven divs the
// validator's XHTML parser cannot read, which its element model would otherwise have dropped
// silently) and Nuvaxovid (the root section's div, likewise), by the map's narrative check.
const LABEL_OUTCOMES = [
  {
    name: "label-brukinsa-smpc-en",
    crosswalk: true,
    twin: "every narrative and code of 32 sections",
  },
  {
    name: "label-imatinib-teva-smpc-en",
    crosswalk: true,
    twin: "every narrative and code of 32 sections",
  },
  {
    name: "label-imatinib-teva-tablets-smpc-en",
    crosswalk: true,
    twin: "every narrative and code of 32 sections",
  },
  { name: "label-jentadueto-smpc-en", crosswalk: true, twin: "refused by narrative" },
  { name: "label-nuvaxovid-smpc-en", crosswalk: true, twin: "refused by narrative" },
];

// The one difference, reviewed: a div the crosswalk carries and the twin's engine cannot read.
// The fidelity vector writes ASCII whitespace inside its tags ("<p\t>", "<br\t/>", "</p >"), which
// XML allows and the fidelity scanner reads; the validator's XHTML parser does not, and its element
// model then drops the div with no error, so the map's narrative check refuses the source rather
// than publish it without its narrative.
const KNOWN_DIFFERENCES = [
  {
    name: "fidelity-accepts-ascii-whitespace-in-tags",
    outcome:
      'the twin refused: org.hl7.fhir.exceptions.FHIRException: Rule "narrative": Check condition failed',
  },
];
