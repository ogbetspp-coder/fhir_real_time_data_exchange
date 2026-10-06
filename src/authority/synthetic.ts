import type { ImportRequest } from "../contracts/index.js";
import type { EmaMapping, SectionRule } from "../fhir/mapping.js";
import {
  DEFAULT_SYNTHETIC_PRODUCT_ID,
  syntheticProduct,
  syntheticSectionDiv,
  type SyntheticProduct,
} from "../fixtures/synthetic-products.js";
import { SYNTHETIC_ID_BLOCK } from "./import.js";
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

export const SYNTHETIC_DOCUMENT_ID = `${SYNTHETIC_ID_BLOCK}000000000001`;
export const SYNTHETIC_INDEX_ID = `${SYNTHETIC_ID_BLOCK}000000000002`;
const TIMESTAMP = "2026-09-24T12:00:00.000000+00:00";
const DOCUMENT_TYPE_DISPLAY = "Summary of Product Characteristics (English)";

type Section = {
  id: string;
  title: string;
  code: { coding: { system: string; code: string; display: string }[] };
  text: { status: string; div: string };
  section?: Section[];
};

// The mandatory sections only, as the publication had before the optional ones were mapped
// (mapping 1.4.0).
function section(rule: SectionRule, product: SyntheticProduct): Section {
  const children = (rule.children ?? [])
    .filter((child) => child.required)
    .map((child) => section(child, product));
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

type Publication = { request: ImportRequest; document: Uint8Array; index: Uint8Array };

// What identifies a publication: the authority's ids and the List's identifying values.
type Identity = {
  authority: ImportRequest["authority"];
  documentId: string;
  indexId: string;
  documentIdentifier: string;
  epiId: string;
  procedureNumber: string;
  holderCode: string;
  agency: { code: string; display: string };
};

function publication(mapping: EmaMapping, identity: Identity): Publication {
  const product = syntheticProduct(DEFAULT_SYNTHETIC_PRODUCT_ID);
  const document = {
    resourceType: "Bundle",
    id: identity.documentId,
    identifier: { system: EMA_DOCUMENT_IDENTIFIER_SYSTEM, value: identity.documentIdentifier },
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
    id: identity.indexId,
    meta: { versionId: "1" },
    identifier: [{ system: EMA_EPI_ID_SYSTEM, value: identity.epiId }],
    status: "current",
    mode: "working",
    title: product.productName,
    code: { coding: [{ display: "epi Master List" }] },
    subject: {
      display: "electronic Product Information (ePI) extension",
      extension: [
        {
          url: `${EMA_EXTENSION_BASE}procedureNumber`,
          valueIdentifier: { system: EMA_PROCEDURE_SYSTEM, value: identity.procedureNumber },
        },
        {
          url: `${EMA_EXTENSION_BASE}marketingAuthorisationHolder`,
          valueCoding: {
            system: SPOR_ORGANISATIONS,
            code: identity.holderCode,
            display: "Synthetic Pharma B.V.",
          },
        },
        {
          url: `${EMA_EXTENSION_BASE}regulatoryAgency`,
          valueCoding: { system: SPOR_ORGANISATIONS, ...identity.agency },
        },
        { url: `${EMA_EXTENSION_BASE}versionNumber`, valueString: "1" },
      ],
    },
    entry: [
      {
        item: { reference: `Bundle/${identity.documentId}`, display: DOCUMENT_TYPE_DISPLAY },
      },
    ],
  };
  return {
    request: {
      authority: identity.authority,
      documentId: identity.documentId,
      indexId: identity.indexId,
      language: "en",
    },
    document: bytes(document),
    index: bytes(index),
  };
}

export function syntheticPublication(mapping: EmaMapping): Publication {
  return publication(mapping, {
    authority: "synthetic",
    documentId: SYNTHETIC_DOCUMENT_ID,
    indexId: SYNTHETIC_INDEX_ID,
    documentIdentifier: `${SYNTHETIC_ID_BLOCK}000000000003`,
    epiId: "SYNTHETIC-EPI-1",
    procedureNumber: "SYNTHETIC-PROCEDURE-1",
    holderCode: "SYNTHETIC-ORG-1",
    agency: { code: "SYNTHETIC-AUTHORITY", display: "Synthetic Medicines Agency" },
  });
}

// The same publication as the EMA would serve it, its narratives still the synthetic ones: ids
// outside the reserved block and identifying values in the EMA's form, none of them a product's
// (the agency's is the EMA's own). It passes every stage but the last, `rendering`, which refuses
// it for want of the renderer gate's evidence (the importer's vectors and tests; no fetcher
// serves it).
export const EMA_SHAPED_DOCUMENT_ID = "7f3c2a10-5e4b-4c8d-9a61-2b0e8d4f1c35";
export const EMA_SHAPED_INDEX_ID = "4b9e6d21-8a3f-4e57-b0c2-91d7a5e3f608";

export function emaShapedPublication(mapping: EmaMapping): Publication {
  return publication(mapping, {
    authority: "EMA",
    documentId: EMA_SHAPED_DOCUMENT_ID,
    indexId: EMA_SHAPED_INDEX_ID,
    documentIdentifier: "c1d5e8f2-3a47-4b69-8e0d-6f2a9b7c4e13",
    epiId: "EPI/99/9999",
    procedureNumber: "EMEA/H/C/999999",
    holderCode: "ORG-199999999",
    agency: { code: "ORG-100013412", display: "European Medicines Agency" },
  });
}
