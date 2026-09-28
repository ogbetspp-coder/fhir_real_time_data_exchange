// Identifier systems and profile bases that more than one module names, declared once here so
// they cannot drift apart. `src/authority/` keeps its own copies because its code is hashed by the
// importer lock (ADR 0004's amendment); test/standards.test.ts holds those copies equal to these,
// and the next importer version should import them from here instead.

// EMA SPOR organisation identifiers: a holder's, a regulator's, and the publishing authority's.
export const SPOR_ORGANISATIONS = "https://spor.ema.europa.eu/v1/organisations/";

// Every HL7 Global ePI profile is `${GLOBAL_EPI_PROFILE_BASE}<ResourceType>-uv-epi`.
export const GLOBAL_EPI_PROFILE_BASE =
  "http://hl7.org/fhir/uv/emedicinal-product-info/StructureDefinition/";

export const GLOBAL_TYPE2_BUNDLE_PROFILE = `${GLOBAL_EPI_PROFILE_BASE}Bundle-uv-epi`;
