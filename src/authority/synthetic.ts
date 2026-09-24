import type { ImportRequest } from "../contracts/index.js";
import type { EmaMapping, SectionRule } from "../fhir/mapping.js";
import {
  DEFAULT_SYNTHETIC_PRODUCT_ID,
  syntheticProduct,
  syntheticSectionDiv,
  type SyntheticProduct,
} from "../fixtures/synthetic-products.js";
import {
  EMA_DOCUMENT_IDENTIFIER_SYSTEM,
  EMA_DOCUMENT_TYPE_SYSTEM,
  EMA_EPI_ID_SYSTEM,
  EMA_EXTENSION_BASE,
  EMA_PROCEDURE_SYSTEM,
  EMA_SECTION_SYSTEM,
  EMA_SMPC_TYPE_CODE,
  SPOR_ORGANISATIONS,
} from "./shape.js";

// The synthetic authority's publication (docs/design/authority-import-contract.md, D7): a
// document and List in the EMA's live form, built deterministically from the demo's synthetic
// narratives, with ids in the reserved block and every identifying value synthetic. The gate
// "fetches" it from here, and only where the deployment accepts synthetic content.

export const SYNTHETIC_DOCUMENT_ID = "00000000-5979-4e74-8000-000000000001";
export const SYNTHETIC_INDEX_ID = "00000000-5979-4e74-8000-000000000002";
const SYNTHETIC_DOCUMENT_IDENTIFIER = "00000000-5979-4e74-8000-000000000003";
const TIMESTAMP = "2026-09-24T12:00:00.000000+00:00";
const DOCUMENT_TYPE_DISPLAY = "Summary of Product Characteristics (English)";

export const SYNTHETIC_REQUEST: ImportRequest = {
  authority: "synthetic",
  documentId: SYNTHETIC_DOCUMENT_ID,
  indexId: SYNTHETIC_INDEX_ID,
  language: "en",
};

type Section = {
  id: string;
  title: string;
  code: { coding: { system: string; code: string; display: string }[] };
  text: { status: string; div: string };
  section?: Section[];
};

function section(rule: SectionRule, product: SyntheticProduct): Section {
  const children = (rule.children ?? []).map((child) => section(child, product));
  return {
    id: rule.sourceKey,
    title: rule.title,
    code: { coding: [{ system: EMA_SECTION_SYSTEM, code: rule.targetCode, display: rule.title }] },
    text: { status: "generated", div: syntheticSectionDiv(product, rule.sourceKey, 1) },
    ...(children.length === 0 ? {} : { section: children }),
  };
}

function bytes(value: unknown): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(value, null, 2));
}

export function syntheticPublication(mapping: EmaMapping): {
  request: ImportRequest;
  document: Uint8Array;
  index: Uint8Array;
} {
  const product = syntheticProduct(DEFAULT_SYNTHETIC_PRODUCT_ID);
  const document = {
    resourceType: "Bundle",
    id: SYNTHETIC_DOCUMENT_ID,
    identifier: { system: EMA_DOCUMENT_IDENTIFIER_SYSTEM, value: SYNTHETIC_DOCUMENT_IDENTIFIER },
    type: "document",
    timestamp: TIMESTAMP,
    entry: [
      {
        fullUrl: "http://ema.europa.eu/fhir",
        resource: {
          resourceType: 0,
          language: 0,
          status: 0,
          type: {
            coding: [
              {
                system: EMA_DOCUMENT_TYPE_SYSTEM,
                code: EMA_SMPC_TYPE_CODE,
                display: DOCUMENT_TYPE_DISPLAY,
              },
            ],
          },
          date: "2026-09-24",
          title: product.productName,
          section: [section(mapping.root, product)],
        },
      },
    ],
  };
  const index = {
    resourceType: "List",
    id: SYNTHETIC_INDEX_ID,
    meta: { versionId: "1" },
    identifier: [{ system: EMA_EPI_ID_SYSTEM, value: "SYNTHETIC-EPI-1" }],
    status: "current",
    mode: "working",
    title: product.productName,
    code: { coding: [{ display: "epi Master List" }] },
    subject: {
      display: "electronic Product Information (ePI) extension",
      extension: [
        {
          url: `${EMA_EXTENSION_BASE}procedureNumber`,
          valueIdentifier: { system: EMA_PROCEDURE_SYSTEM, value: "SYNTHETIC-PROCEDURE-1" },
        },
        {
          url: `${EMA_EXTENSION_BASE}marketingAuthorisationHolder`,
          valueCoding: {
            system: SPOR_ORGANISATIONS,
            code: "SYNTHETIC-ORG-1",
            display: "Synthetic Pharma B.V.",
          },
        },
        {
          url: `${EMA_EXTENSION_BASE}regulatoryAgency`,
          valueCoding: {
            system: SPOR_ORGANISATIONS,
            code: "SYNTHETIC-AUTHORITY",
            display: "Synthetic Medicines Agency",
          },
        },
        { url: `${EMA_EXTENSION_BASE}versionNumber`, valueString: "1" },
      ],
    },
    entry: [
      {
        item: { reference: `Bundle/${SYNTHETIC_DOCUMENT_ID}`, display: DOCUMENT_TYPE_DISPLAY },
      },
    ],
  };
  return { request: SYNTHETIC_REQUEST, document: bytes(document), index: bytes(index) };
}
