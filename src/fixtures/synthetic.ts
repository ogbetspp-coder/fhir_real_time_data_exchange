import type { EmaMapping, SectionRule } from "../fhir/mapping.js";
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
): CompositionSection {
  const narrative = syntheticSectionDiv(product, rule.sourceKey, version);
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
    ...(rule.children === undefined
      ? {}
      : { section: rule.children.map((child) => sourceSection(child, mapping, product, version)) }),
  };
}

function entry(resource: FhirResource): { fullUrl: string; resource: FhirResource } {
  return {
    fullUrl: `https://khs.dev/fhir/${resource.resourceType}/${resource.id ?? "missing"}`,
    resource,
  };
}

export function createSyntheticType2Bundle(
  mapping: EmaMapping,
  options: SyntheticFixtureOptions = {},
): FhirBundle {
  const product = syntheticProduct(options.product ?? DEFAULT_SYNTHETIC_PRODUCT_ID);
  const version = options.version ?? 1;
  const productUrl = `https://khs.dev/fhir/MedicinalProductDefinition/${product.id}`;
  const organizationUrl = `https://khs.dev/fhir/Organization/${product.organizationId}`;

  const composition: FhirComposition = {
    resourceType: "Composition",
    id: product.compositionId,
    meta: {
      profile: [
        "http://hl7.org/fhir/uv/emedicinal-product-info/StructureDefinition/Composition-uv-epi",
      ],
    },
    language: "en",
    identifier: [
      {
        system: "https://khs.dev/fhir/identifier/composition",
        value: product.compositionIdentifier,
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
    date: SYNTHETIC_DOCUMENT_DATE,
    author: [
      {
        reference: organizationUrl,
      },
    ],
    title: product.documentTitle,
    section: [sourceSection(mapping.root, mapping, product, version)],
  };

  const resources: FhirResource[] = [
    composition,
    {
      resourceType: "Organization",
      id: product.organizationId,
      name: product.organizationName,
    },
    {
      resourceType: "MedicinalProductDefinition",
      id: product.id,
      identifier: [
        {
          system: "https://khs.dev/fhir/identifier/product",
          value: product.productIdentifier,
        },
      ],
      type: { coding: [{ code: "MedicinalProduct" }] },
      domain: { coding: [{ code: "Human" }] },
      status: { coding: [{ code: "active" }] },
      name: [{ productName: product.productName, type: { coding: [] } }],
    },
    {
      resourceType: "RegulatedAuthorization",
      id: product.authorizationId,
      identifier: [
        {
          system: "https://khs.dev/fhir/identifier/authorization",
          value: product.marketingAuthorizationNumber,
        },
      ],
      subject: [
        {
          reference: productUrl,
        },
      ],
      holder: {
        reference: organizationUrl,
      },
      status: { coding: [{ code: "active" }] },
    },
    {
      resourceType: "PackagedProductDefinition",
      id: product.packageId,
      packageFor: [
        {
          reference: productUrl,
        },
      ],
      packaging: {
        type: { coding: [{ display: "Carton" }] },
        quantity: 1,
      },
    },
    {
      resourceType: "ManufacturedItemDefinition",
      id: product.itemId,
      status: "active",
      manufacturedDoseForm: { coding: [{ display: "Tablet" }] },
      unitOfPresentation: { coding: [{ display: "Tablet" }] },
    },
    {
      resourceType: "AdministrableProductDefinition",
      id: product.administrableId,
      status: "active",
      formOf: [
        {
          reference: productUrl,
        },
      ],
      administrableDoseForm: { coding: [{ display: "Tablet" }] },
      routeOfAdministration: [
        {
          code: { coding: [{ display: "Oral use" }] },
        },
      ],
    },
    {
      resourceType: "Ingredient",
      id: product.ingredientId,
      status: "active",
      for: [
        {
          reference: `https://khs.dev/fhir/ManufacturedItemDefinition/${product.itemId}`,
        },
      ],
      role: { coding: [{ display: "Active" }] },
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
      identifier: [
        {
          system: "https://khs.dev/fhir/identifier/substance",
          value: product.substanceIdentifier,
        },
      ],
      version: "1",
      status: { coding: [{ code: "active" }] },
      name: [{ name: product.substanceName, status: { coding: [{ code: "current" }] } }],
    },
  ];

  return {
    resourceType: "Bundle",
    id: product.bundleId,
    meta: {
      profile: ["http://hl7.org/fhir/uv/emedicinal-product-info/StructureDefinition/Bundle-uv-epi"],
    },
    // Version-independent on purpose: transform.ts derives the EMA document Bundle id from this
    // value, so version 2 must carry the identifier version 1 carried or the store would hold
    // two documents instead of two versions of one.
    identifier: {
      system: "https://khs.dev/fhir/identifier/type2-document",
      value: product.bundleIdentifier,
    },
    type: "document",
    timestamp: SYNTHETIC_DOCUMENT_DATE,
    entry: resources.map(entry),
  };
}

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
    value: "synthetic-type2-smpc-v1",
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
