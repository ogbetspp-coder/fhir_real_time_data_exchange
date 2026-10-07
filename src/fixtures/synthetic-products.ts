// The synthetic product set the demonstration is built from (roadmap item 1c, scenes described
// in docs/design/verifiable-answers.md). Three demonstration labels and one product used only by
// the `fixture` run source, two versions each, all invented: every sentence says so in its own
// words, and every identifier is prefixed so it can never be read as a real authorisation.
//
// `DEMONSTRATION_PRODUCT_IDS` and `SMOKE_PRODUCT_ID` below say which is which, and why the
// separation exists.
//
// Two rules constrain everything below.
//
// 1. The default product at version 1 is frozen. `test/fixtures/contracts/*.json` are its bytes,
//    `npm run contracts:check` fails on drift, and the hashes in the committed evidence were
//    computed over exactly these strings. Nothing in its narrative, ids or identifiers may
//    change: not an id, not an identifier, not a space inside a sentence. Its product graph's
//    codings have moved twice, each a recorded change that regenerated those files
//    (docs/validation/changes/2026-09-20-mapping-qrd-displays-and-ema-list-code.md,
//    2026-10-06-product-graph-terminology-and-epi-topic.md).
// 2. A version is a content difference, not an identity. Version 2 of a label keeps the Type 2
//    Bundle id, the Bundle identifier and Bundle.timestamp of version 1, because the Global ePI
//    profiles define both as persisting across versions and `src/fhir/transform.ts` derives the
//    EMA document Bundle id from `Bundle.identifier.value` — so both versions transform to the
//    same EMA Bundle id and the FHIR store versions one resource instead of creating two. What
//    the profiles define per version differs: Composition.identifier and Composition.date
//    (docs/design/version-identity.md).

export type SyntheticProductId =
  | "synthetic-paracetamol"
  | "synthetic-demoxetine"
  | "synthetic-placebolol"
  | "synthetic-smoketest"
  | "synthetic-exampline";

export type SyntheticVersion = 1 | 2;

// Options accepted by both synthetic fixture builders. Absent means "the default product at
// version 1", which is the frozen fixture.
export type SyntheticFixtureOptions = {
  product?: SyntheticProductId;
  version?: SyntheticVersion;
  // Every optional section of the mapping as well (2.1, Pregnancy, 11. DOSIMETRY, ...). Off, the
  // fixture has the mandatory sections only, as every fixture had before the optional ones were
  // mapped (mapping 1.4.0).
  optional?: boolean;
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
  // Organization-uv-epi requires an identifier. The three labels share one invented holder,
  // so they share one invented identifier too.
  organizationIdentifier: string;
  compositionId: string;
  compositionIdentifier: string;
  bundleId: string;
  // Stable document identity, unchanged between versions (Bundle-uv-epi: "remains the same for
  // all versions of this ePI"). The trailing `-v1` is part of the literal the default fixture was
  // frozen with, and every EMA id in the store derives from it; it names the document, not its
  // content version (docs/design/version-identity.md).
  bundleIdentifier: string;
  productIdentifier: string;
  authorizationId: string;
  marketingAuthorizationNumber: string;
  // An EU authorisation number (EU/1/YY/NNN/PPP) in the reserved example block EU/1/24/9999, on
  // the RegulatedAuthorization, with its product number on the MedicinalProductDefinition. Only
  // the leaflet's product has one; the others' frozen graphs have none.
  euAuthorisationNumber?: string;
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

// `Bundle.timestamp` is the ePI's original date, shared by both versions (Bundle-uv-epi: it
// "persists across versions"). `Composition.date` is the date of this version's last revision
// (Composition-uv-epi), so version 2 has its own, the day of its approval. Besides one sentence and
// the Composition's identifier and date, what differs is the approval: its own submission id,
// extraction run id, and a later approval instant.
export const SYNTHETIC_DOCUMENT_DATE = DOCUMENT_DATE;

export function syntheticCompositionDate(version: SyntheticVersion): string {
  return version === 1 ? DOCUMENT_DATE : VERSION_TWO_APPROVAL;
}

// Composition.identifier names one version (Composition-uv-epi: "Each new version of the
// Composition receives a new identifier"). Version 1's is the frozen literal; version 2's ends in
// -v2 in its place.
export function syntheticCompositionIdentifier(
  product: SyntheticProduct,
  version: SyntheticVersion,
): string {
  return version === 1
    ? product.compositionIdentifier
    : `${product.compositionIdentifier.replace(/-v1$/, "")}-v${version}`;
}

const PARACETAMOL: SyntheticProduct = {
  id: "synthetic-paracetamol",
  productName: "Synthetic Paracetamol 500 mg tablets",
  substanceName: "Paracetamol",
  documentTitle: "Synthetic Paracetamol 500 mg tablets SmPC",
  strengthMg: 500,
  organizationId: "synthetic-pharma",
  organizationName: "Synthetic Pharma Ltd",
  organizationIdentifier: "SYN-ORG-0001",
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
  organizationIdentifier: "SYN-ORG-0001",
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
  organizationIdentifier: "SYN-ORG-0001",
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

// Not part of the demonstration. The worker's `fixture` run source and the official validation
// gate both use this product and nothing else uses it, so the smoke run that follows every deploy
// proves the pipeline end to end without writing a version over a label the demonstration is
// about. Before it existed, a deploy republished the default product from the fixture path, which
// meant the store could not be both continuously proven and demonstration-ready at the same time.
//
// It is otherwise an ordinary synthetic product built by the same code as the others, so
// validating it is representative of validating any of them.
const SMOKETEST: SyntheticProduct = {
  id: "synthetic-smoketest",
  productName: "Synthetic Smoketest 2.5 mg tablets",
  substanceName: "Smoketestium",
  documentTitle: "Synthetic Smoketest 2.5 mg tablets SmPC",
  // A decimal, so that a non-integer number travels through the pipeline, both languages'
  // canonical JSON and the official validator (audit C-10); until 2026-09-28 every synthetic
  // strength was an integer, and Zone A refused to hash any other number.
  strengthMg: 2.5,
  organizationId: "synthetic-pharma",
  organizationName: "Synthetic Pharma Ltd",
  organizationIdentifier: "SYN-ORG-0001",
  compositionId: "synthetic-smoketest-smpc",
  compositionIdentifier: "synthetic-smoketest-smpc-v1",
  bundleId: "synthetic-smoketest-type2-smpc",
  bundleIdentifier: "synthetic-smoketest-type2-smpc-v1",
  productIdentifier: "SYN-SMOK-001",
  authorizationId: "synthetic-smoketest-authorization",
  marketingAuthorizationNumber: "EU/SYN/0004",
  packageId: "synthetic-smoketest-package",
  itemId: "synthetic-smoketest-tablet",
  administrableId: "synthetic-smoketest-administrable",
  ingredientId: "synthetic-smoketest-active-ingredient",
  substanceId: "synthetic-smoketest-substance",
  substanceIdentifier: "SYN-SMOKETESTIUM",
  sourceFilenameStem: "synthetic-smoketest-smpc",
  namedInNarrative: true,
  additionalSentences: {},
  submissions: {
    1: {
      submissionId: "5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b",
      extractionRunId: "6f7a8b9c-0d1e-4f2a-9b3c-4d5e6f7a8b9c",
      createdAt: VERSION_ONE_APPROVAL,
      approvedAt: VERSION_ONE_APPROVAL,
    },
    2: {
      submissionId: "7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d",
      extractionRunId: "8b9c0d1e-2f3a-4b4c-9d5e-6f7a8b9c0d1e",
      createdAt: VERSION_TWO_APPROVAL,
      approvedAt: VERSION_TWO_APPROVAL,
    },
  },
};

// The package leaflet's product (docs/design/pl-structure.md, ADR 0006 owner decision 8): its
// Type 2 record is built with the leaflet's mapping (fhir/mappings/cap-pl-en.json), as the others'
// are with the SmPC's, and runs through the crosswalk and the official validator
// (scripts/ci/emit-validation-set.ts). It is neither a demonstration label nor the smoke product,
// so it is not in SYNTHETIC_PRODUCTS, whose products the SmPC tests iterate and the demonstration
// seeds.
const EXAMPLINE: SyntheticProduct = {
  id: "synthetic-exampline",
  productName: "Synthetic Exampline 10 mg film-coated tablets",
  substanceName: "Exampline",
  documentTitle: "Synthetic Exampline 10 mg film-coated tablets package leaflet",
  strengthMg: 10,
  organizationId: "synthetic-pharma",
  organizationName: "Synthetic Pharma Ltd",
  organizationIdentifier: "SYN-ORG-0001",
  compositionId: "synthetic-exampline-pl",
  compositionIdentifier: "synthetic-exampline-pl-v1",
  bundleId: "synthetic-exampline-type2-pl",
  bundleIdentifier: "synthetic-exampline-type2-pl-v1",
  productIdentifier: "SYN-EXAM-010",
  authorizationId: "synthetic-exampline-authorization",
  marketingAuthorizationNumber: "EU/SYN/0005",
  euAuthorisationNumber: "EU/1/24/9999/001",
  packageId: "synthetic-exampline-package",
  itemId: "synthetic-exampline-tablet",
  administrableId: "synthetic-exampline-administrable",
  ingredientId: "synthetic-exampline-active-ingredient",
  substanceId: "synthetic-exampline-substance",
  substanceIdentifier: "SYN-EXAMPLINE",
  sourceFilenameStem: "synthetic-exampline-pl",
  namedInNarrative: true,
  additionalSentences: {},
  submissions: {
    1: {
      submissionId: "9c0d1e2f-3a4b-4c5d-8e6f-7a8b9c0d1e2f",
      extractionRunId: "0d1e2f3a-4b5c-4d6e-9f7a-8b9c0d1e2f3a",
      createdAt: VERSION_ONE_APPROVAL,
      approvedAt: VERSION_ONE_APPROVAL,
    },
    2: {
      submissionId: "1e2f3a4b-5c6d-4e7f-8a8b-9c0d1e2f3a4b",
      extractionRunId: "2f3a4b5c-6d7e-4f8a-9b9c-0d1e2f3a4b5c",
      createdAt: VERSION_TWO_APPROVAL,
      approvedAt: VERSION_TWO_APPROVAL,
    },
  },
};

export const LEAFLET_PRODUCT_ID: SyntheticProductId = EXAMPLINE.id;

// Declaration order is demonstration order: the default product first, the smoke product last
// because it is not demonstrated.
export const SYNTHETIC_PRODUCTS: readonly SyntheticProduct[] = [
  PARACETAMOL,
  DEMOXETINE,
  PLACEBOLOL,
  SMOKETEST,
];

// Every product the fixtures can build. Tests iterate this, so anything added here is covered.
export const SYNTHETIC_PRODUCT_IDS: readonly SyntheticProductId[] = SYNTHETIC_PRODUCTS.map(
  ({ id }) => id,
);

// The product the worker's `fixture` run source builds, and so the one the deploy's smoke run
// publishes and the official validation gate checks. Deliberately not a demonstration product.
export const SMOKE_PRODUCT_ID: SyntheticProductId = "synthetic-smoketest";

// The products the demonstration publishes through the document path. The smoke product is
// excluded: seeding it would put it in front of an audience, and republishing it on the next
// deploy is the behaviour this separation exists to make harmless.
export const DEMONSTRATION_PRODUCT_IDS: readonly SyntheticProductId[] =
  SYNTHETIC_PRODUCT_IDS.filter((id) => id !== SMOKE_PRODUCT_ID);

export const SYNTHETIC_VERSIONS: readonly SyntheticVersion[] = [1, 2];

export function syntheticProduct(
  id: SyntheticProductId = DEFAULT_SYNTHETIC_PRODUCT_ID,
): SyntheticProduct {
  const found = [...SYNTHETIC_PRODUCTS, EXAMPLINE].find((product) => product.id === id);
  if (found === undefined) throw new Error(`Unknown synthetic product ${id}`);
  return found;
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
