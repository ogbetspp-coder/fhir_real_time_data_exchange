import type { EmaMapping, SectionRule } from "../fhir/mapping.js";
import { GLOBAL_EPI_PROFILE_BASE } from "../fhir/standards.js";
import type {
  CompositionSection,
  FhirBundle,
  FhirComposition,
  FhirResource,
} from "../fhir/types.js";
import { isComposition } from "../fhir/types.js";
import {
  DEFAULT_SYNTHETIC_PRODUCT_ID,
  SYNTHETIC_DOCUMENT_DATE,
  syntheticCompositionDate,
  syntheticCompositionIdentifier,
  syntheticProduct,
  syntheticSectionDiv,
  type SyntheticFixtureOptions,
  type SyntheticProduct,
  type SyntheticVersion,
} from "./synthetic-products.js";

function sourceSection(
  rule: SectionRule,
  mapping: EmaMapping,
  product: SyntheticProduct,
  version: SyntheticVersion,
  options: SyntheticFixtureOptions,
): CompositionSection {
  const narrative = syntheticSectionDiv(product, rule.sourceKey, version);
  const children = (rule.children ?? [])
    .filter((child) => child.required || options.optional === true)
    .map((child) => sourceSection(child, mapping, product, version, options));
  return {
    id: rule.sourceKey.replaceAll(".", "-"),
    title: rule.title,
    code: {
      coding: [
        {
          system: mapping.sourceCodeSystem,
          code: rule.sourceKey,
          display: rule.title,
        },
      ],
    },
    text: { status: "generated", div: narrative },
    ...(children.length === 0 ? {} : { section: children }),
  };
}

function entry(resource: FhirResource): { fullUrl: string; resource: FhirResource } {
  return {
    fullUrl: `https://khs.dev/fhir/${resource.resourceType}/${resource.id ?? "missing"}`,
    resource,
  };
}

// Every product-graph resource declares the Global ePI profile the Bundle-uv-epi entry slice
// expects of it. The validator resolves a Reference to an entry and matches the target against
// the referring element's allowed profiles; an entry that does not declare one is "Unable to
// find a profile match".
function globalEpiProfile(resourceType: string): { profile: string[] } {
  return { profile: [`${GLOBAL_EPI_PROFILE_BASE}${resourceType}-uv-epi`] };
}

function syntheticIdentifier(kind: string, value: string): { system: string; value: string }[] {
  return [{ system: `https://khs.dev/fhir/identifier/${kind}`, value }];
}

// A product-graph coding in the system its element is bound to: the Global ePI profiles' own
// value sets for dose forms, unit of presentation, route and ingredient role, base R5's for the
// rest. Every system but SNOMED CT is a code system the pinned hl7.fhir.r5.core#5.0.0 holds with
// content `complete`, so the official validator checks the code and its display offline, as
// errors. SNOMED CT it cannot check offline, and says nothing about
// (docs/design/terminology-server.md); the route's code and display are the Global ePI package's
// own example's (package/example/Bundle-bundlepackageleaflet75type2.json).
const R5 = "http://hl7.org/fhir";
function coded(system: string, code: string, display: string) {
  return { coding: [{ system, code, display }] };
}
const active = () => coded(`${R5}/publication-status`, "active", "Active");

export function createSyntheticType2Bundle(
  mapping: EmaMapping,
  options: SyntheticFixtureOptions = {},
): FhirBundle {
  const product = syntheticProduct(options.product ?? DEFAULT_SYNTHETIC_PRODUCT_ID);
  const version = options.version ?? 1;
  const productUrl = `https://khs.dev/fhir/MedicinalProductDefinition/${product.id}`;
  const organizationUrl = `https://khs.dev/fhir/Organization/${product.organizationId}`;
  const itemUrl = `https://khs.dev/fhir/ManufacturedItemDefinition/${product.itemId}`;

  const composition: FhirComposition = {
    resourceType: "Composition",
    id: product.compositionId,
    meta: globalEpiProfile("Composition"),
    language: "en",
    identifier: [
      {
        system: "https://khs.dev/fhir/identifier/composition",
        value: syntheticCompositionIdentifier(product, version),
      },
    ],
    status: "final",
    type: {
      coding: [
        {
          system: "https://khs.dev/fhir/CodeSystem/document-type",
          code: "smpc",
          display: "Summary of Product Characteristics",
        },
      ],
    },
    subject: [
      {
        reference: productUrl,
      },
    ],
    date: syntheticCompositionDate(version),
    author: [
      {
        reference: organizationUrl,
      },
    ],
    title: product.documentTitle,
    section: [sourceSection(mapping.root, mapping, product, version, options)],
  };

  // The graph must be connected the way the Global ePI Bundle profile expects: the validator
  // walks references forward and backward from the Composition and rejects any entry it cannot
  // reach. Composition.subject reaches the product; authorization, package and administrable
  // product point back at it; the package contains the manufactured item and the administrable
  // product is produced from it; the ingredient is for the item and names the substance.
  const resources: FhirResource[] = [
    composition,
    {
      resourceType: "Organization",
      id: product.organizationId,
      meta: globalEpiProfile("Organization"),
      identifier: syntheticIdentifier("organization", product.organizationIdentifier),
      name: product.organizationName,
    },
    {
      resourceType: "MedicinalProductDefinition",
      id: product.id,
      meta: globalEpiProfile("MedicinalProductDefinition"),
      identifier: syntheticIdentifier("product", product.productIdentifier),
      type: coded(`${R5}/medicinal-product-type`, "MedicinalProduct", "Medicinal Product"),
      domain: coded(`${R5}/medicinal-product-domain`, "Human", "Human use"),
      status: active(),
      name: [{ productName: product.productName }],
    },
    {
      resourceType: "RegulatedAuthorization",
      id: product.authorizationId,
      meta: globalEpiProfile("RegulatedAuthorization"),
      identifier: syntheticIdentifier("authorization", product.marketingAuthorizationNumber),
      subject: [
        {
          reference: productUrl,
        },
      ],
      holder: {
        reference: organizationUrl,
      },
      status: active(),
    },
    {
      resourceType: "PackagedProductDefinition",
      id: product.packageId,
      meta: globalEpiProfile("PackagedProductDefinition"),
      identifier: syntheticIdentifier("package", `${product.productIdentifier}-PKG`),
      name: `${product.productName} carton`,
      packageFor: [
        {
          reference: productUrl,
        },
      ],
      packaging: {
        identifier: syntheticIdentifier("packaging", `${product.productIdentifier}-PKG-1`),
        // packaging-type has no "Carton"; it has "Box".
        type: coded(`${R5}/packaging-type`, "100000073498", "Box"),
        quantity: 1,
        containedItem: [
          {
            item: {
              reference: {
                reference: itemUrl,
              },
            },
          },
        ],
      },
    },
    {
      resourceType: "ManufacturedItemDefinition",
      id: product.itemId,
      meta: globalEpiProfile("ManufacturedItemDefinition"),
      identifier: syntheticIdentifier("manufactured-item", `${product.productIdentifier}-ITEM`),
      status: "active",
      manufacturedDoseForm: coded(`${R5}/manufactured-dose-form`, "100000073664", "Tablet"),
      unitOfPresentation: coded(`${R5}/unit-of-presentation`, "200000002152", "Tablet"),
    },
    {
      resourceType: "AdministrableProductDefinition",
      id: product.administrableId,
      meta: globalEpiProfile("AdministrableProductDefinition"),
      identifier: syntheticIdentifier("administrable-product", `${product.productIdentifier}-ADM`),
      status: "active",
      formOf: [
        {
          reference: productUrl,
        },
      ],
      administrableDoseForm: coded(`${R5}/administrable-dose-form`, "100000073664", "Tablet"),
      producedFrom: [
        {
          reference: itemUrl,
        },
      ],
      routeOfAdministration: [
        {
          code: coded("http://snomed.info/sct", "26643006", "Oral route"),
        },
      ],
    },
    {
      resourceType: "Ingredient",
      id: product.ingredientId,
      meta: globalEpiProfile("Ingredient"),
      status: "active",
      for: [
        {
          reference: itemUrl,
        },
      ],
      role: coded(`${R5}/ingredient-role`, "100000072072", "Active"),
      substance: {
        code: {
          reference: {
            reference: `https://khs.dev/fhir/SubstanceDefinition/${product.substanceId}`,
          },
        },
        strength: [
          {
            presentationRatio: {
              numerator: {
                value: product.strengthMg,
                unit: "mg",
                system: "http://unitsofmeasure.org",
                code: "mg",
              },
              denominator: { value: 1, unit: "tablet" },
            },
          },
        ],
      },
    },
    {
      resourceType: "SubstanceDefinition",
      id: product.substanceId,
      meta: globalEpiProfile("SubstanceDefinition"),
      identifier: syntheticIdentifier("substance", product.substanceIdentifier),
      version: "1",
      status: active(),
      name: [{ name: product.substanceName, status: active() }],
    },
  ];

  return {
    resourceType: "Bundle",
    id: product.bundleId,
    meta: globalEpiProfile("Bundle"),
    // Bundle-uv-epi makes language mandatory (1..1); the Composition already says "en".
    language: "en",
    // Version-independent on purpose, as Bundle-uv-epi defines it: transform.ts derives the EMA
    // document Bundle id from this value, so version 2 must carry the identifier version 1
    // carried or the store would hold two documents instead of two versions of one.
    identifier: {
      system: "https://khs.dev/fhir/identifier/type2-document",
      value: product.bundleIdentifier,
    },
    type: "document",
    timestamp: SYNTHETIC_DOCUMENT_DATE,
    entry: resources.map(entry),
  };
}

// The identifier of the document scripts/gcp/bootstrap.sh seeds into the source store for the
// ungated `healthcare-api` run source. Its own, never a demonstration label's: the transform keys
// the EMA document Bundle id on it, and it used to be `synthetic-type2-smpc-v1`, the paracetamol
// label's, so running the seeded source wrote an ungated version, with no Provenance, over the
// label the demonstration quotes, which was then served with the previous version's approval.
export const SEEDED_SOURCE_IDENTIFIER = "synthetic-seeded-type2-v1";

export function createSyntheticSmpcFromPublishedType2(
  publishedType2: FhirBundle,
  mapping: EmaMapping,
): FhirBundle {
  const result = structuredClone(publishedType2);
  const publishedEntry = result.entry[0];
  const syntheticComposition = createSyntheticType2Bundle(mapping).entry[0]?.resource;
  if (
    publishedEntry === undefined ||
    syntheticComposition === undefined ||
    !isComposition(publishedEntry.resource) ||
    !isComposition(syntheticComposition)
  ) {
    throw new Error("Published Type 2 fixture must have Composition as its first entry");
  }
  const publishedComposition = publishedEntry.resource;

  result.id = "synthetic-type2-smpc";
  result.identifier = {
    system: "https://khs.dev/fhir/identifier/type2-document",
    value: SEEDED_SOURCE_IDENTIFIER,
  };
  result.timestamp = "2026-09-19T00:00:00Z";
  result.entry[0] = {
    ...publishedEntry,
    resource: {
      ...publishedComposition,
      language: "en",
      identifier: syntheticComposition.identifier,
      status: syntheticComposition.status,
      type: syntheticComposition.type,
      date: syntheticComposition.date,
      title: "Synthetic DrugX SmPC interoperability demonstration",
      section: syntheticComposition.section,
    },
  };

  const referenceMap = new Map(
    result.entry
      .filter(({ resource }) => resource.id !== undefined)
      .map(({ fullUrl, resource }) => [`${resource.resourceType}/${resource.id ?? ""}`, fullUrl]),
  );

  function normalizeExample(value: unknown, key?: string): unknown {
    if (Array.isArray(value)) return value.map((item) => normalizeExample(item));
    if (value !== null && typeof value === "object") {
      const record = value as Record<string, unknown>;
      return Object.fromEntries(
        Object.entries(record).map(([childKey, child]) => [
          childKey,
          normalizeExample(child, childKey),
        ]),
      );
    }
    if (key === "reference" && typeof value === "string") {
      return referenceMap.get(value) ?? value;
    }
    if (key === "system" && typeof value === "string" && value.startsWith("http://example.org")) {
      return value.replace("http://example.org", "https://khs.dev/fhir/example");
    }
    return value;
  }

  result.entry = result.entry.map((bundleEntry) => ({
    ...bundleEntry,
    resource: normalizeExample(
      Object.fromEntries(Object.entries(bundleEntry.resource).filter(([key]) => key !== "text")),
    ) as FhirResource,
  }));
  return result;
}
