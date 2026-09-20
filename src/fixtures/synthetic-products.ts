// The synthetic product set the demonstration is built from (roadmap item 1c, scenes described
// in docs/design/verifiable-answers.md). Three labels, two versions each, all invented: every
// sentence says so in its own words, and every identifier is prefixed so it can never be read as
// a real authorisation.
//
// Two rules constrain everything below.
//
// 1. The default product at version 1 is frozen. `test/fixtures/contracts/*.json` are its bytes,
//    `npm run contracts:check` fails on drift, and the hashes in the committed evidence were
//    computed over exactly these strings. Nothing that the default product emits may change:
//    not an id, not an identifier, not a space inside a sentence.
// 2. A version is a content difference, not an identity. Version 2 of a label keeps the Type 2
//    Bundle id and the Bundle identifier of version 1, because `src/fhir/transform.ts` derives
//    the EMA document Bundle id from `Bundle.identifier.value` — so both versions transform to
//    the same EMA Bundle id and the FHIR store versions one resource instead of creating two.

export type SyntheticProductId =
  "synthetic-paracetamol" | "synthetic-demoxetine" | "synthetic-placebolol";

export type SyntheticVersion = 1 | 2;

// Options accepted by both synthetic fixture builders. Absent means "the default product at
// version 1", which is the frozen fixture.
export type SyntheticFixtureOptions = {
  product?: SyntheticProductId;
  version?: SyntheticVersion;
};

export type SyntheticSubmissionIdentity = {
  submissionId: string;
  extractionRunId: string;
  createdAt: string;
  approvedAt: string;
};

export type SyntheticProduct = {
  id: SyntheticProductId;
  productName: string;
  substanceName: string;
  documentTitle: string;
  strengthMg: number;
  organizationId: string;
  organizationName: string;
  compositionId: string;
  compositionIdentifier: string;
  bundleId: string;
  // Stable document identity, unchanged between versions. The trailing `-v1` is part of the
  // literal the default fixture was frozen with; it names the document, not its content version.
  bundleIdentifier: string;
  productIdentifier: string;
  authorizationId: string;
  marketingAuthorizationNumber: string;
  packageId: string;
  itemId: string;
  administrableId: string;
  ingredientId: string;
  substanceId: string;
  substanceIdentifier: string;
  // Base name of the source document a Zone A extractor would have read. Version 2 is a
  // different file, so it gets its own stem.
  sourceFilenameStem: string;
  // Whether each section sentence names the product. False for the default product, whose
  // narrative bytes are frozen.
  namedInNarrative: boolean;
  // Extra sentences appended to a section's sentence, by QRD source key. Scene 3 of the
  // demonstration needs exactly one product whose 4.3 says "hepatic impairment".
  additionalSentences: Partial<Record<string, string>>;
  submissions: Record<SyntheticVersion, SyntheticSubmissionIdentity>;
};

export const DEFAULT_SYNTHETIC_PRODUCT_ID: SyntheticProductId = "synthetic-paracetamol";

// The one section whose sentence differs between version 1 and version 2 — scene 2, "change one
// word, watch the world know".
export const VERSIONED_SOURCE_KEY = "smpc.4.4";

// The phrase scene 3 asks about. It appears in exactly one product's section 4.3, inside a
// sentence that says it is a demonstration.
export const SCENE_THREE_PHRASE = "hepatic impairment";

const DOCUMENT_DATE = "2026-09-19T00:00:00Z";
const VERSION_ONE_APPROVAL = "2026-09-19T00:00:00Z";
const VERSION_TWO_APPROVAL = "2026-09-20T00:00:00Z";

// The document's own date is part of its content, and version 2 differs from version 1 by one
// sentence and nothing else, so `Bundle.timestamp` and `Composition.date` are shared by both.
// What differs outside the label is the approval: its own submission id, extraction run id, and
// a later approval instant.
export const SYNTHETIC_DOCUMENT_DATE = DOCUMENT_DATE;

const PARACETAMOL: SyntheticProduct = {
  id: "synthetic-paracetamol",
  productName: "Synthetic Paracetamol 500 mg tablets",
  substanceName: "Paracetamol",
  documentTitle: "Synthetic Paracetamol 500 mg tablets SmPC",
  strengthMg: 500,
  organizationId: "synthetic-pharma",
  organizationName: "Synthetic Pharma Ltd",
  compositionId: "synthetic-smpc",
  compositionIdentifier: "synthetic-smpc-v1",
  bundleId: "synthetic-type2-smpc",
  bundleIdentifier: "synthetic-type2-smpc-v1",
  productIdentifier: "SYN-PARA-500",
  authorizationId: "synthetic-authorization",
  marketingAuthorizationNumber: "EU/SYN/0001",
  packageId: "synthetic-package",
  itemId: "synthetic-tablet",
  administrableId: "synthetic-administrable",
  ingredientId: "synthetic-active-ingredient",
  substanceId: "synthetic-paracetamol-substance",
  substanceIdentifier: "SYN-PARACETAMOL",
  sourceFilenameStem: "synthetic-smpc",
  namedInNarrative: false,
  additionalSentences: {},
  submissions: {
    1: {
      submissionId: "4b1d2c3e-5f60-4a71-8b92-0c1d2e3f4a5b",
      extractionRunId: "7c8d9e0f-1a2b-4c3d-9e4f-5a6b7c8d9e0f",
      createdAt: VERSION_ONE_APPROVAL,
      approvedAt: VERSION_ONE_APPROVAL,
    },
    2: {
      submissionId: "8f2e1d0c-3b4a-4c5d-9e6f-1a2b3c4d5e6f",
      extractionRunId: "0a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d",
      createdAt: VERSION_TWO_APPROVAL,
      approvedAt: VERSION_TWO_APPROVAL,
    },
  },
};

const DEMOXETINE: SyntheticProduct = {
  id: "synthetic-demoxetine",
  productName: "Synthetic Demoxetine 10 mg tablets",
  substanceName: "Demoxetine",
  documentTitle: "Synthetic Demoxetine 10 mg tablets SmPC",
  strengthMg: 10,
  organizationId: "synthetic-pharma",
  organizationName: "Synthetic Pharma Ltd",
  compositionId: "synthetic-demoxetine-smpc",
  compositionIdentifier: "synthetic-demoxetine-smpc-v1",
  bundleId: "synthetic-demoxetine-type2-smpc",
  bundleIdentifier: "synthetic-demoxetine-type2-smpc-v1",
  productIdentifier: "SYN-DEMO-010",
  authorizationId: "synthetic-demoxetine-authorization",
  marketingAuthorizationNumber: "EU/SYN/0002",
  packageId: "synthetic-demoxetine-package",
  itemId: "synthetic-demoxetine-tablet",
  administrableId: "synthetic-demoxetine-administrable",
  ingredientId: "synthetic-demoxetine-active-ingredient",
  substanceId: "synthetic-demoxetine-substance",
  substanceIdentifier: "SYN-DEMOXETINE",
  sourceFilenameStem: "synthetic-demoxetine-smpc",
  namedInNarrative: true,
  additionalSentences: {
    // The single sentence scene 3 searches for, and the reason this product is the only answer.
    "smpc.4.3": `This synthetic demonstration sentence names severe ${SCENE_THREE_PHRASE} as a contraindication for a product that does not exist; not for clinical use.`,
  },
  submissions: {
    1: {
      submissionId: "2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f",
      extractionRunId: "3d4e5f6a-7b8c-4d9e-8f0a-1b2c3d4e5f6a",
      createdAt: VERSION_ONE_APPROVAL,
      approvedAt: VERSION_ONE_APPROVAL,
    },
    2: {
      submissionId: "4e5f6a7b-8c9d-4e0f-9a1b-2c3d4e5f6a7b",
      extractionRunId: "5f6a7b8c-9d0e-4f1a-8b2c-3d4e5f6a7b8c",
      createdAt: VERSION_TWO_APPROVAL,
      approvedAt: VERSION_TWO_APPROVAL,
    },
  },
};

const PLACEBOLOL: SyntheticProduct = {
  id: "synthetic-placebolol",
  productName: "Synthetic Placebolol 25 mg tablets",
  substanceName: "Placebolol",
  documentTitle: "Synthetic Placebolol 25 mg tablets SmPC",
  strengthMg: 25,
  organizationId: "synthetic-pharma",
  organizationName: "Synthetic Pharma Ltd",
  compositionId: "synthetic-placebolol-smpc",
  compositionIdentifier: "synthetic-placebolol-smpc-v1",
  bundleId: "synthetic-placebolol-type2-smpc",
  bundleIdentifier: "synthetic-placebolol-type2-smpc-v1",
  productIdentifier: "SYN-PLAC-025",
  authorizationId: "synthetic-placebolol-authorization",
  marketingAuthorizationNumber: "EU/SYN/0003",
  packageId: "synthetic-placebolol-package",
  itemId: "synthetic-placebolol-tablet",
  administrableId: "synthetic-placebolol-administrable",
  ingredientId: "synthetic-placebolol-active-ingredient",
  substanceId: "synthetic-placebolol-substance",
  substanceIdentifier: "SYN-PLACEBOLOL",
  sourceFilenameStem: "synthetic-placebolol-smpc",
  namedInNarrative: true,
  additionalSentences: {
    // Deliberately a different contraindication: scene 3 is only a demonstration if the answer
    // is one product and not all of them.
    "smpc.4.3":
      "This synthetic demonstration sentence names a synthetic excipient allergy as a contraindication for a product that does not exist; not for clinical use.",
  },
  submissions: {
    1: {
      submissionId: "6a7b8c9d-0e1f-4a2b-9c3d-4e5f6a7b8c9d",
      extractionRunId: "7b8c9d0e-1f2a-4b3c-8d4e-5f6a7b8c9d0e",
      createdAt: VERSION_ONE_APPROVAL,
      approvedAt: VERSION_ONE_APPROVAL,
    },
    2: {
      submissionId: "8c9d0e1f-2a3b-4c4d-9e5f-6a7b8c9d0e1f",
      extractionRunId: "9d0e1f2a-3b4c-4d5e-8f6a-7b8c9d0e1f2a",
      createdAt: VERSION_TWO_APPROVAL,
      approvedAt: VERSION_TWO_APPROVAL,
    },
  },
};

// Declaration order is demonstration order: the default product first.
export const SYNTHETIC_PRODUCTS: readonly SyntheticProduct[] = [
  PARACETAMOL,
  DEMOXETINE,
  PLACEBOLOL,
];

export const SYNTHETIC_PRODUCT_IDS: readonly SyntheticProductId[] = SYNTHETIC_PRODUCTS.map(
  ({ id }) => id,
);

export const SYNTHETIC_VERSIONS: readonly SyntheticVersion[] = [1, 2];

export function syntheticProduct(
  id: SyntheticProductId = DEFAULT_SYNTHETIC_PRODUCT_ID,
): SyntheticProduct {
  const found = SYNTHETIC_PRODUCTS.find((product) => product.id === id);
  if (found === undefined) throw new Error(`Unknown synthetic product ${id}`);
  return found;
}

export function isSyntheticProductId(value: string): value is SyntheticProductId {
  return SYNTHETIC_PRODUCT_IDS.some((id) => id === value);
}

function qualifier(product: SyntheticProduct): string {
  return product.namedInNarrative ? ` of ${product.productName}` : "";
}

// The sentence every section carries at version 1. The default product's wording is the frozen
// one; the added products name themselves so a viewer can tell three labels apart on screen.
function sectionSentence(product: SyntheticProduct, sourceKey: string): string {
  return `Synthetic demonstration content for ${sourceKey}${qualifier(product)}; not for clinical use.`;
}

// Version 2 replaces that sentence in section 4.4 — one sentence, in one section, of one
// version. Everything else about the label is identical, which is what makes the change visible
// as exactly one changed hash.
function versionTwoSentence(product: SyntheticProduct): string {
  return `Revised synthetic demonstration content for ${VERSIONED_SOURCE_KEY}${qualifier(product)}; this version 2 sentence replaces the version 1 sentence and is not for clinical use.`;
}

// The narrative text of one section, before it is wrapped in XHTML. The source page text and the
// narrative are built from this same function, so they cannot disagree and the submission passes
// `verifyDocumentSubmission` honestly.
export function syntheticSectionText(
  product: SyntheticProduct,
  sourceKey: string,
  version: SyntheticVersion,
): string {
  const first =
    version === 2 && sourceKey === VERSIONED_SOURCE_KEY
      ? versionTwoSentence(product)
      : sectionSentence(product, sourceKey);
  const additional = product.additionalSentences[sourceKey];
  return additional === undefined ? first : `${first} ${additional}`;
}

export function syntheticSectionDiv(
  product: SyntheticProduct,
  sourceKey: string,
  version: SyntheticVersion,
): string {
  return `<div xmlns="http://www.w3.org/1999/xhtml"><p>${syntheticSectionText(product, sourceKey, version)}</p></div>`;
}

// Base name of the source document behind a given version. Version 2 is a different file.
export function syntheticSourceStem(product: SyntheticProduct, version: SyntheticVersion): string {
  return version === 1 ? product.sourceFilenameStem : `${product.sourceFilenameStem}-v${version}`;
}
