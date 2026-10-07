import { beforeAll, describe, expect, it } from "vitest";

import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import { validateEmaPreflight } from "../../src/fhir/preflight.js";
import { transformType2ToEma } from "../../src/fhir/transform.js";
import type { CompositionSection, FhirBundle, FhirComposition } from "../../src/fhir/types.js";
import { createSyntheticType2Bundle } from "../../src/fixtures/synthetic.js";

// A certified Word source's titles are its label's heading lines, carried as written: the crosswalk
// does not put the template's title in their place, and the EMA preflight does not hold them to it
// (ADR 0006 decision 4; docs/design/certified-word-import.md, D6). Every other source keeps the
// template's rule.

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

function sectionsOf(bundle: FhirBundle): CompositionSection[] {
  const out: CompositionSection[] = [];
  const walk = (list: CompositionSection[] | undefined): void => {
    for (const section of list ?? []) {
      out.push(section);
      walk(section.section);
    }
  };
  walk((bundle.entry[0]?.resource as FhirComposition).section);
  return out;
}

// The synthetic graph with section 4.1 titled `title`.
function titled(title: string | undefined): FhirBundle {
  const bundle = createSyntheticType2Bundle(mapping);
  const section = sectionsOf(bundle).find(({ code }) => code.coding?.[0]?.code === "smpc.4.1") as
    { title?: string } | undefined;
  if (section === undefined) throw new Error("no 4.1");
  if (title === undefined) delete section.title;
  else section.title = title;
  return bundle;
}

const titles = (bundle: FhirBundle): (string | undefined)[] =>
  sectionsOf(bundle).map(({ title }) => title);

describe("a section's title", () => {
  it("is the template's where the source's is not permitted, unless carried as written", () => {
    const source = titled("4.1 Indications");
    const template = transformType2ToEma(source, mapping);
    const written = transformType2ToEma(source, mapping, undefined, "as-written");
    expect(titles(template.documentBundle)).toContain("4.1 Therapeutic indications");
    expect(titles(template.documentBundle)).not.toContain("4.1 Indications");
    expect(titles(written.documentBundle)).toContain("4.1 Indications");
    // The EMA preflight holds the template's rule, and only it, to the template's titles.
    const severities = (outcome: { issue: { severity: string }[] }): string[] =>
      outcome.issue.map(({ severity }) => severity);
    expect(
      severities(validateEmaPreflight(written.list, written.documentBundle, mapping)),
    ).toContain("error");
    expect(
      severities(validateEmaPreflight(written.list, written.documentBundle, mapping, "as-written")),
    ).toEqual(["success"]);
  });

  it("must be there to be carried as written", () => {
    expect(() => transformType2ToEma(titled(undefined), mapping, undefined, "as-written")).toThrow(
      /EMA QRD transformation failed closed/,
    );
    const { list, documentBundle } = transformType2ToEma(titled("4.1 Indications"), mapping);
    const section = sectionsOf(documentBundle).find(
      ({ title }) => title === "4.1 Therapeutic indications",
    ) as { title?: string };
    section.title = "";
    const outcome = validateEmaPreflight(list, documentBundle, mapping, "as-written");
    expect(outcome.issue.map(({ diagnostics }) => diagnostics)).toContain(
      "A section carries its title",
    );
  });
});
