import { z } from "zod";

// Shared primitives for every Zone A / Zone B contract. Each named primitive carries a
// `.meta({ id })` so it becomes a reusable `$defs` entry in the generated JSON Schema and a
// stable model name for code generated from it (ADR 0002).

export const Sha256Hex = z
  .string()
  .regex(/^[0-9a-f]{64}$/)
  .meta({ id: "Sha256Hex", description: "Lower-case hexadecimal SHA-256 digest." });

export const Uuid = z.uuid().meta({ id: "Uuid" });

export const IsoDateTime = z.iso
  .datetime({ offset: true })
  .meta({ id: "IsoDateTime", description: "RFC 3339 timestamp with Z or a numeric offset." });

// URL-class strings are grammar-limited rather than `z.url()`: the WHATWG parser accepts
// spaces and opaque schemes, which would let a URL field carry a sentence.
const URL_TAIL = "[A-Za-z0-9._~:/?#@!$&'()*+,;=%|-]";

export const HttpUrl = z
  .string()
  .regex(new RegExp(`^https?://[A-Za-z0-9.-]{1,253}(?::\\d{1,5})?(?:[/?#]${URL_TAIL}*)?$`))
  .max(256)
  .meta({ id: "HttpUrl", description: "http(s) URL without whitespace; may carry |version." });

// Identifier namespaces and document fullUrls: an http(s) URL or a URN.
export const CanonicalUri = z
  .string()
  .regex(new RegExp(`^(?:https?://[A-Za-z0-9.-]{1,253}(?:[/?#]${URL_TAIL}*)?|urn:${URL_TAIL}+)$`))
  .max(256)
  .meta({ id: "CanonicalUri" });

// Opaque principal identifier for an approver (IdP subject, service account id); never an
// e-mail address and never prose.
export const PrincipalId = z
  .string()
  .regex(
    /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/,
    "approverId must be an opaque principal identifier, not an e-mail address",
  )
  .meta({ id: "PrincipalId" });

// Locator of an approval record in an external system (URL or record key), no whitespace and
// no characters outside the URL-safe set.
export const RecordRef = z
  .string()
  .regex(/^[A-Za-z0-9][A-Za-z0-9._:/#?=&%+-]{0,511}$/)
  .meta({ id: "RecordRef" });

// Identifier-class strings (tool names, versions, ids, codes). Token-limited so that no
// provenance field can carry prose into a manifest, ledger row, or Provenance resource.
export const Token = z
  .string()
  .regex(/^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$/)
  .meta({ id: "Token", description: "Identifier: letters, digits, and . _ : / @ + - only." });

// JSON path of a target field inside the Bundle, e.g. Composition.section[0].section[1].code.
export const TargetPath = z
  .string()
  .regex(/^[A-Za-z][A-Za-z0-9]*(?:\[\d+\])?(?:\.[A-Za-z][A-Za-z0-9]*(?:\[\d+\])?)*$/)
  .max(256)
  .meta({ id: "TargetPath" });

export const StorageUri = z
  .string()
  .regex(/^gs:\/\/[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]\/[A-Za-z0-9._/-]{1,256}$/)
  .max(512)
  .meta({ id: "StorageUri", description: "Cloud Storage object URI (gs://bucket/object)." });

export const SourceKey = z
  .string()
  .regex(/^[a-z0-9]+(?:\.[a-z0-9]+)*$/)
  .meta({
    id: "SourceKey",
    description:
      "Canonical SmPC section identifier from the mapping manifest, e.g. smpc.4.2.posology.",
  });

// FHIR id grammar (R5 primitive types). It admits `.` and `..`, which encoding leaves as they are
// and the URL parser resolves, so it is not safe as a URL path segment on its own: a value that is
// interpolated into a store URL is an `AddressableFhirId`.
export const FhirId = z
  .string()
  .regex(/^[A-Za-z0-9\-.]{1,64}$/)
  .meta({ id: "FhirId" });

// A FHIR id that is also a single URL path segment: the R5 grammar, beginning with a letter or a
// digit, so never `.` or `..`. Values are interpolated into store URLs, so they are constrained
// here rather than trusted to be harmless once encoded.
export const AddressableFhirId = z
  .string()
  .regex(/^[A-Za-z0-9][A-Za-z0-9\-.]{0,63}$/)
  .meta({
    id: "AddressableFhirId",
    description: "FHIR id that begins with a letter or digit, so a single URL path segment.",
  });

export const NonEmptyString = z.string().min(1).max(1024);

export const Count = z.number().int().nonnegative();

export const PositiveInt = z.number().int().positive();

// A FHIR package as the package registry and the HL7 validator name it: `id#version`.
export const PackageRef = z
  .string()
  .regex(/^[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)*#\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/)
  .max(256)
  .meta({ id: "PackageRef", description: "FHIR package id#version." });

export const NormalizationVersion = z
  .string()
  .regex(/^fidelity-norm\/\d+\.\d+\.\d+$/)
  .meta({
    id: "NormalizationVersion",
    description: "Version of docs/fidelity-normalization.md the hashes were computed under.",
  });
