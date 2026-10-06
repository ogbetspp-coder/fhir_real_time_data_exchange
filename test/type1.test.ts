import { beforeAll, describe, expect, it } from "vitest";

import { loadEmaMapping, type EmaMapping } from "../src/fhir/mapping.js";
import {
  hasValidationErrors,
  validateCanonicalPreflight,
  validateEmaPreflight,
} from "../src/fhir/preflight.js";
import { PROCEDURE_NUMBER_SYSTEM, transformType2ToEma } from "../src/fhir/transform.js";
import type { CompositionSection, FhirBundle, FhirResource } from "../src/fhir/types.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";

// The Type 1 record an authority import makes, its preflight, and what the crosswalk carries of
// its product identity into the EMA List (docs/design/authority-import-contract.md, D4, D9, D11).

const SPOR = "https://spor.ema.europa.eu/v1/organisations/";
let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

function resource(bundle: FhirBundle, type: string): FhirResource {
  const found = bundle.entry.find(({ resource }) => resource.resourceType === type)?.resource;
  if (found === undefined) throw new Error(`fixture has a ${type}`);
  return found;
}

// The synthetic Type 2 graph cut down to a Type 1 record with the EMA's identity systems.
function type1(): FhirBundle {
  const bundle = createSyntheticType2Bundle(mapping);
  const keep = new Set([
    "Composition",
    "MedicinalProductDefinition",
    "Organization",
    "RegulatedAuthorization",
  ]);
  bundle.entry = bundle.entry.filter(({ resource }) => keep.has(resource.resourceType));
  resource(bundle, "Organization").identifier = [{ system: SPOR, value: "SYNTHETIC-ORG-1" }];
  const authorisation = resource(bundle, "RegulatedAuthorization");
  authorisation.identifier = [{ system: PROCEDURE_NUMBER_SYSTEM, value: "SYNTHETIC-PROC-1" }];
  authorisation.case = {
    identifier: { system: PROCEDURE_NUMBER_SYSTEM, value: "SYNTHETIC-PROC-1" },
  };
  authorisation.regulator = {
    identifier: { system: SPOR, value: "SYNTHETIC-AUTHORITY" },
    display: "Synthetic Agency",
  };
  return bundle;
}

function refused(bundle: FhirBundle): boolean {
  return hasValidationErrors(validateCanonicalPreflight(bundle, "type1"));
}

describe("the Type 1 preflight", () => {
  it("accepts the four-resource record and refuses the Type 2 graph as Type 1", () => {
    expect(refused(type1())).toBe(false);
    expect(refused(createSyntheticType2Bundle(mapping))).toBe(true);
    expect(
      hasValidationErrors(validateCanonicalPreflight(createSyntheticType2Bundle(mapping), "type2")),
    ).toBe(false);
  });

  it("refuses a pack, a second holder, a broken link, a nameless product, a missing identifier", () => {
    const withPack = type1();
    const pack = createSyntheticType2Bundle(mapping).entry.find(
      ({ resource }) => resource.resourceType === "PackagedProductDefinition",
    );
    if (pack === undefined) throw new Error("fixture has a pack");
    withPack.entry.push(pack);
    expect(refused(withPack)).toBe(true);

    const twoHolders = type1();
    const holder = twoHolders.entry.find(
      ({ resource }) => resource.resourceType === "Organization",
    );
    if (holder === undefined) throw new Error("fixture has a holder");
    twoHolders.entry.push({ ...structuredClone(holder), fullUrl: `${holder.fullUrl}-2` });
    expect(refused(twoHolders)).toBe(true);

    const twoSubjects = type1();
    const composition = resource(twoSubjects, "Composition");
    composition.subject = [
      ...(composition.subject as unknown[]),
      ...(composition.subject as unknown[]),
    ];
    expect(refused(twoSubjects)).toBe(true);

    const unlinked = type1();
    resource(unlinked, "RegulatedAuthorization").holder = { reference: "urn:uuid:elsewhere" };
    expect(refused(unlinked)).toBe(true);

    const nameless = type1();
    resource(nameless, "MedicinalProductDefinition").name = [];
    expect(refused(nameless)).toBe(true);

    const anonymous = type1();
    resource(anonymous, "Organization").identifier = [];
    expect(refused(anonymous)).toBe(true);

    const missing = type1();
    missing.entry = missing.entry.filter(
      ({ resource }) => resource.resourceType !== "RegulatedAuthorization",
    );
    expect(refused(missing)).toBe(true);

    const notDocument: FhirBundle = { ...type1(), type: "collection" };
    expect(refused(notDocument)).toBe(true);
  });
});

describe("the EMA List's product identity", () => {
  it("carries the holder, agency and procedure a Type 1 record states, titled by the product", () => {
    const { list } = transformType2ToEma(type1(), mapping);
    const extensions = list.extension as { url: string }[];

    expect(extensions.map(({ url }) => url.split("/").at(-1))).toEqual([
      "ext-epi-marketing-authorisation-holder",
      "ext-epi-marketing-authorisation-holder-display",
      "ext-epi-regulatory-agency",
      "ext-epi-regulatory-agency-display",
      "ext-epi-procedure-number",
    ]);
    expect(extensions[4]).toEqual({
      url: "http://ema.europa.eu/fhir/StructureDefinition/ext-epi-procedure-number",
      valueIdentifier: { system: PROCEDURE_NUMBER_SYSTEM, value: "SYNTHETIC-PROC-1" },
    });
    expect(list.title).toBe("Synthetic Paracetamol 500 mg tablets");
  });

  it("writes nothing the synthetic Type 2 graph does not state in those systems", () => {
    const { list } = transformType2ToEma(createSyntheticType2Bundle(mapping), mapping);

    expect(list.extension).toBeUndefined();
    expect(list.title).toBe("Synthetic Paracetamol 500 mg tablets");
  });

  it("refuses a product with two names, and two authorisations that state different identities", () => {
    const named = type1();
    resource(named, "MedicinalProductDefinition").name = [
      { productName: "Synthetic A" },
      { productName: "Synthetic B" },
    ];
    expect(() => transformType2ToEma(named, mapping)).toThrow(/ambiguous/);

    const twice = type1();
    const authorisation = twice.entry.find(
      ({ resource: candidate }) => candidate.resourceType === "RegulatedAuthorization",
    );
    if (authorisation === undefined) throw new Error("fixture has an authorisation");
    // One RegulatedAuthorization per authorisation (docs/design/version-identity.md): two that
    // state the same holder, regulator and procedure are one identity for the List.
    twice.entry.push({ ...structuredClone(authorisation), fullUrl: `${authorisation.fullUrl}-2` });
    expect(refused(twice)).toBe(false);
    expect(transformType2ToEma(twice, mapping).list.extension).toEqual(
      transformType2ToEma(type1(), mapping).list.extension,
    );
    const second = twice.entry.at(-1)?.resource;
    if (second === undefined) throw new Error("fixture has a second authorisation");
    second.case = { identifier: { system: PROCEDURE_NUMBER_SYSTEM, value: "SYNTHETIC-PROC-2" } };
    expect(() => transformType2ToEma(twice, mapping)).toThrow(/ambiguous/);
  });

  it("refuses a graph with two products, and accepts a single Reference as a link", () => {
    const two = type1();
    const product = two.entry.find(
      ({ resource: candidate }) => candidate.resourceType === "MedicinalProductDefinition",
    );
    if (product === undefined) throw new Error("fixture has a product");
    two.entry.push({ ...structuredClone(product), fullUrl: `${product.fullUrl}-2` });
    expect(transformType2ToEma(two, mapping).list.title).not.toBe(
      "Synthetic Paracetamol 500 mg tablets",
    );

    const single = type1();
    const composition = resource(single, "Composition");
    composition.subject = (composition.subject as unknown[])[0];
    expect(refused(single)).toBe(false);
  });

  it("refuses a holder with two identifiers in the SPOR system", () => {
    const bundle = type1();
    resource(bundle, "Organization").identifier = [
      { system: SPOR, value: "SYNTHETIC-ORG-1" },
      { system: SPOR, value: "SYNTHETIC-ORG-2" },
    ];

    expect(() => transformType2ToEma(bundle, mapping)).toThrow(/ambiguous/);
  });
});

describe("headings the QRD template permits", () => {
  function section(bundle: FhirBundle, key: string): CompositionSection {
    const find = (sections: CompositionSection[]): CompositionSection | undefined => {
      for (const candidate of sections) {
        if (candidate.code.coding?.some(({ code }) => code === key)) return candidate;
        const inner = find(candidate.section ?? []);
        if (inner !== undefined) return inner;
      }
      return undefined;
    };
    const found = find(
      (bundle.entry[0]?.resource as unknown as { section: CompositionSection[] }).section,
    );
    if (found === undefined) throw new Error(`fixture has ${key}`);
    return found;
  }

  it("carries a heading without its optional wording, and the EMA preflight accepts it", () => {
    const source = createSyntheticType2Bundle(mapping);
    section(source, "smpc.6.5").title = "6.5 Nature and contents of container";
    const ema = transformType2ToEma(source, mapping);
    const emaSection = section(ema.documentBundle, "200000029841");

    expect(emaSection.title).toBe("6.5 Nature and contents of container");
    expect(hasValidationErrors(validateEmaPreflight(ema.list, ema.documentBundle, mapping))).toBe(
      false,
    );
  });

  it("replaces a heading the template does not permit with the manifest's", () => {
    const source = createSyntheticType2Bundle(mapping);
    section(source, "smpc.6.5").title = "6.5 Something else";

    expect(section(transformType2ToEma(source, mapping).documentBundle, "200000029841").title).toBe(
      "6.5 Nature and contents of container and special equipment for use, administration or implantation",
    );
  });
});
