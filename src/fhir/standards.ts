// Identifier systems and profile bases that more than one module names, declared once here so
// they cannot drift apart. `src/authority/` keeps its own copies because its code is hashed by the
// importer lock (ADR 0004's amendment); test/standards.test.ts holds those copies equal to these,
// and the next importer version should import them from here instead.

// EMA SPOR organisation identifiers: a holder's, a regulator's, and the publishing authority's.
export const SPOR_ORGANISATIONS = "https://spor.ema.europa.eu/v1/organisations/";

// EU marketing authorisation numbers in the canonical record (docs/design/version-identity.md,
// ADR 0006 decision 5). An authorisation number is EU/1/YY/NNN/PPP, one per RegulatedAuthorization;
// a product number is EU/1/YY/NNN, the number without its presentation, on the
// MedicinalProductDefinition. The patterns are zone_a.product's (YY two digits, NNN three or four,
// PPP three); a run as section 8 may write one (EU/1/YY/NNN/PPP-PPP) names several numbers and is
// not one. The preflight (src/fhir/preflight.ts) and the package's invariants
// (scripts/fhir/generate-artifacts.ts) both read these.
export const EU_AUTHORISATION_NUMBER_SYSTEM =
  "https://khs.dev/fhir/identifier/eu-authorisation-number";
export const EU_PRODUCT_NUMBER_SYSTEM = "https://khs.dev/fhir/identifier/eu-product-number";
export const EU_AUTHORISATION_NUMBER_PATTERN = "^EU/1/[0-9]{2}/[0-9]{3,4}/[0-9]{3}$";
export const EU_PRODUCT_NUMBER_PATTERN = "^EU/1/[0-9]{2}/[0-9]{3,4}$";

// The canonical URL of the repository's own FHIR definitions (dev.khs.fhir.epi, fhir/generated/).
// The Cloud Healthcare API store imports the four HL7 and EMA packages only, so a profile under it
// is checked by the official validator and never sent to the store's $validate.
export const KHS_CANONICAL = "https://khs.dev/fhir";

// The package's profile of a document Bundle that carries the EU number invariants above
// (khs-eu-1 to khs-eu-4). Every rule holds vacuously for a graph with no EU number.
export const EU_PRODUCT_IDENTITY_PROFILE = `${KHS_CANONICAL}/StructureDefinition/eu-product-identity`;

// The EMA List's EU number (EUePI 1.0.0's ext-epi-eu-number, valueIdentifier). The package defines
// no identifier system for it; this is the one the EMA's pinned commented sample writes
// (fhir/standards.lock.json, "EMA EPI-23-1022 English commented sample"), a non-normative source.
export const EMA_EU_NUMBER_SYSTEM = "http://ema.europa.eu/fhir/euNumber/";

// Every HL7 Global ePI profile is `${GLOBAL_EPI_PROFILE_BASE}<ResourceType>-uv-epi`.
export const GLOBAL_EPI_PROFILE_BASE =
  "http://hl7.org/fhir/uv/emedicinal-product-info/StructureDefinition/";

export const GLOBAL_TYPE2_BUNDLE_PROFILE = `${GLOBAL_EPI_PROFILE_BASE}Bundle-uv-epi`;

// The EMA QRD template version the transform stamps on the EMA Composition and the run manifest
// records: one constant, so the two cannot disagree (audit B07, S-4).
export const QRD_TEMPLATE_VERSION = "10.4";

// The package ids whose pinned versions (fhir/standards.lock.json) the run manifest names.
export const GLOBAL_EPI_PACKAGE_ID = "hl7.fhir.uv.emedicinal-product-info";
export const EMA_EPI_PACKAGE_ID = "EUePI";
