import { z } from "zod";

import { IsoDateTime } from "../contracts/common.js";

// The EMA's live shape, closed (docs/design/authority-import-contract.md, Appendix A). The EMA
// serves FHIR codes as integers of its own enumerations and places its product identity on
// List.subject under its own extension URLs; every other key, value type or value refuses.

const GUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const BUNDLE_REFERENCE = /^Bundle\/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const Guid = z.string().regex(GUID);

// Every non-narrative string the importer carries: no lone surrogate, control or format
// character (List.title becomes the product name).
const Plain = z
  .string()
  .min(1)
  .max(1_024)
  .refine((value) => !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(value), "a control or format character");

// FHIR's `dateTime` and `instant` (R5, "Primitive Types"): a time needs seconds and a zone. Each
// is also a day and time the calendar has, so the value the record carries (Composition.date, and
// Bundle.timestamp as the approval's authorityTimestamp) is one its own contract accepts (which
// has no leap second).
const YEAR = "(?:[0-9](?:[0-9](?:[0-9][1-9]|[1-9]0)|[1-9]00)|[1-9]000)";
const MONTH = "(?:0[1-9]|1[0-2])";
const DAY = "(?:0[1-9]|[12][0-9]|3[01])";
const TIME = "(?:[01][0-9]|2[0-3]):[0-5][0-9]:(?:[0-5][0-9]|60)(?:\\.[0-9]{1,9})?";
const ZONE = "(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))";
const DATE_TIME = new RegExp(`^${YEAR}(?:-${MONTH}(?:-${DAY}(?:T${TIME}${ZONE})?)?)?$`, "u");
const INSTANT = new RegExp(`^${YEAR}-${MONTH}-${DAY}T${TIME}${ZONE}$`, "u");

function onTheCalendar(value: string): boolean {
  if (value.length < 10) return true;
  return IsoDateTime.safeParse(value.length === 10 ? `${value}T00:00:00Z` : value).success;
}

const DateTime = Plain.refine(
  (value) => DATE_TIME.test(value) && onTheCalendar(value),
  "not a FHIR dateTime",
);
const Instant = Plain.refine(
  (value) => INSTANT.test(value) && onTheCalendar(value),
  "not a FHIR instant",
);

const Meta = z.strictObject({ versionId: Plain, lastUpdated: Plain.optional() });
const Narrative = z.strictObject({ status: Plain, div: z.string() });
const Coding = z.strictObject({ system: Plain, code: Plain, display: Plain.optional() });

export const EMA_DOCUMENT_IDENTIFIER_SYSTEM = "http://ema.europa.eu/fhir/epiDocument";
export const EMA_DOCUMENT_TYPE_SYSTEM = "https://spor.ema.europa.eu/v1/lists/100000155531/terms/";
export const EMA_SMPC_TYPE_CODE = "100000155532";
export const EMA_SECTION_SYSTEM = "https://spor.ema.europa.eu/v1/lists/200000029659/terms/";
export const EMA_EPI_ID_SYSTEM = "http://ema.europa.eu/fhir/epiId";
export const EMA_EXTENSION_BASE = "https://ema.europa.eu/fhir/extension/";
export const SPOR_ORGANISATIONS = "https://spor.ema.europa.eu/v1/organisations/";
export const EMA_PROCEDURE_SYSTEM = "http://ema.europa.eu/fhir/procedureIdentifierNumber";

export type EmaSection = {
  id?: string | undefined;
  title: string;
  code: { coding: [{ system: string; code: string; display?: string | undefined }] };
  text?: { status: string; div: string } | undefined;
  section?: EmaSection[] | undefined;
};

const Section: z.ZodType<EmaSection> = z.lazy(() =>
  z.strictObject({
    id: Plain.optional(),
    title: Plain,
    code: z.strictObject({
      coding: z.tuple([
        z.strictObject({
          system: z.literal(EMA_SECTION_SYSTEM),
          code: Plain,
          display: Plain.optional(),
        }),
      ]),
    }),
    text: Narrative.optional(),
    section: z.array(Section).optional(),
  }),
);

const ContainedBinary = z.strictObject({
  resourceType: z.literal("Binary"),
  id: Plain,
  contentType: Plain,
  data: z.string(),
});

const Composition = z.strictObject({
  resourceType: z.literal(0),
  // The EMA's integer for English; other languages are other integers (D5).
  language: z.literal(0),
  status: z.literal(0),
  text: Narrative.optional(),
  extension: z
    .array(
      z.strictObject({
        url: z.enum([
          "https://ema.europa.eu/fhir/extension/documentType",
          "http://ema.europa.eu/fhir/extension/imageReference",
        ]),
        valueReference: z.record(z.string(), z.unknown()),
      }),
    )
    .optional(),
  type: z.strictObject({
    coding: z.tuple([
      z.strictObject({
        system: z.literal(EMA_DOCUMENT_TYPE_SYSTEM),
        code: z.literal(EMA_SMPC_TYPE_CODE),
        display: Plain,
      }),
    ]),
  }),
  date: DateTime,
  author: z
    .array(z.strictObject({ identifier: z.record(z.string(), z.unknown()) }))
    .max(1)
    .optional(),
  title: Plain,
  contained: z.array(ContainedBinary).optional(),
  section: z.array(Section).min(1),
});

export const EmaDocumentSchema = z.strictObject({
  resourceType: z.literal("Bundle"),
  id: Guid,
  meta: Meta.optional(),
  identifier: z.strictObject({ system: z.literal(EMA_DOCUMENT_IDENTIFIER_SYSTEM), value: Guid }),
  type: z.literal("document"),
  timestamp: Instant,
  entry: z.tuple([z.strictObject({ fullUrl: Plain.optional(), resource: Composition })]),
});

const OrganisationCoding = z.strictObject({
  system: z.literal(SPOR_ORGANISATIONS),
  code: Plain,
  display: Plain,
});

const SubjectExtension = z.discriminatedUnion("url", [
  z.strictObject({
    url: z.literal(`${EMA_EXTENSION_BASE}procedureNumber`),
    valueIdentifier: z.strictObject({ system: z.literal(EMA_PROCEDURE_SYSTEM), value: Plain }),
  }),
  z.strictObject({
    url: z.literal(`${EMA_EXTENSION_BASE}marketingAuthorisationHolder`),
    valueCoding: OrganisationCoding,
  }),
  z.strictObject({
    url: z.literal(`${EMA_EXTENSION_BASE}regulatoryAgency`),
    valueCoding: OrganisationCoding,
  }),
  z.strictObject({ url: z.literal(`${EMA_EXTENSION_BASE}domain`), valueCoding: Coding }),
  z.strictObject({ url: z.literal(`${EMA_EXTENSION_BASE}authorisationType`), valueString: Plain }),
  z.strictObject({ url: z.literal(`${EMA_EXTENSION_BASE}versionNumber`), valueString: Plain }),
]);

export const EmaListSchema = z.strictObject({
  resourceType: z.literal("List"),
  id: Guid,
  meta: z.strictObject({ versionId: Plain, lastUpdated: Plain.optional() }),
  text: Narrative.optional(),
  identifier: z.tuple([z.strictObject({ system: z.literal(EMA_EPI_ID_SYSTEM), value: Plain })]),
  status: z.literal("current"),
  mode: z.literal("working").optional(),
  title: Plain,
  code: z.strictObject({ coding: z.tuple([z.strictObject({ display: Plain })]) }).optional(),
  subject: z.strictObject({
    display: Plain.optional(),
    extension: z.array(SubjectExtension).min(1),
  }),
  entry: z
    .array(
      z.strictObject({
        item: z.strictObject({
          reference: z.string().regex(BUNDLE_REFERENCE),
          display: Plain,
        }),
      }),
    )
    .min(1),
});

export type EmaDocument = z.infer<typeof EmaDocumentSchema>;
export type EmaList = z.infer<typeof EmaListSchema>;

// What the List states about the product, each value exactly once where it is required (D5).
export type ListIdentity = {
  epiId: string;
  versionNumber: string;
  metaVersionId: string;
  title: string;
  procedureNumber: string;
  holder: { code: string; display: string };
  agency: { code: string; display: string };
};

export function listIdentity(list: EmaList): ListIdentity | string {
  const urls = list.subject.extension.map(({ url }) => url);
  if (new Set(urls).size !== urls.length) return "list-extension-repeated";
  let procedureNumber: string | undefined;
  let versionNumber: string | undefined;
  let holder: ListIdentity["holder"] | undefined;
  let agency: ListIdentity["agency"] | undefined;
  for (const extension of list.subject.extension) {
    switch (extension.url) {
      case `${EMA_EXTENSION_BASE}procedureNumber`:
        procedureNumber = extension.valueIdentifier.value;
        break;
      case `${EMA_EXTENSION_BASE}marketingAuthorisationHolder`:
        holder = { code: extension.valueCoding.code, display: extension.valueCoding.display };
        break;
      case `${EMA_EXTENSION_BASE}regulatoryAgency`:
        agency = { code: extension.valueCoding.code, display: extension.valueCoding.display };
        break;
      case `${EMA_EXTENSION_BASE}versionNumber`:
        versionNumber = extension.valueString;
        break;
      default:
        break;
    }
  }
  if (procedureNumber === undefined) return "list-procedure-missing";
  if (holder === undefined) return "list-holder-missing";
  if (agency === undefined) return "list-agency-missing";
  if (versionNumber === undefined) return "list-version-missing";
  return {
    epiId: list.identifier[0].value,
    versionNumber,
    metaVersionId: list.meta.versionId,
    title: list.title,
    procedureNumber,
    holder,
    agency,
  };
}
